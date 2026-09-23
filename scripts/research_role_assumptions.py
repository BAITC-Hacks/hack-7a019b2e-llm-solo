"""Check metric precision and role stability without changing production rules."""
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import networkx as nx
from moneygraph.analysis import analyze, build_graph, classify, read_config
from moneygraph.io import input_hashes, load


def threshold_scenario(cfg, multiplier):
    changed = deepcopy(cfg)
    for key in ('consolidator_min_payers', 'consolidator_min_seeds',
                'distributor_min_payees', 'coordinator_min_degree',
                'coordinator_min_seeds', 'coordinator_min_other_clusters'):
        changed[key] = max(1, math.floor(cfg[key] * multiplier)
                           if multiplier < 1 else math.ceil(cfg[key] * multiplier))
    changed['distributor_fan_ratio'] *= multiplier
    tolerance = 0.2 * (1.2 if multiplier < 1 else 0.8)
    changed['transit_ratio_min'] = 1 - tolerance
    changed['transit_ratio_max'] = 1 + tolerance
    changed['coordinator_centrality_quantile'] = 0.85 if multiplier < 1 else 0.95
    return changed


def main():
    nodes, edges, tx = load(ROOT / 'data')
    cfg = read_config()
    graph = build_graph(nodes, edges)
    graph.remove_edges_from(list(nx.selfloop_edges(graph)))
    started = perf_counter()
    reference = nx.betweenness_centrality(graph, k=None, weight=None, normalized=True)
    exact_seconds = perf_counter() - started
    sampled = nx.betweenness_centrality(graph, k=min(cfg['betweenness_samples'], len(graph)),
                                       weight=None, normalized=True, seed=cfg['random_seed'])
    exact_cfg = deepcopy(cfg)
    exact_cfg['betweenness_samples'] = len(graph)
    _, frame, _, _ = analyze(nodes, edges, tx, exact_cfg)
    kn_error = max(abs(reference[int(r.gid)] - r.betweenness) for r in frame.itertuples())
    configs = {'base': exact_cfg, 'relaxed': threshold_scenario(exact_cfg, 0.8),
               'strict': threshold_scenario(exact_cfg, 1.2)}
    active = frame.in_deg + frame.out_deg > 0
    classified = {}
    for name, settings in configs.items():
        threshold = float(frame.loc[active, 'betweenness'].quantile(
            settings['coordinator_centrality_quantile']))
        classified[name] = {
            int(row['gid']): classify(row, settings, threshold)
            for row in frame.to_dict('records')
        }
    stable = {gid: len({values[gid][0] for values in classified.values()}) == 1
              for gid in classified['base']}
    zero_time_stable_transit = [
        row for row in frame.itertuples()
        if classified['base'][int(row.gid)][0] == 'transit'
        and stable[int(row.gid)] and row.temporal_2d == 0
    ]
    zero_time_stable_transit.sort(key=lambda row: (-row.in_kzt, row.gid))
    counterexamples = []
    for row in zero_time_stable_transit[:3]:
        selected = tx[(tx.src == row.gid) | (tx.dst == row.gid)]
        counterexamples.append({
            'gid': str(row.gid), 'in_kzt': row.in_kzt, 'out_kzt': row.out_kzt,
            'roles_by_threshold_scenario': {name: values[int(row.gid)][0]
                                            for name, values in classified.items()},
            'temporal_0_2d_compatibility': row.temporal_2d,
            'operations': [
                {'src': str(r.src), 'dst': str(r.dst), 'date': str(r.date.date()),
                 'amount_minor_units': str(int(round(r.sum_kzt * 100)))}
                for r in selected.itertuples()
            ],
        })
    ranking = frame.sort_values(['priority_score', 'gid'], ascending=[False, True])
    max_fanout = frame.sort_values(['out_deg', 'gid'], ascending=[False, True]).iloc[0]
    positions = {int(gid): position for position, gid in enumerate(ranking.gid, 1)}
    result = {
        'input_sha256': input_hashes(ROOT / 'data'),
        'versions': {'python': sys.version.split()[0], 'networkx': nx.__version__},
        'base_config': cfg, 'tested_threshold_configs': configs,
        'metric_check': {
            'exact_metric_only_seconds': exact_seconds,
            'exact_positive_nodes': sum(value > 0 for value in reference.values()),
            'sampled_zeros_where_exact_positive': sum(sampled[g] == 0 and reference[g] > 0
                                                       for g in graph),
            'sampled_max_absolute_error': max(abs(sampled[g] - reference[g]) for g in graph),
            'k_equals_N_max_absolute_error': kn_error,
        },
        'stability_check': {
            'design': 'Three declared threshold scenarios; exact features and communities fixed. Sensitivity, not probability of truth.',
            'primary_role_stable_nodes': sum(stable.values()),
            'primary_role_changed_nodes': sum(not v for v in stable.values()),
            'stable_transit_with_zero_temporal_compatibility': len(zero_time_stable_transit),
            'counterexamples': counterexamples,
        },
        'max_fanout_with_exact_metric': {
            'gid': str(int(max_fanout.gid)), 'out_degree': int(max_fanout.out_deg),
            'rank': positions[int(max_fanout.gid)], 'priority_score': float(max_fanout.priority_score),
        },
    }
    out = ROOT / 'artifacts' / 'research' / 'role_assumptions.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({key: result[key] for key in
                     ('metric_check', 'stability_check', 'max_fanout_with_exact_metric')},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
