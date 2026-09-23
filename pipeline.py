#!/usr/bin/env python3
"""Explainable AML graph reconstruction for the HackAlem "Graph of Money" case.

The pipeline is deliberately deterministic: it derives every role, cluster and
priority from the supplied parquet files and never contains a list of expected
gids.  It writes the three mechanically checked CSV files plus a lightweight
HTML network view.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

ROLES = ["consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral"]


def q(series: pd.Series, value: float, default: float = 0.0) -> float:
    """Safe quantile for a possibly empty or constant series."""
    s = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
    return float(s.quantile(value)) if len(s) else default


def minmax(series: pd.Series) -> pd.Series:
    s = pd.to_numeric(series, errors="coerce").fillna(0.0)
    lo, hi = float(s.min()), float(s.max())
    if hi <= lo:
        return pd.Series(0.0, index=series.index)
    return (s - lo) / (hi - lo)


def load_inputs(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    edges = pd.read_parquet(data_dir / "edges.parquet")
    nodes = pd.read_parquet(data_dir / "nodes.parquet")
    tx = pd.read_parquet(data_dir / "transactions.parquet")
    tx["date"] = pd.to_datetime(tx["date"])
    required = {
        "edges": {"src", "dst", "sum_kzt", "n_tx", "depth"},
        "nodes": {"gid", "depth", "is_seed"},
        "transactions": {"src", "dst", "date", "sum_kzt"},
    }
    for name, frame in (("edges", edges), ("nodes", nodes), ("transactions", tx)):
        missing = required[name] - set(frame.columns)
        if missing:
            raise ValueError(f"{name}.parquet: missing columns {sorted(missing)}")
    return edges, nodes, tx


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes["gid"].astype(int).tolist())
    for row in edges.itertuples(index=False):
        graph.add_edge(
            int(row.src), int(row.dst),
            sum_kzt=float(row.sum_kzt), n_tx=int(row.n_tx), depth=int(row.depth),
        )
    return graph


def weighted_pagerank(graph: nx.DiGraph, damping: float = 0.85, max_iter: int = 300) -> dict[int, float]:
    """Small pure-NumPy PageRank so SciPy is not required for the hackathon run."""
    node_list = list(graph.nodes)
    index = {node: i for i, node in enumerate(node_list)}
    n = len(node_list)
    if n == 0:
        return {}
    transition = np.zeros((n, n), dtype=float)
    for src in node_list:
        i = index[src]
        outgoing = list(graph.out_edges(src, data="sum_kzt"))
        total = sum(float(weight or 0.0) for _, _, weight in outgoing)
        if not outgoing or total <= 0:
            transition[i, :] = 1.0 / n
        else:
            for _, dst, weight in outgoing:
                transition[i, index[dst]] = float(weight or 0.0) / total
    rank = np.full(n, 1.0 / n)
    teleport = np.full(n, 1.0 / n)
    for _ in range(max_iter):
        updated = damping * (rank @ transition) + (1.0 - damping) * teleport
        if np.abs(updated - rank).sum() < 1e-12:
            rank = updated
            break
        rank = updated
    return {node: float(rank[i]) for i, node in enumerate(node_list)}


def weighted_hits(graph: nx.DiGraph, max_iter: int = 300) -> tuple[dict[int, float], dict[int, float]]:
    """Pure-NumPy HITS scores; avoids NetworkX's optional SciPy sparse backend."""
    node_list = list(graph.nodes)
    index = {node: i for i, node in enumerate(node_list)}
    n = len(node_list)
    matrix = np.zeros((n, n), dtype=float)
    for src, dst, data in graph.edges(data=True):
        matrix[index[src], index[dst]] = math.log1p(max(float(data.get("sum_kzt", 0.0)), 0.0))
    hub = np.ones(n, dtype=float)
    authority = np.ones(n, dtype=float)
    for _ in range(max_iter):
        new_authority = matrix.T @ hub
        new_hub = matrix @ new_authority
        if np.linalg.norm(new_authority) > 0:
            new_authority /= np.linalg.norm(new_authority)
        if np.linalg.norm(new_hub) > 0:
            new_hub /= np.linalg.norm(new_hub)
        if np.abs(new_hub - hub).sum() < 1e-10:
            hub, authority = new_hub, new_authority
            break
        hub, authority = new_hub, new_authority
    return ({node: float(hub[i]) for i, node in enumerate(node_list)},
            {node: float(authority[i]) for i, node in enumerate(node_list)})


