"""Reproduce transit claims using explicit timing/amount definitions (read-only)."""
from pathlib import Path
import sys
import json
from collections import deque

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moneygraph.io import load, input_hashes
from moneygraph.analysis import analyze, read_config


def fifo(rows, lower, upper):
    daily = {}
    for direction, day, amount in rows:
        daily.setdefault(day, [0, 0])[direction] += amount
    pending = deque()
    matched = total_in = 0
    for day, (incoming, outgoing) in sorted(daily.items()):
        total_in += incoming
        pending.append([day, incoming])
        while pending and pending[0][0] < day - upper:
            pending.popleft()
        for item in pending:
            if day - item[0] < lower:
                continue
            take = min(outgoing, item[1])
            matched += take
            outgoing -= take
            item[1] -= take
    return matched / total_in if total_in else 0


def main():
    nodes, edges, tx = load(ROOT / 'data')
    _, frame, _, _ = analyze(nodes, edges, tx, read_config())
    incoming, outgoing = {}, {}
    for row in tx.itertuples(index=False):
        if row.src == row.dst:
            continue
        record = (row.date.toordinal(), round(row.sum_kzt * 100), str(row.src), str(row.dst))
        incoming.setdefault(int(row.dst), []).append(record)
        outgoing.setdefault(int(row.src), []).append(record)
    results = []
    for row in frame.loc[frame.role == 'transit'].itertuples(index=False):
        inc, out = incoming[int(row.gid)], outgoing[int(row.gid)]
        pairs = [(i, o) for i in inc for o in out if 0 <= o[0] - i[0] <= 2]
        all_pairs = [(i, o) for i in inc for o in out if o[0] >= i[0]]
        rows = [(0, d, a) for d, a, _, _ in inc] + [(1, d, a) for d, a, _, _ in out]
        record = dict(gid=str(row.gid), all_out_before_all_in=max(o[0] for o in out) < min(i[0] for i in inc),
                      any_after=bool(all_pairs), any_within_0_2=bool(pairs),
                      pair_out_ge_85pct=any(o[1] * 100 >= i[1] * 85 for i, o in pairs),
                      pair_out_between_85_115pct=any(i[1]*85 <= o[1]*100 <= i[1]*115 for i,o in pairs),
                      pair_out_between_85_100pct=any(i[1]*85 <= o[1]*100 <= i[1]*100 for i,o in pairs),
                      pair_min_max_85pct=any(min(i[1],o[1])*100 >= max(i[1],o[1])*85 for i,o in pairs),
                      pair_min_max_85pct_distinct_endpoints=any(min(i[1],o[1])*100 >= max(i[1],o[1])*85
                                                             and i[2] != o[3] for i,o in pairs),
                      fifo_0_2=fifo(rows, 0, 2), fifo_1_2=fifo(rows, 1, 2), production_fifo=row.temporal_2d,
                      operations=[dict(direction='in', day=d, cents=a, src=s, dst=t) for d,a,s,t in inc] +
                                 [dict(direction='out', day=d, cents=a, src=s, dst=t) for d,a,s,t in out])
        assert abs(record['fifo_0_2']-record['production_fifo']) < 1e-12
        results.append(record)
    definitions = {
        'primary_transit': len(results),
        'all_output_strictly_before_all_input': sum(r['all_out_before_all_in'] for r in results),
        'no_any_output_on_or_after_input': sum(not r['any_after'] for r in results),
        'no_timing_pair_0_2': sum(not r['any_within_0_2'] for r in results),
        'no_pair_out_ge_85pct_0_2': sum(not r['pair_out_ge_85pct'] for r in results),
        'no_pair_out_between_85_115pct_0_2': sum(not r['pair_out_between_85_115pct'] for r in results),
        'no_pair_out_between_85_100pct_0_2': sum(not r['pair_out_between_85_100pct'] for r in results),
        'no_pair_min_max_ratio_ge_85pct_0_2': sum(not r['pair_min_max_85pct'] for r in results),
        'no_pair_min_max_ratio_ge_85pct_0_2_distinct_endpoints': sum(not r['pair_min_max_85pct_distinct_endpoints'] for r in results),
        'of_32_no_symmetric_pair_have_positive_fifo': sum(not r['pair_min_max_85pct'] and r['fifo_0_2'] > 0 for r in results),
        'of_32_no_symmetric_pair_have_fifo_at_least_80pct': sum(not r['pair_min_max_85pct'] and r['fifo_0_2'] >= .8 for r in results),
        'zero_fifo_0_2': sum(r['fifo_0_2'] == 0 for r in results),
        'fifo_0_2_below_80pct': sum(r['fifo_0_2'] < .8 for r in results),
        'fifo_0_2_at_least_80pct': sum(r['fifo_0_2'] >= .8 for r in results),
        'zero_fifo_1_2': sum(r['fifo_1_2'] == 0 for r in results),
    }
    counterexamples = [r for r in results if not r['pair_out_ge_85pct'] and r['fifo_0_2'] >= .8]
    motif_nodes, motif_pairs = set(), 0
    for gid, inc in incoming.items():
        for i in inc:
            for o in outgoing.get(gid, []):
                if (0 <= o[0] - i[0] <= 2 and min(i[1], o[1])*100 >= max(i[1], o[1])*85
                        and i[2] != o[3]):
                    motif_nodes.add(gid)
                    motif_pairs += 1
    result = dict(hashes=input_hashes(ROOT/'data'), definitions=definitions,
                  all_graph_symmetric_85pct_distinct_endpoints=dict(nodes=len(motif_nodes), pairs=motif_pairs),
                  missing_85pct_pair_but_fifo_at_least_80pct=counterexamples, nodes=results)
    output = ROOT/'artifacts/research/transit_definitions.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(definitions=definitions, counterexamples=counterexamples), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
