"""Motifs must name real operations without upgrading structure to money tracing."""
import json
import unittest

import networkx as nx
import pandas as pd

from moneygraph.io import validate_tables
from moneygraph.observations import build_observations


def tables(transfers, isolated=()):
    tx = pd.DataFrame(transfers, columns=['src', 'dst', 'date', 'sum_kzt'])
    gids = sorted(set(tx.src) | set(tx.dst) | set(isolated))
    nodes = pd.DataFrame({'gid': gids, 'depth': [1] * len(gids),
                          'is_seed': [False] * len(gids)})
    edges = tx.groupby(['src', 'dst'], as_index=False).agg(
        sum_kzt=('sum_kzt', 'sum'), n_tx=('sum_kzt', 'size'))
    edges['depth'] = 1
    return nodes, edges, tx


def observe(transfers, isolated=()):
    nodes, edges, tx = validate_tables(*tables(transfers, isolated))
    return build_observations(build_graph(nodes, edges), tx), tx


def build_graph(nodes, edges):
    graph = nx.DiGraph()
    graph.add_nodes_from(nodes.gid)
    graph.add_edges_from(zip(edges.src, edges.dst))
    return graph


def fact_for(node, kind):
    return next(fact for fact in node['observed_facts'] if fact['kind'] == kind)