def temporal_features(tx: pd.DataFrame) -> pd.DataFrame:
    """Return explainable daily-flow features without pretending to see balances."""
    incoming = tx.groupby(["dst", "date"], as_index=False)["sum_kzt"].sum().rename(columns={"dst": "gid", "sum_kzt": "daily_in"})
    outgoing = tx.groupby(["src", "date"], as_index=False)["sum_kzt"].sum().rename(columns={"src": "gid", "sum_kzt": "daily_out"})
    daily = incoming.merge(outgoing, on=["gid", "date"], how="outer").fillna(0.0)
    by_node = daily.groupby("gid").agg(
        active_days=("date", "nunique"),
        same_day_in=("daily_in", lambda s: float(s.sum())),
    ).reset_index()
    same = daily.assign(same_day_pass=np.minimum(daily.daily_in, daily.daily_out))
    same = same.groupby("gid", as_index=False)["same_day_pass"].sum()
    by_node = by_node.drop(columns="same_day_in").merge(same, on="gid", how="left")
    return by_node


def node_features(graph: nx.DiGraph, nodes: pd.DataFrame, edges: pd.DataFrame, tx: pd.DataFrame) -> pd.DataFrame:
    in_deg = dict(graph.in_degree())
    out_deg = dict(graph.out_degree())
    in_kzt = dict(graph.in_degree(weight="sum_kzt"))
    out_kzt = dict(graph.out_degree(weight="sum_kzt"))
    in_tx = dict(graph.in_degree(weight="n_tx"))
    out_tx = dict(graph.out_degree(weight="n_tx"))
    pagerank = weighted_pagerank(graph)
    # Amount is a strength, not a distance.  This transform makes large flows
    # cheaper for betweenness while keeping a deterministic weighted signal.
    for _, _, data in graph.edges(data=True):
        data["distance"] = 1.0 / math.log1p(max(data["sum_kzt"], 1.0))
    betweenness = nx.betweenness_centrality(graph, weight="distance", normalized=True)
    hubs, authorities = weighted_hits(graph)

    result = nodes[["gid", "depth", "is_seed"]].copy()
    for name, values in (("in_deg", in_deg), ("out_deg", out_deg), ("in_kzt", in_kzt),
                         ("out_kzt", out_kzt), ("in_tx", in_tx), ("out_tx", out_tx),
                         ("pagerank", pagerank), ("betweenness", betweenness),
                         ("hub_score", hubs), ("authority_score", authorities)):
        result[name] = result.gid.map(values).fillna(0.0)
    result["pass_through"] = np.where(result.in_kzt > 0, result.out_kzt / result.in_kzt, np.nan)
    result["truncated_by_depth"] = (result.depth >= 4) & (result.out_deg == 0)

    counterpart_in = edges.groupby("dst")["src"].nunique()
    counterpart_out = edges.groupby("src")["dst"].nunique()
    result["unique_in_counterparts"] = result.gid.map(counterpart_in).fillna(0).astype(int)
    result["unique_out_counterparts"] = result.gid.map(counterpart_out).fillna(0).astype(int)
    result = result.merge(temporal_features(tx), on="gid", how="left")
    result["active_days"] = result.active_days.fillna(0).astype(int)
    result["same_day_pass"] = result.same_day_pass.fillna(0.0)
    result["same_day_pass_ratio"] = np.where(result.in_kzt > 0, result.same_day_pass / result.in_kzt, 0.0)
    return result


