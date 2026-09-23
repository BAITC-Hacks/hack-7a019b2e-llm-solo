"""Local research only: bounded static and calendar-compatible seed paths.

Does not alter application behavior. Run from repository root:
  .venv/Scripts/python.exe scripts/research_paths.py
No amount provenance is inferred; paths concern observed transaction dates only.
"""
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import networkx as nx
from moneygraph.analysis import analyze, read_config
from moneygraph.io import input_hashes, load


def temporal_reach(graph, dates, source, strict=False, max_hops=4):
    """Earliest arrival dominates later arrivals when waiting is unrestricted."""
    reached = {}
    layers = [{source: (0, [source], [])}]
    lookup = bisect_right if strict else bisect_left
    for _ in range(max_hops):
        layer = {}
        for u, (arrival, route, days) in layers[-1].items():
            for v in graph.successors(u):
                ds = dates[u, v]
                pos = lookup(ds, arrival)
                if pos == len(ds):
                    continue
                departure = ds[pos]
                if v not in layer or departure < layer[v][0]:
                    layer[v] = (departure, route + [v], days + [departure])
        layers.append(layer)
        for target, witness in layer.items():
            if target != source and target not in reached:
                reached[target] = witness
    return reached


def expanded_state_reach(graph, dates, source, strict=False, max_hops=4):
    """Independent exhaustive node/day state propagation for research verification."""
    frontier = {(source, 0)}
    reached = set()
    for _ in range(max_hops):
        next_frontier = set()
        for u, arrival in frontier:
            for v in graph.successors(u):
                for day in dates[u, v]:
                    if day > arrival if strict else day >= arrival:
                        next_frontier.add((v, day))
                        if v != source:
                            reached.add(v)
        frontier = next_frontier
    return reached


