#!/usr/bin/env python3
"""Explainable transaction-network analysis for the HackAlem AI AML case."""

from __future__ import annotations

import argparse
import json
import math
from collections import deque
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

ROLES = ("consolidator", "transit", "distributor", "terminal", "coordinator", "peripheral")
NODE_COLUMNS = ("gid", "role", "role_score", "cluster_id", "priority_score", "evidence")
CLUSTER_COLUMNS = ("cluster_id", "n_nodes", "n_seed", "sum_kzt_internal", "top_gids", "hypothesis")
TOP_COLUMNS = ("rank", "gid", "role", "priority_score", "why")
PRIORITY_WEIGHTS = {"in_deg": 0.18, "out_deg": 0.18, "authority_score": 0.20,
                    "hub_score": 0.16, "pagerank": 0.16, "seed_sources": 0.12}
ROLE_THRESHOLDS = {"collector_sources": 3, "distributor_targets": 5,
                   "transit_min": 0.70, "transit_max": 1.30, "quick_share": 0.35}


def load_data(data_dir: Path):
    def read_parquet(path: Path) -> pd.DataFrame:
        try:
            return pd.read_parquet(path, engine="pyarrow")
        except (ImportError, OSError):
            # Some managed Python distributions ship without the native Arrow DLL runtime.
            # Use fastparquet when present; a healthy pyarrow remains the normal path.
            try:
                return pd.read_parquet(path, engine="fastparquet")
            except ImportError as exc:
                raise RuntimeError("Установите pyarrow или fastparquet из requirements.txt") from exc

    edges = read_parquet(data_dir / "edges.parquet")
    nodes = read_parquet(data_dir / "nodes.parquet")
    tx = read_parquet(data_dir / "transactions.parquet")
    tx["date"] = pd.to_datetime(tx["date"], errors="raise")
    required = {
        "edges": (edges, {"src", "dst", "sum_kzt", "n_tx", "depth"}),
        "nodes": (nodes, {"gid", "depth", "is_seed"}),
        "transactions": (tx, {"src", "dst", "date", "sum_kzt"}),
    }
    for name, (frame, columns) in required.items():
        missing = columns - set(frame.columns)
        if missing:
            raise ValueError(f"{name}: отсутствуют колонки {sorted(missing)}")
    return edges, nodes, tx


def validate_data(edges: pd.DataFrame, nodes: pd.DataFrame, tx: pd.DataFrame) -> None:
    if nodes.empty:
        raise ValueError("Список клиентов пуст")
    for frame in (nodes, edges, tx):
        if frame.isna().any().any():
            raise ValueError("Обнаружены пропуски во входных данных")
        for col in ("gid", "src", "dst", "depth", "n_tx"):
            if col in frame and not pd.api.types.is_integer_dtype(frame[col]):
                raise ValueError(f"{col}: ожидается целочисленный тип без потери точности")
    if not pd.api.types.is_bool_dtype(nodes.is_seed) or not nodes.depth.between(0, 4).all():
        raise ValueError("Некорректные is_seed или depth")
    if not np.isfinite(tx.sum_kzt).all() or (tx.sum_kzt <= 0).any() or (edges.n_tx <= 0).any():
        raise ValueError("Некорректные суммы транзакций или n_tx")
    if nodes.gid.duplicated().any():
        raise ValueError("nodes.parquet содержит повторяющиеся gid")
    if edges[["src", "dst"]].duplicated().any():
        raise ValueError("edges.parquet должен содержать одну строку на пару src→dst")
    if not np.isfinite(edges.sum_kzt).all() or (edges.sum_kzt < 0).any():
        raise ValueError("В edges.parquet обнаружены некорректные суммы")
    tx_agg = tx.groupby(["src", "dst"], as_index=False).agg(
        tx_sum=("sum_kzt", "sum"), tx_count=("sum_kzt", "size")
    )
    check = edges.merge(tx_agg, on=["src", "dst"], how="outer", indicator=True)
    if not check._merge.eq("both").all():
        raise ValueError("Пары рёбер и транзакций не совпадают")
    if not np.allclose(check.sum_kzt, check.tx_sum, rtol=0, atol=0.01):
        raise ValueError("Суммы рёбер не совпадают с суммами транзакций")
    if not (check.n_tx.astype(int) == check.tx_count.astype(int)).all():
        raise ValueError("n_tx не совпадает с числом транзакций для пары")
    node_ids = set(nodes.gid)
    edge_ids = set(edges.src) | set(edges.dst)
    if not edge_ids.issubset(node_ids):
        raise ValueError(f"В рёбрах есть gid, отсутствующие в nodes: {len(edge_ids - node_ids)}")