class ObservationTests(unittest.TestCase):
    def assert_references(self, observations, tx):
        for node in observations.values():
            for fact in node['observed_facts']:
                self.assertEqual(fact['transaction_row_ids'],
                                 [row['row_id'] for row in fact['transactions']])
                self.assertEqual(len(set(fact['transaction_row_ids'])),
                                 len(fact['transaction_row_ids']))
                for row in fact['transactions']:
                    actual = tx.iloc[row['row_id']]
                    self.assertEqual(row['src'], str(actual.src))
                    self.assertEqual(row['dst'], str(actual.dst))
                    self.assertEqual(row['date'], actual.date.date().isoformat())
                    self.assertEqual(row['cents'], int(actual['_cents']))
                    self.assertIs(type(row['cents']), int)

    def test_reversed_dates_are_only_a_structural_cycle(self):
        observations, tx = observe([(1, 2, '2026-07-03', 5000.),
                                    (2, 3, '2026-07-02', 5000.),
                                    (3, 1, '2026-07-01', 5000.)])
        self.assert_references(observations, tx)
        for node in observations.values():
            fact = fact_for(node, 'cycle')
            self.assertEqual(fact['members'], ['1', '2', '3'])
            self.assertEqual(fact['transaction_row_ids'], [2, 1, 0])
            self.assertFalse(fact['chronology_checked'])
            self.assertIn('временная совместимость', fact['limitations'][0])
            self.assertIn('внутри одного дня неизвестен', fact['limitations'][1])
            self.assertNotIn('Средства вернулись', node['motif_summary'])

    def test_repeat_amount_uses_exact_cents_not_rounded_whole_kzt(self):
        observations, tx = observe([(1, 2, '2026-07-01', 5000.01)] * 3
                                   + [(1, 2, '2026-07-02', 5000.02)] * 2)
        repeat = fact_for(observations[1], 'repeat_amount')
        self.assertEqual(repeat['cents'], 500001)
        self.assertEqual(repeat['count'], 3)
        self.assertEqual(repeat['total_cents'], 1500003)
        self.assertIn('5000.01', repeat['summary'])
        self.assertEqual(repeat['transaction_row_ids'], [0, 1, 2])
        self.assertEqual(len(repeat['transactions']), 3)
        self.assertEqual(fact_for(observations[2], 'repeat_amount')['direction'], 'in')
        self.assert_references(observations, tx)

    def test_burst_requires_five_operations_in_one_direction_on_one_day(self):
        # Three incoming plus two outgoing is not a five-transfer directional burst.
        base = [(1, 2, '2026-07-01', 5000.)] * 3 + [(2, 3, '2026-07-01', 6000.)] * 2
        observations, _ = observe(base)
        self.assertNotIn('burst', observations[2]['motifs'])
        observations, tx = observe(base + [(2, 3, '2026-07-02', 6000.)] * 5)
        burst = fact_for(observations[2], 'burst')
        self.assertEqual(burst['direction'], 'out')
        self.assertEqual(burst['date'], '2026-07-02')
        self.assertEqual(burst['count'], 5)
        self.assertEqual(burst['transaction_row_ids'], [5, 6, 7, 8, 9])
        self.assertEqual(burst['total_cents'], 3000000)
        self.assert_references(observations, tx)

    def test_self_transfer_policy_is_explicit_and_does_not_double_count(self):
        observations, tx = observe([(7, 7, '2026-07-01', 5000.)] * 5)
        self.assertEqual(observations[7]['motifs'], ['burst'])
        fact = fact_for(observations[7], 'burst')
        self.assertEqual(fact['count'], 5)
        self.assertEqual(fact['burst_count'], 1)
        self.assertEqual(fact['direction'], 'out')
        self.assertEqual(fact['self_transfer_count'], 5)
        self.assert_references(observations, tx)

    def test_every_node_including_isolates_has_an_empty_contract(self):
        observations, _ = observe([(1, 2, '2026-07-01', 5000.)], isolated=[99])
        self.assertEqual(set(observations), {1, 2, 99})
        for node in observations.values():
            self.assertEqual(node, dict(motifs=[], observed_facts=[], motif_summary=''))

    def test_cycle_bounds_and_multiple_examples_have_deterministic_counts(self):
        transfers = [(1, 2, '2026-07-01', 5000.), (2, 1, '2026-07-02', 5000.),
                     (1, 3, '2026-07-01', 5000.), (3, 1, '2026-07-02', 5000.)]
        # A disjoint seven-node cycle is outside the documented bound.
        transfers += [(gid, gid + 1 if gid < 16 else 10, '2026-07-01', 5000.)
                      for gid in range(10, 17)]
        observations, _ = observe(transfers)
        cycle = fact_for(observations[1], 'cycle')
        self.assertEqual(cycle['cycle_count'], 2)
        self.assertEqual(cycle['members'], ['1', '2'])
        for gid in range(10, 17):
            self.assertNotIn('cycle', observations[gid]['motifs'])

    def test_multiple_repeats_and_bursts_keep_one_example_and_count_groups(self):
        transfers = [(1, 2, '2026-07-01', 5000.01)] * 5
        transfers += [(1, 3, '2026-07-02', 6000.01)] * 6
        observations, tx = observe(transfers)
        self.assertEqual(observations[1]['motifs'], ['repeat_amount', 'burst'])
        repeat = fact_for(observations[1], 'repeat_amount')
        self.assertEqual(repeat['group_count'], 2)
        self.assertEqual(repeat['dst'], '3')
        self.assertEqual(repeat['count'], 6)
        burst = fact_for(observations[1], 'burst')
        self.assertEqual(burst['burst_count'], 2)
        self.assertEqual(burst['date'], '2026-07-02')
        self.assert_references(observations, tx)

    def test_validated_shuffle_and_graph_insertion_order_do_not_change_facts(self):
        big = 2**53 + 17
        raw = tables([(big, big + 1, '2026-07-01', 5000.01)] * 5
                     + [(big + 1, big + 2, '2026-07-03', 10000.),
                        (big + 2, big, '2026-07-02', 10000.),
                        (big, big + 3, '2026-07-02', 6000.01)] * 3, [big + 4])
        nodes, edges, tx = validate_tables(*raw)
        expected = build_observations(build_graph(nodes, edges), tx)
        for seed in (7, 91):
            n, e, t = validate_tables(*(table.sample(frac=1, random_state=seed) for table in raw))
            graph = nx.DiGraph()
            graph.add_nodes_from(reversed(n.gid.tolist()))
            graph.add_edges_from(reversed(list(zip(e.src, e.dst))))
            actual = build_observations(graph, t)
            self.assertEqual(json.dumps(expected), json.dumps(actual))
            self.assert_references(actual, t)
        self.assertEqual(fact_for(expected[big], 'cycle')['members'][0], str(big))


if __name__ == '__main__':
    unittest.main()
