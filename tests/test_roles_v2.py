"""Regression contracts for temporal role evidence and exact centrality."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import networkx as nx
import pandas as pd

from moneygraph.analysis import analyze, read_config, temporal_details, temporal_matching
from moneygraph.io import validate_tables


def tables(transfers, isolated=()):
    tx = pd.DataFrame(transfers, columns=['src', 'dst', 'date', 'sum_kzt'])
    gids = sorted(set(tx.src) | set(tx.dst) | set(isolated))
    nodes = pd.DataFrame({'gid': gids, 'depth': [1] * len(gids), 'is_seed': [False] * len(gids)})
    edges = tx.groupby(['src', 'dst'], as_index=False).agg(sum_kzt=('sum_kzt', 'sum'), n_tx=('sum_kzt', 'size'))
    edges['depth'] = 1
    return validate_tables(nodes, edges, tx)


class TemporalRoleTests(unittest.TestCase):
    def setUp(self):
        self.cfg = read_config()

    def node(self, transfers, gid=2, cfg=None):
        _, frame, _, _ = analyze(*tables(transfers), cfg or self.cfg)
        return frame.set_index('gid').loc[gid]

    def test_future_input_cannot_explain_past_output(self):
        row = self.node([(2, 3, '2026-07-01', 100.), (1, 2, '2026-07-02', 100.)])
        self.assertEqual(row.temporal_matched_kzt, 0)
        self.assertNotEqual(row.role, 'transit')
        self.assertNotIn('transit', row.matched_roles)
        self.assertEqual(row.peripheral_reason, 'no_rule')

    def test_one_input_can_fund_several_outputs(self):
        row = self.node([(1, 2, '2026-07-01', 100.),
                         (2, 3, '2026-07-02', 40.), (2, 4, '2026-07-03', 60.)])
        self.assertEqual(row.role, 'transit')
        self.assertEqual(row.temporal_matched_kzt, 100)
        self.assertEqual(row.temporal_in_fraction, 1)
        self.assertEqual(row.temporal_out_fraction, 1)
        self.assertEqual(row.temporal_strict_matched_kzt, 100)

    def test_several_inputs_can_fund_one_output(self):
        row = self.node([(1, 2, '2026-07-01', 40.), (4, 2, '2026-07-02', 60.),
                         (2, 3, '2026-07-03', 100.)])
        self.assertEqual(row.role, 'transit')
        self.assertEqual(row.temporal_matched_kzt, 100)

    def test_volume_is_not_reused_and_both_denominators_matter(self):
        # Monthly ratio is allowed; only 70 of 100 incoming and 120 outgoing
        # can be paired. The earlier 50 outgoing cannot be explained by later input.
        row = self.node([(2, 3, '2026-07-01', 50.), (1, 2, '2026-07-02', 100.),
                         (2, 3, '2026-07-03', 35.), (2, 4, '2026-07-03', 35.)])
        self.assertEqual(row.temporal_matched_kzt, 70)
        self.assertAlmostEqual(row.temporal_in_fraction, .7)
        self.assertAlmostEqual(row.temporal_out_fraction, 70 / 120)
        self.assertNotEqual(row.role, 'transit')
        # Enough input is matched, but less than 80% of outgoing is explained.
        row = self.node([(2, 3, '2026-07-01', 35.), (1, 2, '2026-07-02', 100.),
                         (2, 3, '2026-07-03', 85.)])
        self.assertAlmostEqual(row.temporal_in_fraction, .85)
        self.assertLess(row.temporal_out_fraction, .8)
        self.assertNotEqual(row.role, 'transit')
        tx = tables([(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-02', 80.),
                     (2, 4, '2026-07-03', 80.)])[2]
        self.assertEqual(temporal_details(tx)[2]['temporal_matched_kzt'], 100)

    def test_same_day_is_a_separate_ambiguous_scenario(self):
        row = self.node([(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-01', 100.)])
        self.assertEqual(row.role, 'transit')
        self.assertEqual(row.temporal_matched_kzt, 100)
        self.assertEqual(row.temporal_same_day_matched_kzt, 100)
        self.assertEqual(row.temporal_strict_matched_kzt, 0)
        self.assertIn('порядок неизвестен', row.evidence)

    def test_expired_input_cannot_match_and_window_is_configurable(self):
        transfers = [(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-04', 100.)]
        self.assertNotEqual(self.node(transfers).role, 'transit')
        row = self.node(transfers, cfg={**self.cfg, 'transit_window_days': 3})
        self.assertEqual(row.role, 'transit')
        self.assertEqual(row.temporal_window_days, 3)
        self.assertEqual(row.temporal_2d, 0)
        self.assertEqual(temporal_matching(tables(transfers)[2])[2], (0., 2))

    def test_self_transfers_do_not_inflate_temporal_denominators(self):
        row = self.node([(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-02', 100.),
                         (2, 2, '2026-07-01', 900.)])
        self.assertEqual(row.in_kzt, 1000)
        self.assertEqual(row.out_kzt, 1000)
        self.assertEqual(row.external_in_kzt, 100)
        self.assertEqual(row.external_out_kzt, 100)
        self.assertEqual(row.temporal_matched_kzt, 100)
        self.assertEqual(row.temporal_in_fraction, 1)
        self.assertEqual(row.temporal_out_fraction, 1)

    def test_peripheral_reasons_and_guards(self):
        n, e, t = tables([(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-02', 100.)], isolated=(9,))
        n.loc[n.gid == 2, ['depth', 'is_seed']] = [0, True]
        n.loc[n.gid == 3, 'depth'] = 4
        _, frame, _, _ = analyze(n, e, t, self.cfg)
        indexed = frame.set_index('gid')
        self.assertEqual(indexed.loc[9, 'peripheral_reason'], 'isolated')
        self.assertEqual(indexed.loc[2, 'peripheral_reason'], 'incomplete_seed')
        self.assertEqual(indexed.loc[3, 'peripheral_reason'], 'truncated')
        self.assertEqual(indexed.loc[1, 'peripheral_reason'], 'no_observed_input')
        self.assertTrue(frame.evidence.str.len().between(1, 200).all())


class CentralityConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = read_config()

    def test_exact_is_default_and_passes_none_to_networkx(self):
        data = tables([(1, 2, '2026-07-01', 100.), (2, 3, '2026-07-02', 100.)])
        self.assertEqual(self.cfg['centrality_mode'], 'exact')
        with patch('moneygraph.analysis.nx.betweenness_centrality', wraps=nx.betweenness_centrality) as centrality:
            analyze(*data, self.cfg)
            self.assertIsNone(centrality.call_args.kwargs['k'])
        with patch('moneygraph.analysis.nx.betweenness_centrality', wraps=nx.betweenness_centrality) as centrality:
            analyze(*data, {**self.cfg, 'centrality_mode': 'approximate', 'betweenness_samples': 2})
            self.assertEqual(centrality.call_args.kwargs['k'], 2)

    def test_shuffle_invariance_exceeds_sample_count(self):
        count = self.cfg['betweenness_samples'] + 5
        data = tables([(i, i + 1, '2026-07-01', float(100 + i)) for i in range(1, count)])
        _, a, ca, ta = analyze(*data, self.cfg)
        shuffled = validate_tables(*(table.sample(frac=1, random_state=29) for table in data))
        _, b, cb, tb = analyze(*shuffled, self.cfg)
        pd.testing.assert_frame_equal(a, b)
        pd.testing.assert_frame_equal(ca, cb)
        pd.testing.assert_frame_equal(ta, tb)

    def test_invalid_new_configuration_is_rejected(self):
        for updates in ({'centrality_mode': 'auto'}, {'centrality_mode': None},
                        {'transit_window_days': -1}, {'transit_window_days': 1.5},
                        {'transit_window_days': True}, {'transit_min_matched_fraction': 0},
                        {'transit_min_matched_fraction': 1.1}, {'transit_min_matched_fraction': float('nan')}):
            with self.subTest(updates=updates), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'roles.json'
                path.write_text(json.dumps({**self.cfg, **updates}), encoding='utf-8')
                with self.assertRaises(ValueError):
                    read_config(path)


if __name__ == '__main__':
    unittest.main()