def build_graph(edges: pd.DataFrame, nodes: pd.DataFrame) -> nx.DiGraph:
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes.gid.tolist())  # preserve isolated seed clients
    graph.add_weighted_edges_from(
        ((int(r.src), int(r.dst), float(r.sum_kzt)) for r in edges.itertuples(index=False)),
        weight="sum_kzt",
    )
    for r in edges.itertuples(index=False):
        graph[r.src][r.dst]["n_tx"] = int(r.n_tx)
        graph[r.src][r.dst]["depth"] = int(r.depth)
    return graph


def percentile(values: pd.Series) -> pd.Series:
    """Stable 0..1 rank scale, with ties sharing their average rank."""
    numeric = pd.to_numeric(values, errors="coerce").fillna(0.0)
    if len(numeric) <= 1 or numeric.nunique() <= 1:
        return pd.Series(0.0, index=values.index)
    return (numeric.rank(method="average", pct=True) - 1.0 / len(numeric)).clip(0, 1)


def seed_reach_counts(graph: nx.DiGraph, nodes: pd.DataFrame) -> dict[int, int]:
    counts = {int(gid): 0 for gid in nodes.gid}
    for seed in nodes.loc[nodes.is_seed, "gid"]:
        for reached in nx.descendants(graph, seed):
            counts[int(reached)] = counts.get(int(reached), 0) + 1
    return counts


def weighted_pagerank(graph: nx.DiGraph, alpha: float = 0.85, max_iter: int = 250,
                      tolerance: float = 1e-10) -> dict[int, float]:
    """Weighted PageRank using adjacency lists; does not require SciPy."""
    ids = list(graph.nodes)
    if not ids:
        return {}
    size = len(ids)
    rank = {gid: 1.0 / size for gid in ids}
    outgoing = {}
    for src in ids:
        rows = [(dst, float(attrs.get("sum_kzt", 1.0))) for dst, attrs in graph[src].items()]
        total = sum(weight for _, weight in rows)
        outgoing[src] = ([(dst, weight / total) for dst, weight in rows] if total > 0 else [])
    for _ in range(max_iter):
        dangling = sum(rank[gid] for gid in ids if not outgoing[gid]) / size
        nxt = {gid: (1.0 - alpha) / size + alpha * dangling for gid in ids}
        for src, links in outgoing.items():
            for dst, share in links:
                nxt[dst] += alpha * rank[src] * share
        error = sum(abs(nxt[gid] - rank[gid]) for gid in ids)
        rank = nxt
        if error < tolerance:
            break
    return rank


def weighted_hits(graph: nx.DiGraph, max_iter: int = 200,
                  tolerance: float = 1e-10) -> tuple[dict[int, float], dict[int, float]]:
    """Weighted HITS power iteration on the edge list; no sparse-matrix dependency."""
    ids = list(graph.nodes)
    hubs = {gid: 1.0 for gid in ids}
    authorities = {gid: 1.0 for gid in ids}
    links = [(src, dst, math.sqrt(max(float(attrs.get("sum_kzt", 1.0)), 0.0)))
             for src, dst, attrs in graph.edges(data=True)]

    def normalise(values):
        norm = math.sqrt(sum(value * value for value in values.values()))
        return ({gid: value / norm for gid, value in values.items()} if norm else
                {gid: 0.0 for gid in ids})

    for _ in range(max_iter):
        new_auth = {gid: 0.0 for gid in ids}
        for src, dst, weight in links:
            new_auth[dst] += hubs[src] * weight
        new_auth = normalise(new_auth)
        new_hub = {gid: 0.0 for gid in ids}
        for src, dst, weight in links:
            new_hub[src] += new_auth[dst] * weight
        new_hub = normalise(new_hub)
        error = sum(abs(new_auth[g] - authorities[g]) + abs(new_hub[g] - hubs[g]) for g in ids)
        authorities, hubs = new_auth, new_hub
        if error < tolerance:
            break
    # Ranking features use scale-insensitive percentiles; normalise to probability-like sums.
    ah = sum(authorities.values()) or 1.0
    hh = sum(hubs.values()) or 1.0
    return ({gid: value / hh for gid, value in hubs.items()},
            {gid: value / ah for gid, value in authorities.items()})


