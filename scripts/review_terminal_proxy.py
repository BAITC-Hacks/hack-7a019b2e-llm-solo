"""Independent audit of DATA-STUDY sections 5 and 7; offline, no scipy needed.

Run from repository root: .venv/Scripts/python.exe scripts/review_terminal_proxy.py
Only writes an ignored aggregate research artifact; no production behavior changes.
"""
from pathlib import Path
import json
import sys

import networkx as nx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moneygraph.io import load, input_hashes


def auc(y, score):
    """Exact pairwise ROC AUC via average ranks, with half credit for ties."""
    y = np.asarray(y, dtype=bool)
    ranks = pd.Series(np.asarray(score)).rank(method="average").to_numpy()
    pos, neg = int(y.sum()), int((~y).sum())
    return float((ranks[y].sum() - pos * (pos + 1) / 2) / (pos * neg))


def main():
    nodes, edges, tx = load(ROOT / "data")
    frame = nodes.set_index("gid").copy()
    incoming = tx.groupby("dst").agg(in_tx=("src", "size"), in_deg=("src", "nunique"),
        in_cents=("_cents", "sum"), in_max_cents=("_cents", "max"),
        incoming_active_days=("date", "nunique"), in_first=("date", "min"),
        in_last=("date", "max"))
    frame = frame.join(incoming)
    frame["in_span_days"] = (frame.in_last - frame.in_first).dt.days
    frame["in_max_share"] = frame.in_max_cents / frame.in_cents
    frame["out_deg"] = edges.groupby("src").dst.nunique().reindex(frame.index, fill_value=0)
    all_dates = pd.concat([tx[["src", "date"]].rename(columns={"src": "gid"}),
                           tx[["dst", "date"]].rename(columns={"dst": "gid"})])
    all_dates = all_dates.groupby("gid").date.agg(["nunique", "min", "max"])
    frame["all_active_days"] = all_dates["nunique"]
    frame["all_span_days"] = (all_dates["max"] - all_dates["min"]).dt.days
    interior = frame[frame.depth.between(1, 3)].fillna(0)
    target = interior.out_deg > 0
    score_cols = ["in_tx", "in_deg", "incoming_active_days", "in_span_days",
                  "in_max_share", "all_active_days", "all_span_days"]
    terminal = interior[~target]
    boundary = frame[frame.depth == 4]
    # Empirical KS distance is descriptive only, not an equivalence claim/p-value.
    def ecdf_distance(a, b):
        a, b = np.sort(a), np.sort(b)
        points = np.sort(np.unique(np.concatenate([a, b])))
        return float(np.abs(np.searchsorted(a, points, side="right") / len(a) -
                            np.searchsorted(b, points, side="right") / len(b)).max())

    out = {
        "input_hashes": input_hashes(ROOT / "data"),
        "proxy_target": "any observed outgoing edge in July, not transit/terminal ground truth",
        "interior_n": len(interior), "interior_outgoing_n": int(target.sum()),
        "interior_no_outgoing_n": int((~target).sum()),
        "auc": {col: auc(target, interior[col]) for col in score_cols},
        "incoming_vs_all_days_differ_n": int((interior.incoming_active_days != interior.all_active_days).sum()),
        "incoming_vs_all_spans_differ_n": int((interior.in_span_days != interior.all_span_days).sum()),
        "boundary_vs_observed_no_outgoing": {
            "boundary_n": len(boundary), "observed_no_outgoing_n": len(terminal),
            "in_kzt_medians": [float(boundary.in_cents.median() / 100), float(terminal.in_cents.median() / 100)],
            "in_tx_medians": [float(boundary.in_tx.median()), float(terminal.in_tx.median())],
            "in_kzt_ecdf_max_distance": ecdf_distance(boundary.in_cents, terminal.in_cents),
            "in_tx_ecdf_max_distance": ecdf_distance(boundary.in_tx, terminal.in_tx)},
    }
    gids = nodes.gid.tolist()
    index = {int(gid): i for i, gid in enumerate(gids)}
    n = len(gids)
    src = np.array([index[int(v)] for v in edges.src])
    dst = np.array([index[int(v)] for v in edges.dst])
    weights = edges._cents.to_numpy(dtype=float)
    out_sum = np.bincount(src, weights=weights, minlength=n)
    transition = weights / out_sum[src]
    p = np.ones(n) / n
    for step in range(10000):
        updated = .85 * (np.bincount(dst, weights=p[src] * transition, minlength=n) +
                         p[out_sum == 0].sum() / n) + .15 / n
        if np.abs(p - updated).sum() < 1e-14:
            p = updated
            break
        p = updated
    graph = nx.DiGraph()
    graph.add_nodes_from(gids)
    graph.add_edges_from(zip(edges.src, edges.dst))
    bc_dict = nx.betweenness_centrality(graph, weight=None, normalized=True)
    bc = np.array([bc_dict[gid] for gid in gids])
    rankings = {"pagerank_sum_kzt": p, "betweenness_exact_unweighted": bc}
    hits_info = {}
    for name, w in [("unweighted", np.ones(len(edges))), ("amount_weighted", weights / weights.max())]:
        h = np.ones(n) / np.sqrt(n)
        for iteration in range(10000):
            a = np.bincount(dst, weights=w * h[src], minlength=n)
            a /= np.linalg.norm(a)
            updated_h = np.bincount(src, weights=w * a[dst], minlength=n)
            updated_h /= np.linalg.norm(updated_h)
            error = float(np.linalg.norm(updated_h - h))
            h = updated_h
            if error < 1e-13:
                break
        a = np.bincount(dst, weights=w * h[src], minlength=n)
        a /= a.sum()
        h /= h.sum()
        rankings[f"hubs_{name}"] = h
        rankings[f"authorities_{name}"] = a
        hits_info[name] = {"power_iterations": iteration + 1, "l2_difference": error}
    top50 = {key: set(np.lexsort((np.array(gids), -scores))[:50].tolist())
             for key, scores in rankings.items()}
    out["metric_comparison"] = {
        "pagerank": {"alpha": .85, "l1_tolerance": 1e-14, "iterations": step + 1},
        "hits": hits_info,
        "top50_intersections": {a: {b: len(top50[a] & top50[b]) for b in top50} for a in top50},
        "betweenness_positive_n": int((bc > 0).sum()),
        "interpretation": "Different metrics measure different graph structures; overlap is not accuracy without labels."
    }
    path = ROOT / "artifacts/research/terminal_proxy_review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
