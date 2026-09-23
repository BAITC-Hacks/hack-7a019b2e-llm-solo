"""Describe actual components and distribution patterns, without assigning guilt."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from moneygraph.analysis import analyze, read_config
from moneygraph.io import input_hashes, load


def main():
    nodes, edges, tx = load(ROOT / 'data')
    cfg = read_config()
    graph, frame, _, _ = analyze(nodes, edges, tx, cfg)
    ranked = frame.sort_values(['priority_score', 'gid'], ascending=[False, True])
    ranks = {int(gid): i for i, gid in enumerate(ranked.gid, 1)}
    component_rows = []
    for cid, group in frame.groupby('component_id'):
        members = set(int(x) for x in group.gid)
        internal = edges[edges.src.isin(members) & edges.dst.isin(members)]
        leader = group.sort_values(['out_deg', 'gid'], ascending=[False, True]).iloc[0]
        component_rows.append({
            'component_id': int(cid), 'nodes': len(group),
            'seeds': int(group.is_seed.sum()), 'edges': len(internal),
            'transactions': int(internal.n_tx.sum()),
            'turnover_minor_units': str(int(internal._cents.sum())),
            'max_out_degree': int(leader.out_deg),
            'distribution_leader_gid': str(int(leader.gid)),
            'distribution_leader_global_rank': ranks[int(leader.gid)],
        })
    examples = []
    for row in frame.sort_values(['out_deg', 'gid'], ascending=[False, True]).head(5).itertuples():
        outgoing = tx[tx.src == row.gid]
        per_day = outgoing.groupby('date').agg(
            recipients=('dst', 'nunique'), transactions=('dst', 'size'),
            minor_units=('_cents', 'sum')).sort_values(
                ['recipients', 'minor_units'], ascending=False)
        destinations = outgoing.groupby('dst').size()
        examples.append({
            'gid': str(row.gid), 'is_seed': bool(row.is_seed),
            'component_id': int(row.component_id), 'depth': int(row.depth),
            'role': row.role, 'global_rank': ranks[int(row.gid)],
            'recipients': int(row.out_deg), 'outgoing_transactions': int(row.out_tx),
            'days_with_outgoing': int(outgoing.date.nunique()),
            'recipients_paid_exactly_once': int((destinations == 1).sum()),
            'outgoing_minor_units': str(int(outgoing._cents.sum())),
            'largest_daily_fanouts': [
                {'date': str(day.date()), 'recipients': int(values.recipients),
                 'transactions': int(values.transactions),
                 'minor_units': str(int(values.minor_units))}
                for day, values in per_day.head(3).iterrows()
            ],
        })
    result = {
        'input_sha256': input_hashes(ROOT / 'data'), 'config': cfg,
        'interpretation': 'Observed distribution and components only; no customer type, intent or money provenance is inferred.',
        'components': sorted(component_rows, key=lambda r: (-r['nodes'], r['component_id'])),
        'distribution_examples': examples,
    }
    out = ROOT / 'artifacts' / 'research' / 'component_profiles.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'components_largest': result['components'][:2],
                      'distribution_examples': examples[:2]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