def calculate_features(graph: nx.DiGraph, nodes: pd.DataFrame, edges: pd.DataFrame,
                       tx: pd.DataFrame) -> pd.DataFrame:
    ids = nodes.gid.tolist()
    incoming = {gid: [] for gid in ids}
    outgoing = {gid: [] for gid in ids}
    for r in edges.itertuples(index=False):
        outgoing[int(r.src)].append((int(r.dst), float(r.sum_kzt), int(r.n_tx)))
        incoming[int(r.dst)].append((int(r.src), float(r.sum_kzt), int(r.n_tx)))

    frame = nodes[["gid", "depth", "is_seed"]].copy()
    frame["in_deg"] = frame.gid.map(lambda g: len(incoming[int(g)]))
    frame["out_deg"] = frame.gid.map(lambda g: len(outgoing[int(g)]))
    frame["in_kzt"] = frame.gid.map(lambda g: sum(x[1] for x in incoming[int(g)]))
    frame["out_kzt"] = frame.gid.map(lambda g: sum(x[1] for x in outgoing[int(g)]))
    frame["in_tx"] = frame.gid.map(lambda g: sum(x[2] for x in incoming[int(g)]))
    frame["out_tx"] = frame.gid.map(lambda g: sum(x[2] for x in outgoing[int(g)]))
    frame["truncated_by_depth"] = (frame.depth == 4) & (frame.out_deg == 0)
    frame["pass_through"] = np.where(frame.in_kzt > 0, frame.out_kzt / frame.in_kzt.replace(0, np.nan), np.nan)

    # PageRank and HITS use edge weights; all listed nodes, including isolates, remain in outputs.
    frame["pagerank"] = frame.gid.map(weighted_pagerank(graph)).fillna(0.0)
    hubs, authorities = weighted_hits(graph)
    frame["hub_score"] = frame.gid.map(hubs).fillna(0.0)
    frame["authority_score"] = frame.gid.map(authorities).fillna(0.0)
    frame["seed_sources"] = frame.gid.map(seed_reach_counts(graph, nodes)).fillna(0).astype(int)

    # Greedily match each outgoing transaction to previously received money no older than 2 days.
    # The dataset contains dates rather than timestamps, so incoming rows on a date are considered first.
    events = {int(g): [] for g in ids}
    for r in tx.itertuples(index=False):
        events[int(r.dst)].append((r.date, 0, float(r.sum_kzt)))
        events[int(r.src)].append((r.date, 1, float(r.sum_kzt)))
    quick_by_node = {int(g): 0.0 for g in ids}
    for gid, node_events in events.items():
        available = deque()
        for day, direction, amount in sorted(node_events, key=lambda event: (event[0], event[1])):
            while available and (day - available[0][0]).days > 2:
                available.popleft()
            if direction == 0:
                available.append([day, amount])
                continue
            remaining = amount
            while remaining > 0.01 and available:
                matched = min(remaining, available[0][1])
                quick_by_node[gid] += matched
                remaining -= matched
                available[0][1] -= matched
                if available[0][1] <= 0.01:
                    available.popleft()
    frame["quick_turnover"] = frame.gid.map(quick_by_node).fillna(0.0)
    frame["quick_turnover_share"] = np.where(frame.out_kzt > 0, frame.quick_turnover / frame.out_kzt, 0.0).clip(0, 1)
    return frame