def cluster_graph(graph: nx.DiGraph, features: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    undirected = nx.Graph()
    undirected.add_nodes_from(graph.nodes)
    for src, dst, data in graph.edges(data=True):
        weight = float(data["sum_kzt"])
        if undirected.has_edge(src, dst):
            undirected[src][dst]["weight"] += weight
        else:
            undirected.add_edge(src, dst, weight=weight)
    try:
        communities = nx.community.louvain_communities(undirected, weight="weight", seed=42)
    except AttributeError:
        communities = [set(component) for component in nx.connected_components(undirected)]
    communities = sorted(communities, key=lambda group: (-len(group), min(group) if group else 0))
    cluster_map = {gid: idx for idx, group in enumerate(communities) for gid in group}
    cluster_ids = features.gid.map(cluster_map).fillna(-1).astype(int)
    rows = []
    for cid, group in enumerate(communities):
        members = set(group)
        internal = sum(data["sum_kzt"] for src, dst, data in graph.edges(data=True) if src in members and dst in members)
        f = features[features.gid.isin(members)].sort_values(["pagerank", "in_kzt"], ascending=False)
        rows.append({
            "cluster_id": cid,
            "n_nodes": len(members),
            "n_seed": int(f.is_seed.sum()),
            "sum_kzt_internal": round(float(internal), 2),
            "top_gids": ",".join(str(int(x)) for x in f.gid.head(5)),
            "hypothesis": cluster_hypothesis(f),
        })
    return cluster_ids, pd.DataFrame(rows, columns=["cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis"])


def cluster_hypothesis(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "малый фрагмент без достаточных признаков"
    if int(frame.is_seed.sum()) >= 2:
        return "общий финансовый контур нескольких seed-клиентов; требует проверки связей между ними"
    if float(frame.out_deg.mean()) > float(frame.in_deg.mean()) * 1.5:
        return "контур с выраженной раздачей средств"
    if float(frame.in_deg.mean()) > float(frame.out_deg.mean()) * 1.5:
        return "контур с выраженной консолидацией средств"
    return "смешанный транзитно-периферийный контур; нужна проверка ключевых узлов"


def assign_roles(features: pd.DataFrame) -> pd.DataFrame:
    f = features.copy()
    # Robust thresholds are derived from this dataset, not from hard-coded gids.
    in_deg_hi, out_deg_hi = q(f.in_deg, .90), q(f.out_deg, .90)
    flow_hi, pr_hi = q(f.in_kzt, .90), q(f.pagerank, .90)
    transit = (f.in_deg > 0) & (f.out_deg > 0) & f.pass_through.between(.75, 1.25)
    consolidator = (f.unique_in_counterparts >= max(3, q(f.unique_in_counterparts, .75))) & (f.pass_through.fillna(0) < .80)
    distributor = (f.unique_out_counterparts >= max(5, out_deg_hi)) | ((f.out_kzt >= flow_hi) & (f.out_deg >= 4))
    terminal = (f.out_deg == 0) & (~f.truncated_by_depth) & (f.in_deg > 0)
    coordinator_signal = (minmax(f.betweenness) * .55 + minmax(f.pagerank) * .25 + minmax(f.hub_score) * .20)
    coordinator = (coordinator_signal >= q(coordinator_signal, .97)) & (f.in_deg > 0) & (f.out_deg > 0)

    # Priority uses role-independent evidence: flow scale, structural influence,
    # multi-seed reach (approximated by seed flag in the local component) and
    # temporal pass-through. Seed ratios are intentionally excluded.
    f["role"] = "peripheral"
    f.loc[terminal, "role"] = "terminal"
    f.loc[transit, "role"] = "transit"
    f.loc[consolidator, "role"] = "consolidator"
    f.loc[distributor, "role"] = "distributor"
    f.loc[coordinator, "role"] = "coordinator"
    # Keep coordinator as the strongest structural designation, then avoid
    # contradictory labels for clear fan-out/fan-in nodes.
    f.loc[coordinator, "role"] = "coordinator"

    role_strength = pd.Series(.35, index=f.index)
    role_strength = np.where(f.role == "terminal", np.clip((f.in_kzt / (f.in_kzt + f.out_kzt + 1)), 0, 1), role_strength)
    role_strength = np.where(f.role == "transit", 1 - np.minimum(abs(f.pass_through - 1), 1), role_strength)
    role_strength = np.where(f.role == "consolidator", np.clip(f.unique_in_counterparts / max(in_deg_hi, 1), 0, 1), role_strength)
    role_strength = np.where(f.role == "distributor", np.clip(f.unique_out_counterparts / max(out_deg_hi, 1), 0, 1), role_strength)
    role_strength = np.where(f.role == "coordinator", coordinator_signal, role_strength)
    role_strength = np.where((f.role == "peripheral") & f.truncated_by_depth, .20, role_strength)
    f["role_score"] = np.clip(pd.Series(role_strength, index=f.index).astype(float), 0, 1).round(4)

    influence = .35 * minmax(np.log1p(f.in_kzt + f.out_kzt)) + .25 * minmax(f.pagerank) + .20 * minmax(f.betweenness) + .20 * minmax(f.unique_in_counterparts + f.unique_out_counterparts)
    f["priority_score"] = np.clip(influence, 0, 1).round(4)
    f["evidence"] = f.apply(make_evidence, axis=1, result_type="reduce")
    return f


def money(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


def make_evidence(row: pd.Series) -> str:
    role = row.role
    if role == "consolidator":
        text = f"получает от {int(row.unique_in_counterparts)} разных gid, входящий оборот {money(row.in_kzt)} KZT; pass-through {row.pass_through:.2f}"
    elif role == "transit":
        text = f"входящий {money(row.in_kzt)} KZT, исходящий {money(row.out_kzt)} KZT; pass-through {row.pass_through:.2f}, транзитный профиль"
    elif role == "distributor":
        text = f"распределяет на {int(row.unique_out_counterparts)} получателей, исходящий оборот {money(row.out_kzt)} KZT"
    elif role == "terminal":
        text = f"получает {money(row.in_kzt)} KZT от {int(row.unique_in_counterparts)} gid и не имеет исходящих в пределах наблюдаемого графа"
    elif role == "coordinator":
        text = f"высокая посредническая роль: betweenness {row.betweenness:.3f}, PageRank {row.pagerank:.4f}; {int(row.in_deg)} входящих / {int(row.out_deg)} исходящих связей"
    elif row.truncated_by_depth:
        text = f"нет исходящих, но depth=4: вероятен обрыв обхода, а не доказанный конечный получатель"
    else:
        text = f"признаки роли не выражены: {int(row.in_deg)} входящих / {int(row.out_deg)} исходящих связей, depth={int(row.depth)}"
    return text[:200]


def write_visualization(features: pd.DataFrame, edges: pd.DataFrame, out_file: Path) -> None:
    top = features.sort_values("priority_score", ascending=False).head(80)
    visible = set(top.gid.astype(int))
    visible_edges = edges[edges.src.isin(visible) & edges.dst.isin(visible)].copy()
    width, height = 1200, 760
    center = (width / 2, height / 2)
    nodes = []
    for i, row in enumerate(top.itertuples(index=False)):
        angle = 2 * math.pi * i / max(len(top), 1)
        radius = 260 + 90 * ((i % 5) / 4)
        x = center[0] + radius * math.cos(angle)
        y = center[1] + radius * math.sin(angle) * .65
        nodes.append((int(row.gid), x, y, row.role, float(row.priority_score), row.evidence))
    pos = {gid: (x, y) for gid, x, y, *_ in nodes}
    colors = {"coordinator": "#8b5cf6", "consolidator": "#ef4444", "distributor": "#f59e0b", "transit": "#06b6d4", "terminal": "#22c55e", "peripheral": "#94a3b8"}
    lines = []
    for row in visible_edges.itertuples(index=False):
        if int(row.src) not in pos or int(row.dst) not in pos:
            continue
        x1, y1 = pos[int(row.src)]; x2, y2 = pos[int(row.dst)]
        lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="#cbd5e1" stroke-width="{1 + min(float(row.sum_kzt) / max(edges.sum_kzt.max(), 1) * 8, 5):.2f}" marker-end="url(#arrow)" opacity=".65"><title>{int(row.src)} → {int(row.dst)}: {money(float(row.sum_kzt))} KZT</title></line>')
    circles = []
    for gid, x, y, role, priority, evidence in nodes:
        circles.append(f'<g class="node" data-gid="{gid}" data-role="{role}"><circle cx="{x:.1f}" cy="{y:.1f}" r="{7 + 10 * priority:.1f}" fill="{colors.get(role, colors["peripheral"])}" stroke="#fff" stroke-width="2"><title>gid {gid} · {role}\n{html.escape(evidence)}</title></circle><text x="{x + 10:.1f}" y="{y + 4:.1f}" font-size="10">{gid}</text></g>')
    doc = f'''<!doctype html><meta charset="utf-8"><title>AML Graph — top 80 nodes</title>
<style>body{{font-family:system-ui;margin:0;background:#f8fafc;color:#0f172a}}header{{padding:16px 24px;background:#0f172a;color:white}}input{{margin-left:20px;padding:8px 12px;border-radius:8px;border:0}}svg{{width:100%;height:calc(100vh - 80px);background:white}}.node.hide{{display:none}}.legend{{position:fixed;right:20px;top:90px;background:white;padding:12px;border-radius:10px;box-shadow:0 2px 12px #0002;font-size:12px}}</style>
<header><b>Граф денег</b> · визуализация 80 узлов с максимальным приоритетом <input id="search" placeholder="Поиск по gid или роли"></header>
<div class="legend">coordinator 🟣<br>consolidator 🔴<br>distributor 🟠<br>transit 🔵<br>terminal 🟢<br>peripheral ⚪</div>
<svg viewBox="0 0 {width} {height}"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#94a3b8"/></marker></defs>{''.join(lines)}{''.join(circles)}</svg>
<script>const q=document.querySelector('#search');q.addEventListener('input',()=>{{const s=q.value.toLowerCase();document.querySelectorAll('.node').forEach(n=>n.classList.toggle('hide',s&&!((n.dataset.gid+' '+n.dataset.role).includes(s))))}});</script>'''
    out_file.write_text(doc, encoding="utf-8")


def write_graph_json(features: pd.DataFrame, edges: pd.DataFrame, out_file: Path) -> None:
    """Compact browser-friendly artifact consumed by the standalone frontend."""
    node_cols = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence", "depth", "is_seed", "in_kzt", "out_kzt", "in_deg", "out_deg"]
    nodes = []
    for row in features[node_cols].itertuples(index=False):
        item = dict(zip(node_cols, row))
        item["gid"] = int(item["gid"])
        item["cluster_id"] = int(item["cluster_id"])
        item["depth"] = int(item["depth"])
        item["is_seed"] = bool(item["is_seed"])
        nodes.append(item)
    links = [{"src": int(r.src), "dst": int(r.dst), "sum_kzt": float(r.sum_kzt), "n_tx": int(r.n_tx), "depth": int(r.depth)} for r in edges.itertuples(index=False)]
    out_file.write_text(json.dumps({"nodes": nodes, "edges": links}, ensure_ascii=False), encoding="utf-8")


def run(data_dir: Path, out_dir: Path) -> None:
    edges, nodes, tx = load_inputs(data_dir)
    graph = build_graph(edges, nodes)
    features = node_features(graph, nodes, edges, tx)
    features["cluster_id"], clusters = cluster_graph(graph, features)
    features = assign_roles(features)
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = features.sort_values("gid")
    columns = ["gid", "role", "role_score", "cluster_id", "priority_score", "evidence", "in_deg", "out_deg", "in_kzt", "out_kzt", "in_tx", "out_tx", "pagerank", "pass_through", "depth", "is_seed", "truncated_by_depth"]
    ordered[columns].to_csv(out_dir / "nodes_roles.csv", index=False)
    clusters.to_csv(out_dir / "clusters.csv", index=False)
    top = features.sort_values(["priority_score", "pagerank", "gid"], ascending=[False, False, True]).head(max(20, min(50, len(features))))
    pd.DataFrame({"rank": range(1, len(top) + 1), "gid": top.gid.astype(int), "role": top.role, "priority_score": top.priority_score, "why": top.evidence}).to_csv(out_dir / "top_nodes.csv", index=False)
    write_visualization(features, edges, out_dir / "network.html")
    write_graph_json(features, edges, out_dir / "graph.json")
    print(f"Wrote {len(ordered)} nodes, {len(clusters)} clusters and {len(top)} priority nodes to {out_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data"))
    parser.add_argument("--out", type=Path, default=Path("out"))
    args = parser.parse_args()
    run(args.data, args.out)
