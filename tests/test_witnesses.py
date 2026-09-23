"""Transaction-level evidence must support, not overstate, graph hypotheses."""
from collections import defaultdict
import random
import unittest

import networkx as nx
import pandas as pd

from moneygraph.analysis import build_graph, read_config, temporal_details
from moneygraph.io import validate_tables
from moneygraph.snapshot import make_snapshot
from moneygraph.witnesses import build_witnesses


def fixture(transfers, isolated=()):
    tx = pd.DataFrame(transfers, columns=['src', 'dst', 'date', 'sum_kzt'])
    gids = sorted(set(tx.src) | set(tx.dst) | set(isolated))
    nodes = pd.DataFrame({'gid': gids, 'depth': [1] * len(gids), 'is_seed': [False] * len(gids)})
    edges = tx.groupby(['src', 'dst'], as_index=False).agg(sum_kzt=('sum_kzt', 'sum'), n_tx=('sum_kzt', 'size'))
    edges['depth'] = 1
    return validate_tables(nodes, edges, tx)


class WitnessTests(unittest.TestCase):
    def build(self, transfers, isolated=(), clusters=None, window=2):
        nodes, edges, tx = fixture(transfers, isolated)
        frame = nodes[['gid']].copy()
        frame['cluster_id'] = [clusters[gid] if clusters else gid for gid in frame.gid]
        graph = build_graph(nodes, edges)
        cfg = {**read_config(), 'transit_window_days': window}
        return build_witnesses(graph, frame, tx, cfg), tx

    def assert_capacities_and_ordinals(self, witnesses, tx):
        payload = dict(nodes=[], edges=[], clusters=[], top=[], components=[],
                       motif_rankings=[], sensitivity_profiles=[],
                       summary={'input_sha256': {}}, config={})
        rows = make_snapshot(payload, tx, {})['transactions']
        for gid, witness in witnesses.items():
            timing = witness['temporal_evidence']
            for key in ('matches', 'strict_matches'):
                used_input, used_output = defaultdict(int), defaultdict(int)
                for match in timing[key]:
                    incoming, outgoing = rows[match['in_row_id']], rows[match['out_row_id']]
                    self.assertEqual(incoming['dst'], str(gid))
                    self.assertEqual(outgoing['src'], str(gid))
                    self.assertNotEqual(incoming['src'], str(gid))
                    self.assertNotEqual(outgoing['dst'], str(gid))
                    lag = (pd.Timestamp(outgoing['date']) - pd.Timestamp(incoming['date'])).days
                    self.assertEqual(match['lag_days'], lag)
                    self.assertLessEqual(lag, timing['window_days'])
                    self.assertGreaterEqual(lag, 1 if key == 'strict_matches' else 0)
                    self.assertIsInstance(match['cents'], int)
                    self.assertGreater(match['cents'], 0)
                    used_input[incoming['row_id']] += match['cents']
                    used_output[outgoing['row_id']] += match['cents']
                for usage in (used_input, used_output):
                    for row_id, cents in usage.items():
                        self.assertLessEqual(cents, rows[row_id]['cents'])
            bridge = witness['bridge_evidence']
            if bridge:
                self.assertEqual(bridge['transaction_row_ids'], [leg['row_id'] for leg in bridge['legs']])
                for index, leg in enumerate(bridge['legs']):
                    source = rows[leg['row_id']]
                    self.assertEqual(leg, {key: source[key] for key in leg})
                    self.assertEqual([leg['src'], leg['dst']], bridge['path'][index:index + 2])

    def test_future_input_cannot_explain_past_output(self):
        witnesses, tx = self.build([(2, 3, '2026-07-01', 100.), (1, 2, '2026-07-02', 100.)])
        timing = witnesses[2]['temporal_evidence']
        self.assertEqual(timing['matches'], [])
        self.assertEqual(timing['strict_matches'], [])
        self.assertEqual(timing['matched_cents'], 0)
        # Structural path is still factual, but never labelled a temporal chain.
        self.assertEqual(witnesses[2]['bridge_evidence']['semantic'], 'structural_path')
        self.assertIn('хронологическая совместимость', witnesses[2]['bridge_evidence']['limitation'])
        self.assert_capacities_and_ordinals(witnesses, tx)

    def test_split_merge_fifo_and_capacity_not_reused(self):
        witnesses, tx = self.build([(1, 2, '2026-07-01', 40.), (4, 2, '2026-07-01', 60.),
                                    (2, 3, '2026-07-02', 70.), (2, 5, '2026-07-03', 80.)])
        timing = witnesses[2]['temporal_evidence']
        self.assertEqual([match['cents'] for match in timing['matches']], [4000, 3000, 3000])
        self.assertEqual(timing['matched_cents'], 10000)
        self.assertEqual(timing['strict_matched_cents'], 10000)
        self.assertEqual(timing['total_out_cents'], 15000)
        self.assert_capacities_and_ordinals(witnesses, tx)

    def test_same_day_strict_scenario_is_independent(self):
        witnesses, tx = self.build([(1, 2, '2026-07-01', 100.),
                                    (2, 3, '2026-07-01', 60.), (2, 4, '2026-07-02', 60.)])
        timing = witnesses[2]['temporal_evidence']
        self.assertEqual(timing['matched_cents'], 10000)
        self.assertEqual(timing['same_day_matched_cents'], 6000)
        self.assertEqual(timing['strict_matched_cents'], 6000)
        self.assertEqual(timing['strict_matches'][0]['cents'], 6000)
        self.assert_capacities_and_ordinals(witnesses, tx)

    def test_window_expiration_and_zero_day_window(self):
        transfers = [(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-04', 100.)]
        self.assertEqual(self.build(transfers)[0][2]['temporal_evidence']['matched_cents'], 0)
        self.assertEqual(self.build(transfers, window=3)[0][2]['temporal_evidence']['matched_cents'], 10000)
        witnesses, tx = self.build([(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-01', 100.)], window=0)
        self.assertEqual(witnesses[2]['temporal_evidence']['strict_matches'], [])
        self.assertEqual(witnesses[2]['temporal_evidence']['matched_cents'], 10000)
        self.assert_capacities_and_ordinals(witnesses, tx)

    def test_self_transfers_excluded_and_isolates_retained(self):
        witnesses, tx = self.build([(1, 2, '2026-07-01', .01), (2, 3, '2026-07-02', .01),
                                    (2, 2, '2026-07-01', 900.)], isolated=(9,))
        timing = witnesses[2]['temporal_evidence']
        self.assertEqual(timing['total_in_cents'], 1)
        self.assertEqual(timing['total_out_cents'], 1)
        self.assertEqual(timing['matched_cents'], 1)
        self.assertIsNone(witnesses[9]['bridge_evidence'])
        self.assertEqual(witnesses[9]['temporal_evidence']['matches'], [])
        self.assertEqual(witnesses[9]['temporal_evidence']['total_in_cents'], 0)
        self.assert_capacities_and_ordinals(witnesses, tx)

    def test_duplicate_transfers_keep_distinct_large_gid_ordinals(self):
        offset = 100000000000000001
        witnesses, tx = self.build([(offset, offset + 1, '2026-07-01', 50.),
                                    (offset, offset + 1, '2026-07-01', 50.),
                                    (offset + 1, offset + 2, '2026-07-02', 100.)])
        matches = witnesses[offset + 1]['temporal_evidence']['matches']
        self.assertEqual([match['in_row_id'] for match in matches], [0, 1])
        self.assertEqual(witnesses[offset + 1]['bridge_evidence']['path'], list(map(str, range(offset, offset + 3))))
        self.assert_capacities_and_ordinals(witnesses, tx)

    def test_bridge_requires_direction_distinct_endpoints_and_clusters(self):
        tests = [
            ([(1, 2), (3, 2)], {1: 0, 2: 1, 3: 2}),  # Two incoming edges.
            ([(2, 1), (2, 3)], {1: 0, 2: 1, 3: 2}),  # Two outgoing edges.
            ([(1, 2), (2, 1)], {1: 0, 2: 1}),         # A cycle is not a simple A->gid->B path.
            ([(1, 2), (2, 3)], {1: 0, 2: 1, 3: 0}),  # Same endpoint community.
            ([(2, 2)], {2: 1}),
        ]
        for pairs, clusters in tests:
            with self.subTest(pairs=pairs):
                witnesses, _ = self.build([(a, b, '2026-07-01', 10.) for a, b in pairs], clusters=clusters)
                self.assertIsNone(witnesses[2]['bridge_evidence'])
        witnesses, tx = self.build([(4, 2, '2026-07-01', 10.), (1, 2, '2026-07-01', 10.),
                                    (2, 5, '2026-07-01', 10.), (2, 3, '2026-07-01', 10.)])
        self.assertEqual(witnesses[2]['bridge_evidence']['path'], ['1', '2', '3'])
        self.assert_capacities_and_ordinals(witnesses, tx)

    def test_zero_amount_transfers_do_not_create_allocations(self):
        witnesses, tx = self.build([(1, 2, '2026-07-01', 0.), (2, 3, '2026-07-01', 0.)])
        self.assertEqual(witnesses[2]['temporal_evidence']['matches'], [])
        self.assert_capacities_and_ordinals(witnesses, tx)

    def test_totals_match_daily_algorithm_for_generated_calendars(self):
        random_source = random.Random(71)
        transfers = [(random_source.randint(1, 8), random_source.randint(1, 8),
                      f'2026-07-{random_source.randint(1, 15):02}', random_source.randrange(20001) / 100)
                     for _ in range(180)]
        for window in (0, 1, 2, 6):
            witnesses, tx = self.build(transfers, isolated=(9,), window=window)
            details = temporal_details(tx, window)
            for gid, expected in details.items():
                with self.subTest(window=window, gid=gid):
                    actual = witnesses[gid]['temporal_evidence']
                    self.assertEqual(actual['matched_cents'] / 100, expected['temporal_matched_kzt'])
                    self.assertEqual(actual['strict_matched_cents'] / 100, expected['temporal_strict_matched_kzt'])
                    self.assertEqual(actual['same_day_matched_cents'] / 100, expected['temporal_same_day_matched_kzt'])
            self.assert_capacities_and_ordinals(witnesses, tx)

    def test_validated_shuffles_and_graph_insertion_order_preserve_witnesses(self):
        transfers = [(1, 2, '2026-07-01', 20.), (1, 2, '2026-07-01', 20.),
                     (2, 3, '2026-07-02', 10.), (2, 4, '2026-07-02', 30.)]
        nodes, edges, tx = fixture(transfers, isolated=(9,))
        frame = nodes[['gid']].copy()
        frame['cluster_id'] = frame.gid
        cfg = read_config()
        original = build_witnesses(build_graph(nodes, edges), frame, tx, cfg)
        sn, se, st = validate_tables(*(table.sample(frac=1, random_state=91) for table in (nodes, edges, tx)))
        reversed_graph = nx.DiGraph()
        reversed_graph.add_nodes_from(reversed(list(sn.gid)))
        reversed_graph.add_edges_from(reversed(list(zip(se.src, se.dst))))
        self.assertEqual(original, build_witnesses(reversed_graph, frame, st, cfg))


if __name__ == '__main__':
    unittest.main()