def cluster_graph(graph: nx.DiGraph, edges: pd.DataFrame, nodes: pd.DataFrame):
    undirected = nx.Graph()
    undirected.add_nodes_from(graph.nodes)
    for r in edges.itertuples(index=False):
        a, b = int(r.src), int(r.dst)
        weight = float(r.sum_kzt)
        if undirected.has_edge(a, b):
            undirected[a][b]["weight"] += weight
        else:
            undirected.add_edge(a, b, weight=weight)
    communities = (nx.community.louvain_communities(undirected, weight="weight", seed=42, resolution=1.0)
                   if undirected.number_of_edges() else [{gid} for gid in undirected])
    communities = sorted(communities, key=lambda group: min(group))
    membership = {int(gid): idx for idx, group in enumerate(communities) for gid in group}
    seed_ids = set(nodes.loc[nodes.is_seed, "gid"].astype(int))
    summaries = []
    for cid, group in enumerate(communities):
        subset = nodes[nodes.gid.isin(group)]
        internal = edges[edges.src.isin(group) & edges.dst.isin(group)]
        ranking = sorted(group, key=lambda gid: (graph.in_degree(gid, weight="sum_kzt") +
                                                  graph.out_degree(gid, weight="sum_kzt"), -int(gid)), reverse=True)
        top_gids = ",".join(str(g) for g in ranking[:8])
        totals_in = sum(float(graph.in_degree(g, weight="sum_kzt")) for g in group)
        totals_out = sum(float(graph.out_degree(g, weight="sum_kzt")) for g in group)
        seed_count = len(set(group) & seed_ids)
        if seed_count:
            hypothesis = f"Кластер содержит {seed_count} seed; проверить внутренние пути от начальных узлов и точки сбора/распределения."
        elif totals_in > totals_out * 1.15:
            hypothesis = "Внутри выборки входящий оборот выше исходящего; возможная зона удержания, с учётом неполноты графа."
        elif totals_out > totals_in * 1.15:
            hypothesis = "Исходящий оборот выше входящего; возможная зона распределения или влияние границы выборки."
        else:
            hypothesis = "Смешанный поток; назначение кластера по доступным транзакциям однозначно не определяется."
        summaries.append({"cluster_id": cid, "n_nodes": len(subset), "n_seed": seed_count,
                          "sum_kzt_internal": float(internal.sum_kzt.sum()), "top_gids": top_gids,
                          "hypothesis": hypothesis})
    return membership, pd.DataFrame(summaries, columns=CLUSTER_COLUMNS), communities


