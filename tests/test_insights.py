"""Sensitivity is conditional; component views must not hide small networks."""
from copy import deepcopy
import unittest

import networkx as nx
import pandas as pd

from moneygraph.analysis import classify, read_config
from moneygraph.insights import build_overviews, role_sensitivity, sensitivity_profiles


def role_row(gid=100000000000000001, **updates):
    row = dict(
        gid=gid, in_deg=1, out_deg=1, in_kzt=10000., out_kzt=10000.,
        external_in_kzt=10000., in_tx=1, out_tx=1, pass_through=1.,
        truncated_by_depth=False, is_seed=False, seed_reach=1,
        other_clusters=0, betweenness=0., temporal_2d=.8,
        temporal_in_fraction=.8, temporal_out_fraction=.8,
        temporal_strict_in_fraction=.8, temporal_strict_out_fraction=.8,
        active_days=3, depth=1, bridge_evidence=None,
    )
    return {**row, **updates}


class SensitivityTests(unittest.TestCase):
    def test_boundary_is_not_falsely_reported_stable(self):
        cfg = read_config()
        rows = [role_row(), role_row(gid=100000000000000002,
                temporal_in_fraction=1., temporal_out_fraction=1.)]
        frame = pd.DataFrame(rows)
        result = role_sensitivity(frame, cfg)
        first, second = (result[row['gid']] for row in rows)
        self.assertEqual(first['profiles'], {
            'base': 'transit', 'relaxed': 'transit', 'strict': 'peripheral'})
        self.assertFalse(first['stable'])
        self.assertTrue(second['stable'])
        self.assertIn('внутри дня', first['limitation'])
        self.assertIn('фиксированы', first['scope'])
        for row in rows:
            self.assertEqual(result[row['gid']]['profiles']['base'], classify(row, cfg, 0.0)[0])

    def test_profiles_respect_custom_config_without_mutation(self):
        cfg = read_config()
        cfg.update(transit_ratio_min=.5, transit_ratio_max=1.5,
                   transit_window_days=7, transit_min_matched_fraction=.95,
                   coordinator_centrality_quantile=.98, consolidator_min_seeds=0)
        original = deepcopy(cfg)
        profiles = sensitivity_profiles(cfg)
        self.assertEqual(cfg, original)
        self.assertEqual(profiles['base'], cfg)
        self.assertAlmostEqual(profiles['relaxed']['transit_ratio_min'], .4)
        self.assertAlmostEqual(profiles['strict']['transit_ratio_max'], 1.4)
        self.assertEqual(profiles['strict']['transit_min_matched_fraction'], 1.)
        self.assertEqual(profiles['strict']['coordinator_centrality_quantile'], 1.)
        for profile in profiles.values():
            self.assertEqual(profile['transit_window_days'], 7)
            self.assertEqual(profile['consolidator_min_seeds'], 0)
            for fixed in ('random_seed', 'centrality_mode', 'louvain_resolution', 'priority_weights'):
                self.assertEqual(profile[fixed], original[fixed])
        profiles['relaxed']['priority_weights']['flow'] = -1
        self.assertEqual(profiles['base']['priority_weights'], original['priority_weights'])
        self.assertEqual(cfg, original)

    def test_stability_is_invariant_to_row_order_and_preserves_input(self):
        frame = pd.DataFrame([role_row(gid=3), role_row(gid=1), role_row(gid=2)])
        original = deepcopy(frame)
        expected = role_sensitivity(frame, read_config())
        self.assertEqual(expected, role_sensitivity(frame.sample(frac=1, random_state=9), read_config()))
        self.assertEqual(list(expected), [1, 2, 3])
        pd.testing.assert_frame_equal(frame, original)


class OverviewTests(unittest.TestCase):
    def fixture(self):
        big = [100000000000000000 + n for n in range(1, 52)]
        small = [100000000000000100, 100000000000000101]
        isolate = 100000000000000200
        rows = [dict(gid=gid, component_id=0, is_seed=i == 0,
                     role='distributor', priority_score=1., motifs=[])
                for i, gid in enumerate(big)]
        rows += [dict(gid=gid, component_id=1, is_seed=i == 0,
                      role='transit', priority_score=.1, motifs=['repeat_amount', 'repeat_amount'])
                 for i, gid in enumerate(small)]
        rows.append(dict(gid=isolate, component_id=2, is_seed=True,
                         role='peripheral', priority_score=0., motifs=[]))
        graph = nx.DiGraph()
        graph.add_nodes_from(row['gid'] for row in rows)
        graph.add_edges_from((a, b, {'cents': 101}) for a, b in zip(big, big[1:]))
        graph.add_edge(*small, cents=1023)
        return graph, pd.DataFrame(rows), small, isolate

    def test_small_component_has_its_own_top_and_isolates_survive(self):
        graph, frame, small, isolate = self.fixture()
        global_top = set(frame.sort_values(['priority_score', 'gid'], ascending=[False, True]).head(50).gid)
        self.assertTrue(global_top.isdisjoint(small))
        result = build_overviews(graph, frame)
        components = {row['component_id']: row for row in result['components']}
        self.assertEqual(components[1]['top_gids'], list(map(str, small)))
        self.assertEqual(components[1]['sum_kzt_internal'], 10.23)
        self.assertEqual(components[1]['n_edges'], 1)
        self.assertEqual(components[1]['n_seed'], 1)
        self.assertEqual(components[1]['role_counts'], {'transit': 2})
        self.assertEqual(components[1]['motif_counts']['repeat_amount'], 2)
        self.assertEqual(components[2]['top_gids'], [str(isolate)])
        self.assertEqual(components[2]['n_edges'], 0)
        self.assertEqual(result['motif_rankings']['repeat_amount'], {'n_nodes': 2, 'top_gids': list(map(str, small))})
        self.assertEqual(result['motif_rankings']['cycle'], {'n_nodes': 0, 'top_gids': []})
        self.assertEqual(len(components[0]['top_gids']), 10)
        self.assertEqual(sum(c['n_nodes'] for c in components.values()), len(frame))

    def test_overviews_ignore_row_and_graph_insertion_order(self):
        graph, frame, _, _ = self.fixture()
        reversed_graph = nx.DiGraph()
        reversed_graph.add_nodes_from(reversed(list(graph.nodes)))
        reversed_graph.add_edges_from(reversed(list(graph.edges(data=True))))
        self.assertEqual(build_overviews(graph, frame), build_overviews(
            reversed_graph, frame.sample(frac=1, random_state=11)))

    def test_invalid_coverage_and_money_are_rejected(self):
        graph, frame, _, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, 'cover every graph node'):
            build_overviews(graph, frame.iloc[:-1])
        src, dst = next(iter(graph.edges))
        graph[src][dst]['cents'] = 1.1
        with self.assertRaisesRegex(ValueError, 'nonnegative integer'):
            build_overviews(graph, frame)


if __name__ == '__main__':
    unittest.main()
