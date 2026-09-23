"""Local, label-free sensitivity study. Writes only ignored research artifacts.

Run from the repository root: .venv/Scripts/python.exe scripts/research_robustness.py
No graph label agreement in this report is a measure of detection accuracy.
"""
from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import networkx as nx
from moneygraph.analysis import analyze, read_config
from moneygraph.io import input_hashes, load


def adjusted_rand(labels_a, labels_b):
    """Permutation-invariant comparison of community partitions, without sklearn."""
    choose2 = lambda n: n * (n - 1) / 2
    cells = Counter(zip(labels_a, labels_b))
    rows, cols = Counter(labels_a), Counter(labels_b)
    observed = sum(choose2(n) for n in cells.values())
    row_pairs = sum(choose2(n) for n in rows.values())
    col_pairs = sum(choose2(n) for n in cols.values())
    total = choose2(len(labels_a))
    expected = row_pairs * col_pairs / total
    maximum = (row_pairs + col_pairs) / 2
    return (observed - expected) / (maximum - expected) if maximum != expected else 1.0


def ordered(frame):
    return frame.sort_values(['priority_score', 'gid'], ascending=[False, True])


def selection(frame):
    return {
        'nodes': len(frame), 'known_seeds': int(frame.is_seed.sum()),
        'non_seed_nodes': int((~frame.is_seed).sum()),
        'roles': {str(k): int(v) for k, v in frame.role.value_counts().items()},
        'mean_seed_reach': float(frame.seed_reach.mean()),
        'distinct_clusters': int(frame.cluster_id.nunique()),
        'distinct_components': int(frame.component_id.nunique()),
        'observed_flow_max_sum_kzt': float(frame[['in_kzt', 'out_kzt']].max(axis=1).sum()),
        'zero_betweenness_nodes': int((frame.betweenness == 0).sum()),
        'gid': [str(gid) for gid in frame.gid],
    }


def comparison(base, variant):
    a = base.set_index('gid').sort_index()
    b = variant.set_index('gid').sort_index()
    rank_a, rank_b = ordered(base), ordered(variant)
    result = {
        'role_changes': int((a.role != b.role).sum()),
        'role_counts': {str(k): int(v) for k, v in b.role.value_counts().items()},
        'role_transitions': {f'{x}->{y}': n for (x, y), n in Counter(zip(a.role, b.role)).items() if x != y},
        'priority_spearman': float(a.priority_score.rank().corr(b.priority_score.rank())),
        'community_adjusted_rand': adjusted_rand(a.cluster_id.tolist(), b.cluster_id.tolist()),
        'clusters': int(b.cluster_id.nunique()),
        'nonzero_betweenness': int((b.betweenness > 0).sum()),
    }
    for k in (20, 50):
        sa, sb = set(rank_a.head(k).gid), set(rank_b.head(k).gid)
        result[f'top{k}_intersection'] = len(sa & sb)
        result[f'top{k}_jaccard'] = len(sa & sb) / len(sa | sb)
        result[f'top{k}_role_changes_among_original'] = int((a.loc[list(sa), 'role'] != b.loc[list(sa), 'role']).sum())
    return result