def main():
    started = time.perf_counter()
    nodes, edges, tx = load(ROOT / 'data')
    config = read_config()
    graph, frame, clusters, top = analyze(nodes, edges, tx, config)
    graph.remove_edges_from(list(nx.selfloop_edges(graph)))
    seeds = sorted(int(x) for x in nodes.loc[nodes.is_seed, 'gid'])
    dates = {}
    for row in tx.itertuples(index=False):
        if row.src != row.dst:
            dates.setdefault((int(row.src), int(row.dst)), set()).add(row.date.toordinal())
    dates = {edge: sorted(ds) for edge, ds in dates.items()}
    static_by_node = {gid: set() for gid in graph}
    temporal_by_node = {gid: set() for gid in graph}
    strict_by_node = {gid: set() for gid in graph}
    incompatible = []
    for source in seeds:
        static = nx.single_source_shortest_path(graph, source, cutoff=4)
        temporal = temporal_reach(graph, dates, source)
        strict = temporal_reach(graph, dates, source, strict=True)
        assert set(temporal) == expanded_state_reach(graph, dates, source)
        assert set(strict) == expanded_state_reach(graph, dates, source, strict=True)
        for target, route in static.items():
            if target == source:
                continue
            static_by_node[target].add(source)
            if target in temporal:
                temporal_by_node[target].add(source)
            else:
                incompatible.append((source, target, route))
            if target in strict:
                strict_by_node[target].add(source)
    frame = frame.set_index('gid')
    assert all(int(frame.loc[g, 'seed_reach']) == len(static_by_node[g]) for g in graph)
    assert all(strict_by_node[g] <= temporal_by_node[g] <= static_by_node[g] for g in graph)

    def node_summary(g):
        r = frame.loc[g]
        return dict(gid=str(g), is_seed=bool(r.is_seed), role=str(r.role),
                    cluster_id=int(r.cluster_id), component_id=int(r.component_id),
                    static_seed_reach=len(static_by_node[g]),
                    calendar_compatible_seed_reach=len(temporal_by_node[g]),
                    strict_later_day_seed_reach=len(strict_by_node[g]),
                    in_deg=int(r.in_deg), out_deg=int(r.out_deg),
                    priority_score=float(r.priority_score))

    def group_summary(ids):
        rows = [node_summary(g) for g in ids]
        return dict(n=len(rows), known_seed=sum(r['is_seed'] for r in rows),
                    non_seed=sum(not r['is_seed'] for r in rows),
                    clusters=len({r['cluster_id'] for r in rows}),
                    components=len({r['component_id'] for r in rows}),
                    roles=dict(Counter(r['role'] for r in rows)),
                    nodes_with_reduced_reach=sum(r['static_seed_reach'] > r['calendar_compatible_seed_reach'] for r in rows),
                    static_seed_target_pairs=sum(r['static_seed_reach'] for r in rows),
                    calendar_compatible_pairs=sum(r['calendar_compatible_seed_reach'] for r in rows),
                    strict_later_day_pairs=sum(r['strict_later_day_seed_reach'] for r in rows),
                    nodes_without_calendar_compatible_seed=sum(r['static_seed_reach'] > 0 and r['calendar_compatible_seed_reach'] == 0 for r in rows),
                    rows=rows)

    top_ids = [int(x) for x in top.gid]
    priority_rank = {gid: rank for rank, gid in enumerate(top_ids, start=1)}
    examples = []
    example_targets = set()
    for source, target, route in sorted(incompatible, key=lambda r: (priority_rank.get(r[1], 9999), len(r[2]), r[0], r[1])):
        if target in example_targets:
            continue
        example_targets.add(target)
        route_edges = []
        arrival = 0
        failure = None
        for position, (u, v) in enumerate(zip(route, route[1:]), start=1):
            ds = dates[u, v]
            pos = bisect_left(ds, arrival)
            if pos == len(ds) and failure is None:
                failure = position
            elif failure is None:
                arrival = ds[pos]
            route_edges.append(dict(src=str(u), dst=str(v), dates=[date.fromordinal(d).isoformat() for d in ds]))
        examples.append(dict(source=str(source), target=str(target), top_rank=priority_rank.get(target),
                             path=[str(g) for g in route], edges=route_edges, first_impossible_edge=failure,
                             target_metrics=node_summary(target),
                             note='No nondecreasing-date path of <=4 hops exists for this seed-target pair; displayed route is one shortest static path.'))
        if len(examples) == 20:
            break

    scc = sorted([set(s) for s in nx.strongly_connected_components(graph) if len(s) > 1], key=lambda s: (-len(s), min(s)))
    reciprocal = {(min(u, v), max(u, v)) for u, v in graph.edges if graph.has_edge(v, u)}
    wedges = compatible_wedges = strict_wedges = closed_wedges = 0
    directed_triangles = set()
    for middle in graph:
        for left in graph.predecessors(middle):
            for right in graph.successors(middle):
                if left == right:
                    continue
                wedges += 1
                compatible_wedges += dates[left, middle][0] <= dates[middle, right][-1]
                strict_wedges += dates[left, middle][0] < dates[middle, right][-1]
                closed_wedges += graph.has_edge(left, right)
                if graph.has_edge(right, left):
                    rotations = [(left, middle, right), (middle, right, left), (right, left, middle)]
                    directed_triangles.add(min(rotations))
    all_group = group_summary(list(graph))
    all_group.pop('rows')
    result = dict(
        input_sha256=input_hashes(ROOT / 'data'),
        config=config,
        versions={'python': sys.version.split()[0], 'networkx': nx.__version__},
        definitions={
            'static': 'Directed path <=4 hops; target is not the same seed.',
            'calendar_compatible': 'At least one <=4-hop path has nondecreasing dates. Same-day order is unknown; no amount origin is asserted.',
            'strict_later_day': 'At least one <=4-hop path has strictly increasing dates. This establishes observed ordering only, not provenance or intent.',
            'impossible': 'No calendar-compatible path <=4 hops in this observed month; unobserved earlier money, longer routes and external flows remain unknown.',
        },
        all_nodes=all_group,
        top20=group_summary(top_ids[:20]),
        top50=group_summary(top_ids[:50]),
        calendar_compatible_reach_distribution=dict(Counter(len(s) for s in temporal_by_node.values())),
        static_reach_distribution=dict(Counter(len(s) for s in static_by_node.values())),
        impossible_pair_examples=examples,
        motifs=dict(reciprocal_pairs=len(reciprocal), scc_gt1_count=len(scc),
                    scc_gt1_sizes=[len(s) for s in scc], scc_gt1_nodes=sum(map(len, scc)),
                    directed_distinct_node_two_edge_paths=wedges,
                    two_edge_paths_with_calendar_compatible_dates=compatible_wedges,
                    two_edge_paths_with_strict_dates=strict_wedges,
                    two_edge_paths_with_shortcut_edge=closed_wedges,
                    directed_three_cycles=len(directed_triangles)),
        verification='All 81 seeds checked in both date modes against independent exhaustive node/day state propagation.',
        elapsed_seconds=round(time.perf_counter() - started, 4),
    )
    output = ROOT / 'artifacts' / 'research' / 'paths.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    printable = {k: v for k, v in result.items() if k not in ['impossible_pair_examples', 'definitions']}
    for name in ['top20', 'top50']:
        printable[name] = {k: v for k, v in printable[name].items() if k != 'rows'}
    print(json.dumps(printable, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
