import json
from pathlib import Path
import tempfile
import unittest
import pandas as pd
from moneygraph.analysis import analyze, build_graph, classify, read_config, temporal_matching
from moneygraph.io import DataError, validate_tables
from moneygraph.report import export, validate_outputs


def fixture():
    nodes = pd.DataFrame({'gid': [10, 20, 30, 40, 50], 'depth': [0, 1, 2, 4, 0], 'is_seed': [True, False, False, False, True]})
    edges = pd.DataFrame({'src': [10, 20, 20], 'dst': [20, 30, 40], 'sum_kzt': [10000., 6000., 4000.], 'n_tx': [1, 1, 1], 'depth': [1, 2, 2]})
    tx = edges[['src', 'dst', 'sum_kzt']].copy()
    tx['date'] = ['2026-07-01', '2026-07-02', '2026-07-02']
    return nodes, edges, tx


class InputTests(unittest.TestCase):
    def test_valid_input_and_numeric_reconciliation(self):
        n, e, t = validate_tables(*fixture())
        self.assertEqual(len(n), 5)
        self.assertEqual(int(t._cents.sum()), 2000000)

    def test_bad_sums_and_counts_rejected(self):
        for column, value in [('sum_kzt', 12000.), ('n_tx', 2)]:
            n, e, t = fixture()
            e.loc[0, column] = value
            with self.assertRaises(DataError):
                validate_tables(n, e, t)

    def test_unknown_nodes_duplicates_nulls_and_missing_schema(self):
        for case in range(5):
            n, e, t = fixture()
            if case == 0: e.loc[0, 'src'] = 999
            if case == 1: n.loc[1, 'gid'] = 10
            if case == 2: e.loc[0, 'sum_kzt'] = float('nan')
            if case == 3: t = t.drop(columns='date')
            if case == 4: e = pd.concat([e, e.iloc[:1]])
            with self.subTest(case=case), self.assertRaises(DataError):
                validate_tables(n, e, t)

    def test_non_integer_gid_rejected_instead_of_rounding(self):
        n, e, t = fixture()
        n['gid'] = n.gid.astype(float)
        with self.assertRaises(DataError): validate_tables(n, e, t)

    def test_empty_nodes_rejected(self):
        n, e, t = fixture()
        with self.assertRaises(DataError): validate_tables(n.iloc[:0], e, t)

    def test_bad_dates_infinite_negative_money_and_seed(self):
        for case in range(5):
            n, e, t = fixture()
            if case == 0: t.loc[0, 'date'] = 'garbage'
            if case == 1: t.loc[0, 'sum_kzt'] = -1.
            if case == 2: t.loc[0, 'sum_kzt'] = float('inf')
            if case == 3: n.loc[0, 'is_seed'] = False
            if case == 4: t.loc[0, 'sum_kzt'] = 5000.123
            with self.subTest(case=case), self.assertRaises(DataError): validate_tables(n, e, t)


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.cfg = read_config()
        self.tables = validate_tables(*fixture())

    def test_isolates_and_truncation_preserved(self):
        graph, frame, clusters, top = analyze(*self.tables, self.cfg)
        validate_outputs(self.tables[0], frame, clusters, top)
        self.assertIn(50, graph)
        f = frame.set_index('gid')
        self.assertEqual(f.loc[50, 'role'], 'peripheral')
        self.assertEqual(f.loc[50, 'priority_score'], 0)
        self.assertNotEqual(f.loc[40, 'role'], 'terminal')
        self.assertIn('Обрыв', f.loc[40, 'evidence'])
        self.assertEqual(f.loc[30, 'role'], 'terminal')
        self.assertEqual(f.loc[20, 'role'], 'transit')
        self.assertEqual(clusters.n_seed.sum(), 2)

    def test_output_is_invariant_to_input_row_order(self):
        _, a, ca, ta = analyze(*self.tables, self.cfg)
        shuffled = validate_tables(*(table.sample(frac=1, random_state=7) for table in fixture()))
        _, b, cb, tb = analyze(*shuffled, self.cfg)
        pd.testing.assert_frame_equal(a, b)
        pd.testing.assert_frame_equal(ca, cb)
        pd.testing.assert_frame_equal(ta, tb)

    def test_all_isolates_dataset(self):
        n, e, t = fixture()
        tables = validate_tables(n, e.iloc[:0], t.iloc[:0])
        g, f, c, top = analyze(*tables, self.cfg)
        validate_outputs(n, f, c, top)
        self.assertEqual(len(c), len(n))
        self.assertTrue((f.role == 'peripheral').all())

    def test_role_motifs_and_seed_ratio_guard(self):
        base = dict(in_deg=1, out_deg=1, in_kzt=10000, out_kzt=10000, in_tx=1, out_tx=1,
                    pass_through=1., truncated_by_depth=False, is_seed=False, seed_reach=1,
                    other_clusters=0, betweenness=0., temporal_2d=1., active_days=3, depth=1)
        cases = [({}, 'transit'), ({'out_deg': 10, 'out_tx': 20}, 'distributor'),
                 ({'in_deg': 8, 'seed_reach': 3}, 'consolidator'),
                 ({'in_deg': 4, 'other_clusters': 3, 'betweenness': .1, 'seed_reach': 4}, 'coordinator'),
                 ({'out_deg': 0, 'out_kzt': 0, 'pass_through': 0}, 'terminal'),
                 ({'is_seed': True}, 'peripheral'),
                 ({'truncated_by_depth': True, 'depth': 4, 'out_deg': 0}, 'peripheral')]
        for updates, expected in cases:
            role, score, evidence, _ = classify({**base, **updates}, self.cfg, .01)
            with self.subTest(expected=expected):
                self.assertEqual(role, expected)
                self.assertLessEqual(len(evidence), 200)
                self.assertTrue(0 <= score <= 1)

    def test_cutoff_consolidator_does_not_claim_retention(self):
        row = dict(in_deg=10, out_deg=0, in_kzt=10000, out_kzt=0, in_tx=12, out_tx=0,
                   pass_through=0, truncated_by_depth=True, is_seed=False, seed_reach=4,
                   other_clusters=0, betweenness=0, temporal_2d=0, active_days=3, depth=4)
        role, score, evidence, _ = classify(row, self.cfg, .1)
        self.assertEqual(role, 'consolidator')
        self.assertLessEqual(score, .6)
        self.assertIn('удержание неизвестно', evidence)

    def test_temporal_matching_does_not_reuse_money(self):
        tx = pd.DataFrame({'src': [1, 2, 2, 1, 2], 'dst': [2, 3, 4, 2, 5],
                           'sum_kzt': [100., 80., 80., 100., 100.],
                           'date': pd.to_datetime(['2026-07-01', '2026-07-01', '2026-07-02', '2026-07-03', '2026-07-06'])})
        ratio, days = temporal_matching(tx)[2]
        self.assertEqual(ratio, .5)
        self.assertEqual(days, 4)

    def test_large_gid_preserved_in_csv_and_browser(self):
        n, e, t = fixture()
        offset = 100000000000000001
        n['gid'] += offset
        for df in (e, t):
            df['src'] += offset
            df['dst'] += offset
        n, e, t = validate_tables(n, e, t)
        graph, f, c, top = analyze(n, e, t, self.cfg)
        with tempfile.TemporaryDirectory() as directory:
            page = export(directory, graph, f, c, top, {}, self.cfg)
            text = page.read_text(encoding='utf-8')
            self.assertIn(f'"gid":"{offset+10}"', text)
            csv = pd.read_csv(Path(directory) / 'nodes_roles.csv', dtype={'gid': 'int64'})
            self.assertEqual(set(csv.gid), set(n.gid))
            self.assertNotIn('<script src=', text)

    def test_invalid_config(self):
        for updates in [{'max_depth': 3}, {'betweenness_samples': 0}, {'transit_ratio_min': 2}]:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'config.json'
                path.write_text(json.dumps({**self.cfg, **updates}), encoding='utf-8')
                with self.assertRaises(ValueError): read_config(path)


class DatasetAcceptance(unittest.TestCase):
    @unittest.skipUnless(Path('data/nodes.parquet').exists(), 'Официальный набор не приложен к Git; используйте локальный data/')
    def test_official_dataset_outputs(self):
        from moneygraph.__main__ import run
        from moneygraph.io import load
        with tempfile.TemporaryDirectory() as directory:
            summary, page = run('data', directory)
            n, _, _ = load('data')
            result = pd.read_csv(Path(directory) / 'nodes_roles.csv')
            self.assertEqual(set(result.gid), set(n.gid))
            self.assertEqual(len(result), 2248)
            self.assertEqual(summary['n_isolates'], 19)
            self.assertEqual(summary['n_truncated'], 444)
            self.assertEqual(summary['n_components'], 35)
            self.assertLess(summary['total_seconds'], 300)
            self.assertTrue(page.exists())


if __name__ == '__main__': unittest.main()