def main():
    started = perf_counter()
    nodes, edges, tx = load(ROOT / 'data')
    cfg = read_config()
    original_bc = nx.betweenness_centrality
    original_louvain = nx.community.louvain_communities

    def calculate(settings, bc_seed=None, louvain_seed=None, exact=False):
        def bc(graph, **kwargs):
            if exact:
                kwargs['k'] = None
            if bc_seed is not None:
                kwargs['seed'] = bc_seed
            return original_bc(graph, **kwargs)

        def louvain(graph, **kwargs):
            if louvain_seed is not None:
                kwargs['seed'] = louvain_seed
            return original_louvain(graph, **kwargs)

        start = perf_counter()
        with patch('networkx.betweenness_centrality', side_effect=bc), patch('networkx.community.louvain_communities', side_effect=louvain):
            _, frame, clusters, _ = analyze(nodes, edges, tx, settings)
        return frame, clusters, perf_counter() - start

    base, clusters, baseline_seconds = calculate(cfg)
    base_ordered = ordered(base)
    result = {
        'warning': 'Sensitivity and ranking agreement, NOT accuracy; no ground-truth labels exist.',
        'input_sha256': input_hashes(ROOT / 'data'),
        'baseline_config': cfg,
        'baseline': {
            'seconds_analyze_only': baseline_seconds,
            'nodes': len(base),
            'role_counts': {str(k): int(v) for k, v in base.role.value_counts().items()},
            'clusters': len(clusters), 'clusters_with_multiple_seeds': int((clusters.n_seed > 1).sum()),
            'positive_betweenness': int((base.betweenness > 0).sum()),
            'centrality_quantile_threshold': float(base.loc[base.in_deg + base.out_deg > 0, 'betweenness'].quantile(cfg['coordinator_centrality_quantile'])),
            'nodes_with_multiple_role_matches': int(base.matched_roles.map(len).gt(1).sum()),
            'top20': selection(base_ordered.head(20)), 'top50': selection(base_ordered.head(50)),
        },
        'experiments': [], 'simple_rankings': {},
    }

    def experiment(name, settings=None, **kwargs):
        settings = settings or cfg
        frame, _, seconds = calculate(settings, **kwargs)
        item = {'name': name, 'seconds_analyze_only': seconds, 'config_changes': {k: v for k, v in settings.items() if cfg[k] != v}, 'overrides': kwargs}
        item.update(comparison(base, frame))
        result['experiments'].append(item)
        print(json.dumps({k: item[k] for k in ('name', 'role_changes', 'top20_intersection', 'top50_intersection', 'community_adjusted_rand')}, ensure_ascii=False), flush=True)
        return frame

    exact_frame = experiment('exact_betweenness', exact=True)
    result['exact_betweenness_top20'] = selection(ordered(exact_frame).head(20))
    exact_index = exact_frame.set_index('gid')
    result['sampled_betweenness_false_zero_relative_to_exact'] = int(((base.set_index('gid').betweenness == 0) & (exact_index.betweenness > 0)).sum())
    for seed in (0, 1, 7, 99):
        experiment(f'betweenness_seed_{seed}', bc_seed=seed)
        experiment(f'louvain_seed_{seed}', louvain_seed=seed)
        changed = deepcopy(cfg)
        changed['random_seed'] = seed
        experiment(f'both_seeds_{seed}', settings=changed)
    for resolution in (0.8, 1.2):
        changed = deepcopy(cfg)
        changed['louvain_resolution'] = resolution
        experiment(f'louvain_resolution_{resolution}', settings=changed)
    for name, multiplier in [('thresholds_relaxed', 0.8), ('thresholds_strict', 1.2)]:
        changed = deepcopy(cfg)
        for field in ('consolidator_min_payers', 'consolidator_min_seeds', 'distributor_min_payees', 'coordinator_min_degree', 'coordinator_min_seeds', 'coordinator_min_other_clusters'):
            changed[field] = max(1, math.floor(cfg[field] * multiplier) if multiplier < 1 else math.ceil(cfg[field] * multiplier))
        changed['distributor_fan_ratio'] *= multiplier
        tolerance = 0.2 * (1.2 if multiplier < 1 else 0.8)
        changed['transit_ratio_min'], changed['transit_ratio_max'] = 1 - tolerance, 1 + tolerance
        changed['coordinator_centrality_quantile'] = 0.85 if multiplier < 1 else 0.95
        experiment(name, settings=changed)
    for factor in cfg['priority_weights']:
        for multiplier in (0.8, 1.2):
            changed = deepcopy(cfg)
            changed['priority_weights'][factor] *= multiplier
            total = sum(changed['priority_weights'].values())
            changed['priority_weights'] = {k: v / total for k, v in changed['priority_weights'].items()}
            experiment(f'weight_{factor}_x{multiplier}', settings=changed)
    for name, values in {
        'observed_flow_max': base[['in_kzt', 'out_kzt']].max(axis=1),
        'observed_flow_in_plus_out': base.in_kzt + base.out_kzt,
        'fanin': base.in_deg,
        'fanout': base.out_deg,
        'seed_reach': base.seed_reach,
        'exact_betweenness': exact_frame.betweenness,
    }.items():
        alternative = base.assign(baseline_score=values).sort_values(['baseline_score', 'gid'], ascending=[False, True])
        info = {}
        for k in (20, 50):
            sa, sb = set(base_ordered.head(k).gid), set(alternative.head(k).gid)
            info[f'top{k}_intersection_with_current'] = len(sa & sb)
            info[f'top{k}'] = selection(alternative.head(k))
        result['simple_rankings'][name] = info
    result['elapsed_seconds'] = perf_counter() - started
    out = ROOT / 'artifacts' / 'research' / 'robustness.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    print(f'Wrote {out}; elapsed {result["elapsed_seconds"]:.2f}s', flush=True)


if __name__ == '__main__':
    main()