def assign_roles(features: pd.DataFrame, membership: dict[int, int]):
    f = features.copy()
    f["cluster_id"] = f.gid.map(membership).astype(int)
    for name, weight in PRIORITY_WEIGHTS.items():
        f["contribution_" + name] = weight * percentile(f[name])

    rows = []
    for r in f.itertuples(index=False):
        is_seed = bool(r.is_seed)
        boundary = bool(r.truncated_by_depth)
        ratio = float(r.pass_through) if pd.notna(r.pass_through) else None
        reasons = []
        # Conservative, mutually exclusive primary role rules. Seed inflow ratio is never used.
        if r.seed_sources >= 2 and r.in_deg >= 2 and r.out_deg >= 2 and not is_seed:
            role = "coordinator"
            score = min(0.96, 0.62 + 0.08 * min(int(r.seed_sources), 3) + 0.04 * min(int(r.in_deg), 5))
            reasons.append(f"достижим из {int(r.seed_sources)} seed")
            reasons.append(f"вход от {int(r.in_deg)} и выход к {int(r.out_deg)} узлам")
        elif r.in_deg >= ROLE_THRESHOLDS["collector_sources"] and r.out_deg >= 1 and not is_seed:
            role = "consolidator"
            score = min(0.94, 0.60 + 0.05 * min(int(r.in_deg) - 3, 5) + 0.04 * min(int(r.out_deg), 3))
            reasons.append(f"получает от {int(r.in_deg)} узлов")
            reasons.append(f"дальше переводит {float(r.out_kzt):,.0f} ₸")
        elif r.out_deg >= ROLE_THRESHOLDS["distributor_targets"]:
            role = "distributor"
            score = min(0.94, 0.60 + 0.04 * min(int(r.out_deg) - 4, 7) + 0.04 * min(int(r.in_deg), 4))
            reasons.append(f"переводит {int(r.out_deg)} получателям")
            reasons.append(f"исходящий оборот {float(r.out_kzt):,.0f} ₸")
        elif (r.in_deg > 0 and r.out_deg > 0 and not is_seed and
              ((ratio is not None and ROLE_THRESHOLDS["transit_min"] <= ratio <= ROLE_THRESHOLDS["transit_max"])
               or r.quick_turnover_share >= ROLE_THRESHOLDS["quick_share"])):
            role = "transit"
            ratio_evidence = (1.0 - min(abs(ratio - 1.0), 1.0)) if ratio is not None else 0.0
            score = min(0.92, 0.52 + 0.22 * ratio_evidence + 0.22 * float(r.quick_turnover_share))
            reasons.append(f"получает от {int(r.in_deg)} / отправляет {int(r.out_deg)} узлам")
            reasons.append(f"проход {ratio:.2f}" if ratio is not None else "неполное отношение оборотов")
            reasons.append(f"FIFO 0–2 дня {r.quick_turnover_share:.0%}; порядок внутри дня неизвестен")
        elif r.out_deg == 0 and r.in_deg > 0 and not boundary and not is_seed:
            role = "terminal"
            score = 0.72
            reasons.append(f"получает от {int(r.in_deg)} узлов")
            reasons.append("в выборке исходящих связей нет")
        else:
            role = "peripheral"
            score = 0.48 if boundary else 0.42
            if boundary:
                reasons.append("граница обхода depth=4; отсутствие исходящих не доказывает удержание")
            elif r.in_deg == 0 and r.out_deg == 0:
                reasons.append("in=0, out=0; нет наблюдаемых рёбер")
            else:
                reasons.append(f"in={int(r.in_deg)}, out={int(r.out_deg)}; критерии основных ролей не достигнуты")
        if is_seed:
            reasons.append("seed: входящие потоки неполны")
        evidence = "; ".join(reasons)[:200]
        role_rows = {"gid": int(r.gid), "role": role, "role_score": round(float(score), 4),
                     "cluster_id": int(r.cluster_id), "evidence": evidence}
        rows.append(role_rows)
    role_df = pd.DataFrame(rows)

    # Priority is a transparent percentile blend; it ranks review effort, not culpability.
    f["priority_factor"] = np.where(f.truncated_by_depth, 0.75, 1.0)
    f.loc[(f.in_deg == 0) & (f.out_deg == 0), "priority_factor"] = 0.0
    f["priority_score"] = f[["contribution_" + k for k in PRIORITY_WEIGHTS]].sum(axis=1) * f.priority_factor
    f["priority_score"] = f.priority_score.clip(0, 1)
    role_df = role_df.merge(f[["gid", "priority_score"]], on="gid", how="left")
    role_df["priority_score"] = role_df.priority_score.round(4)
    role_df = role_df[["gid", "role", "role_score", "cluster_id", "priority_score", "evidence"]]
    return role_df, f


def build_top_list(role_df: pd.DataFrame, features: pd.DataFrame, limit: int = 50) -> pd.DataFrame:
    ranked = role_df.sort_values(["priority_score", "gid"], ascending=[False, True]).head(max(20, limit)).copy()
    metric = features.set_index("gid")
    explanations = []
    for r in ranked.itertuples(index=False):
        f = metric.loc[r.gid]
        leaders = sorted(PRIORITY_WEIGHTS, key=lambda k: (-f["contribution_" + k], k))[:3]
        drivers = "; ".join(f"{k}={f[k]:.4g}, вклад {f['contribution_' + k]:.3f}" for k in leaders)
        why = (f"{r.role}; основные вклады: {drivers}; множитель {f.priority_factor:.2f}; "
               f"приоритет {r.priority_score:.4f}. Гипотеза для проверки, не вывод о виновности.")
        explanations.append(why[:400])
    return pd.DataFrame({"rank": range(1, len(ranked) + 1), "gid": ranked.gid.astype(int),
                         "role": ranked.role, "priority_score": ranked.priority_score,
                         "why": explanations}, columns=TOP_COLUMNS)


