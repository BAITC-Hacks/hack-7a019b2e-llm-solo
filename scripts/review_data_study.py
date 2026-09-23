"""Independent, read-only reproduction of numerical claims in DATA-STUDY.md."""
from collections import Counter, deque
from pathlib import Path
import hashlib
import json
import math

import networkx as nx
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    nodes = pd.read_parquet(ROOT / 'data/nodes.parquet')
    edges = pd.read_parquet(ROOT / 'data/edges.parquet')
    tx = pd.read_parquet(ROOT / 'data/transactions.parquet')
    tx['cents'] = np.rint(tx.sum_kzt * 100).astype('int64')
    edges['cents'] = np.rint(edges.sum_kzt * 100).astype('int64')
    tx['day'] = pd.to_datetime(tx.date).map(lambda d: d.toordinal())
    g = nx.DiGraph()
    g.add_nodes_from(int(x) for x in nodes.gid)
    g.add_edges_from((int(r.src), int(r.dst), {'cents': int(r.cents)}) for r in edges.itertuples())
    depth = nodes.set_index('gid').depth.to_dict()
    seed = set(nodes.loc[nodes.is_seed, 'gid'])
    result = {'hashes': {name: hashlib.sha256((ROOT / 'data' / name).read_bytes()).hexdigest()
                         for name in ['nodes.parquet', 'edges.parquet', 'transactions.parquet']}}
    result['structural'] = {
        'nodes': len(nodes), 'edges': len(edges), 'transactions': len(tx),
        'backward': sum(depth[a] > depth[b] for a, b in g.edges),
        'same_depth': sum(depth[a] == depth[b] for a, b in g.edges),
        'nonseed_to_seed': sum(a not in seed and b in seed for a, b in g.edges),
        'weak_components': nx.number_weakly_connected_components(g),
        'self_loops': nx.number_of_selfloops(g),
        'gids_altered_by_float64_roundtrip': sum(int(x) != int(float(x)) for x in nodes.gid),
    }
    components = sorted(nx.weakly_connected_components(g), key=lambda c: (-len(c), min(c)))
    result['components'] = [dict(nodes=len(c), seeds=len(seed.intersection(c)),
                               kzt=sum(g[a][b]['cents'] for a in c for b in g.successors(a))/100)
                            for c in components]
    result['other_components_after_first_four_kzt'] = sum(c['kzt'] for c in result['components'][4:])
    cycles = list(nx.simple_cycles(g, length_bound=6))
    result['cycles'] = {'n': len(cycles), 'length_counts': dict(Counter(map(len, cycles))),
                        'nodes': len(set(x for c in cycles for x in c))}
    date_by_edge = {k: sorted(set(int(x) for x in v.day)) for k, v in tx.groupby(['src', 'dst'])}
    def date_compatible(cycle, strict=False, window=None):
        # Any rotation may be the first observed leg. Earliest feasible choices
        # are sufficient after fixing the start leg and start date.
        for rotation in range(len(cycle)):
            chain = cycle[rotation:] + cycle[:rotation]
            arcs = list(zip(chain, chain[1:] + chain[:1]))
            for first_day in date_by_edge[arcs[0]]:
                last = first_day
                for arc in arcs[1:]:
                    choices = [d for d in date_by_edge[arc] if d > last or (not strict and d == last)]
                    if not choices:
                        break
                    last = choices[0]
                else:
                    if window is None or last - first_day <= window:
                        return True
        return False
    result['cycles']['nondecreasing_dates_any_rotation_n'] = sum(date_compatible(c) for c in cycles)
    result['cycles']['strictly_increasing_dates_any_rotation_n'] = sum(date_compatible(c, strict=True) for c in cycles)
    result['cycles']['nondecreasing_dates_round_trip_within2days_n'] = sum(date_compatible(c, window=2) for c in cycles)
    indeg = dict(g.in_degree())
    outdeg = dict(g.out_degree())
    incoming = dict(g.in_degree(weight='cents'))
    outgoing = dict(g.out_degree(weight='cents'))
    eligible = [gid for gid in g if gid not in seed and depth[gid] < 4 and incoming[gid] > outgoing[gid]]
    result['flow'] = {
        'total_kzt': int(tx.cents.sum()) / 100,
        'positive_observed_net_nonseed_below4_n': len(eligible),
        'positive_observed_net_nonseed_below4_kzt': sum(incoming[x] - outgoing[x] for x in eligible)/100,
        'sum_positive_observed_net_all_nodes_kzt': sum(max(incoming[x] - outgoing[x], 0) for x in g)/100,
        'received_zero_outgoing_kzt': sum(incoming[x] for x in g if outdeg[x] == 0)/100,
        'zero_outgoing_n': sum(outdeg[x] == 0 for x in g),
        'invisible_outgoing_seed_isolates': sum(indeg[x] == 0 and outdeg[x] == 0 for x in seed),
    }
    repeats = tx.groupby(['src', 'dst', 'cents']).size()
    groups = repeats[repeats >= 3]
    result['repeat_amounts'] = {'unique_cents': int(tx.cents.nunique()),
        'unique_float': int(tx.sum_kzt.nunique()), 'groups_gte3': len(groups),
        'directed_pairs_gte3': len(set((a,b) for a,b,c in groups.index)),
        'nodes': len(set(x for a,b,c in groups.index for x in [a,b])),
        'count_5000': int((tx.cents == 500000).sum()),
        'full_duplicate_rows_beyond_first': int(tx.duplicated(['src','dst','date','cents']).sum())}
    residual = tx.sum_kzt * 100 - tx.cents
    nonzero = tx[residual != 0]
    result['fractional_cents'] = {'nonzero_float_residual_n': len(nonzero),
        'maximum_abs_residual_in_cents': float(abs(residual).max()),
        'rows': [dict(sum_repr=repr(r.sum_kzt), cents=int(r.cents),
                    residual_cents=float(r.sum_kzt * 100 - r.cents),
                    ulp_kzt=math.ulp(r.sum_kzt)) for r in nonzero.itertuples()]}
    outgoing_days = {int(k): sorted(int(x) for x in v.day) for k,v in tx.groupby('src')}
    lag_rows = []
    matched_next_events = []
    for r in tx.itertuples():
        later = [d for d in outgoing_days.get(int(r.dst), []) if d >= r.day]
        if later:
            lag_rows.append((later[0] - r.day, int(r.cents)))
            matched_next_events.append((int(r.dst), later[0]))
    result['nearest_future_outgoing'] = {
        'matched_incoming_rows': len(lag_rows),
        'excluded_incoming_rows': len(tx) - len(lag_rows),
        'count_fractions_conditioned_on_a_later_outgoing': {str(d): sum(lag <= d for lag, c in lag_rows)/len(lag_rows) for d in [0,1,2,7]},
        'count_fractions_all_incoming': {str(d): sum(lag <= d for lag, c in lag_rows)/len(tx) for d in [0,1,2,7]},
        'amount_fractions_conditioned_on_a_later_outgoing': {str(d): sum(c for lag,c in lag_rows if lag <= d)/sum(c for lag,c in lag_rows) for d in [0,1,2,7]},
        'unique_next_node_days': len(set(matched_next_events)),
        'incoming_rows_reusing_same_next_node_day': sum(c-1 for c in Counter(matched_next_events).values() if c>1),
    }
    daily = {}
    for r in tx.itertuples():
        daily.setdefault(int(r.dst), {}).setdefault(r.day, [0,0])[0] += int(r.cents)
        daily.setdefault(int(r.src), {}).setdefault(r.day, [0,0])[1] += int(r.cents)
    fifo_results = {}
    for window in [0,1,2,7]:
        matched = 0
        all_in = 0
        for gid, days in daily.items():
            queue = deque()
            for day, (inc, out) in sorted(days.items()):
                all_in += inc
                while queue and queue[0][0] < day-window:
                    queue.popleft()
                if inc:
                    queue.append([day,inc])
                while out and queue:
                    n = min(out,queue[0][1]); out -= n; queue[0][1] -= n; matched += n
                    if not queue[0][1]: queue.popleft()
        fifo_results[str(window)] = {'matched_kzt': matched/100, 'incoming_kzt': all_in/100,
                                    'compatible_fraction': matched/all_in}
    result['fifo_no_reuse_all_nodes'] = fifo_results
    active_days = {gid:len(days) for gid, days in daily.items()}
    result['activity'] = {'one_day_nodes': sum(v == 1 for v in active_days.values()),
                          'max_active_days': max(active_days.values())}
    out = ROOT / 'artifacts/research/data_study_review.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