def make_viewer(role_df: pd.DataFrame, features: pd.DataFrame, edges: pd.DataFrame,
                communities: list[set[int]], template: Path, out_file: Path) -> None:
    center = {}
    cols = max(1, math.ceil(math.sqrt(len(communities))))
    rows = max(1, math.ceil(len(communities) / cols))
    for cid, group in enumerate(communities):
        cx = (cid % cols + 0.5) / cols
        cy = (cid // cols + 0.5) / rows
        n = len(group)
        radius = min(0.46 / cols, 0.42 / rows, max(0.012, 0.48 * math.sqrt(n / 110) / max(cols, rows)))
        ordered = sorted(group)
        for j, gid in enumerate(ordered):
            angle = j * 2.399963229728653
            rad = radius * math.sqrt((j + 0.5) / max(n, 1))
            center[int(gid)] = [cx + rad * math.cos(angle), cy + rad * math.sin(angle)]
    lookup = role_df.set_index("gid")
    metrics = features.set_index("gid")
    nodes = []
    for gid in role_df.gid.astype(int):
        r, m = lookup.loc[gid], metrics.loc[gid]
        nodes.append({"gid": str(gid), "role": r.role, "score": float(r.role_score),
                      "priority": float(r.priority_score), "cluster": int(r.cluster_id),
                      "evidence": r.evidence, "x": center[gid][0], "y": center[gid][1],
                      "in": int(m.in_deg), "out": int(m.out_deg),
                      "in_kzt": round(float(m.in_kzt), 2), "out_kzt": round(float(m.out_kzt), 2),
                      "seed": bool(m.is_seed), "cut": bool(m.truncated_by_depth)})
    links = [{"s": str(r.src), "t": str(r.dst), "v": round(float(r.sum_kzt), 2)}
             for r in edges.itertuples(index=False)]
    payload = json.dumps({"nodes": nodes, "edges": links}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    page = template.read_text(encoding="utf-8").replace("__GRAPH_DATA__", payload)
    out_file.write_text(page, encoding="utf-8")


def run(data_dir: Path, out_dir: Path, viewer_template: Path) -> None:
    edges, nodes, tx = load_data(data_dir)
    validate_data(edges, nodes, tx)
    nodes = nodes.sort_values("gid").reset_index(drop=True)
    edges = edges.sort_values(["src", "dst"]).reset_index(drop=True)
    graph = build_graph(edges, nodes)
    features = calculate_features(graph, nodes, edges, tx)
    membership, clusters, communities = cluster_graph(graph, edges, nodes)
    roles, features = assign_roles(features, membership)
    top = build_top_list(roles, features)
    out_dir.mkdir(parents=True, exist_ok=True)
    roles.to_csv(out_dir / "nodes_roles.csv", index=False, columns=NODE_COLUMNS, encoding="utf-8-sig")
    clusters.to_csv(out_dir / "clusters.csv", index=False, columns=CLUSTER_COLUMNS, encoding="utf-8-sig")
    top.to_csv(out_dir / "top_nodes.csv", index=False, columns=TOP_COLUMNS, encoding="utf-8-sig")
    features.to_csv(out_dir / "node_features.csv", index=False, encoding="utf-8-sig")
    records = []
    for row in roles.merge(features.drop(columns=["cluster_id", "priority_score"]), on="gid").to_dict("records"):
        row["gid"] = str(row["gid"])
        records.append({key: (None if pd.isna(value) else value) for key, value in row.items()})
    payload = {"schema_version": 1, "nodes": records,
               "edges": [{**row, "src": str(row["src"]), "dst": str(row["dst"])}
                         for row in edges.to_dict("records")],
               "limitations": ["Роли — эвристики, не доказательство виновности",
                               "FIFO: входящие внутри дня рассматриваются раньше исходящих",
                               "Граница depth=4 и входящие seed неполны"]}
    (out_dir / "analysis.json").write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    make_viewer(roles, features, edges, communities, viewer_template, out_dir / "index.html")
    print(f"Готово: {len(roles)} узлов, {len(edges)} рёбер, {len(clusters)} кластеров.")
    print(f"CSV и экран просмотра: {out_dir.resolve()}")
    print("Роли и приоритеты — объяснимые эвристики для проверки, не вывод о виновности.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Пайплайн кейса «Граф денег»")
    parser.add_argument("--data", type=Path, default=Path("../data"), help="папка с тремя parquet-файлами")
    parser.add_argument("--out", type=Path, default=Path("./out"), help="папка для CSV и index.html")
    parser.add_argument("--viewer", type=Path, default=Path(__file__).with_name("viewer.html"), help="шаблон экрана")
    args = parser.parse_args()
    run(args.data, args.out, args.viewer)


if __name__ == "__main__":
    main()
