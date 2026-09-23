"""Malformed backend inputs must fail clearly before producing exports."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import warnings

import pandas as pd

from moneygraph.analysis import read_config
from moneygraph.io import DataError, validate_tables


def two_nodes(gids, dtype='int64'):
    nodes = pd.DataFrame({'gid': pd.Series(gids, dtype=dtype), 'depth': [0, 1],
                          'is_seed': [True, False]})
    edges = pd.DataFrame({'src': pd.Series([gids[0]], dtype=dtype),
                          'dst': pd.Series([gids[1]], dtype=dtype),
                          'sum_kzt': [5000.], 'n_tx': [1], 'depth': [1]})
    tx = edges[['src', 'dst', 'sum_kzt']].copy()
    tx['date'] = '2026-07-01'
    return nodes, edges, tx


class BackendValidationTests(unittest.TestCase):
    def test_negative_and_out_of_int64_identifiers_are_rejected(self):
        for gids, dtype in (([-1, 2], 'int64'), ([2**63, 2**63 + 1], 'uint64')):
            with self.subTest(gids=gids), self.assertRaisesRegex(DataError, 'int64'):
                validate_tables(*two_nodes(gids, dtype))

    def test_identifier_limits_are_accepted_without_rounding(self):
        for gids, dtype in (([0, 2**63 - 1], 'int64'),
                            ([100000000000000001, 100000000000000002], 'uint64')):
            with self.subTest(dtype=dtype):
                nodes, edges, tx = validate_tables(*two_nodes(gids, dtype))
                self.assertEqual(nodes.gid.tolist(), gids)
                self.assertEqual(edges.src.tolist(), [gids[0]])
                self.assertEqual(edges.dst.tolist(), [gids[1]])
                self.assertEqual(tx.src.tolist(), [gids[0]])

    def test_config_shapes_and_boolean_weights_are_rejected(self):
        cfg = read_config()
        cases = [None, [], [{'bad': 'shape'}], True, 1,
                 {**cfg, 'priority_weights': None}, {**cfg, 'priority_weights': []},
                 {**cfg, 'priority_weights': list(cfg['priority_weights'])},
                 {**cfg, 'priority_weights': dict.fromkeys(cfg['priority_weights'], False)},
                 {**cfg, 'priority_weights': {'seed_reach': True, 'flow': False,
                       'bridge': False, 'fan': False, 'role_support': False}},
                 {**cfg, 'louvain_resolution': 10**500},
                 {**cfg, 'priority_weights': {**cfg['priority_weights'], 'flow': 10**500}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            for value in cases:
                with self.subTest(value=str(value)[:100]):
                    path.write_text(json.dumps(value), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        read_config(path)

    def test_mixed_timezones_fail_validation_without_later_dt_crash(self):
        nodes, edges, tx = two_nodes([1, 2])
        tx = pd.concat([tx, tx], ignore_index=True)
        tx['date'] = ['2026-07-01T00:00:00+00:00', '2026-07-02T00:00:00+03:00']
        edges['sum_kzt'] = 10000.
        edges['n_tx'] = 2
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            with self.assertRaisesRegex(DataError, 'часовые пояса'):
                validate_tables(nodes, edges, tx)
        self.assertEqual(caught, [])

    def test_numeric_dates_cannot_be_interpreted_as_nanoseconds(self):
        for date in (0, 1782864000000000000, 1782864000.0, True):
            with self.subTest(date=date):
                nodes, edges, tx = two_nodes([1, 2])
                tx['date'] = date
                with self.assertRaisesRegex(DataError, 'числовая дата'):
                    validate_tables(nodes, edges, tx)
        nodes, edges, tx = two_nodes([1, 2])
        tx = pd.concat([tx, tx], ignore_index=True)
        tx['date'] = pd.Series(['2026-07-01', 1], dtype='object')
        edges['sum_kzt'] = 10000.
        edges['n_tx'] = 2
        with self.assertRaisesRegex(DataError, 'числовая дата'):
            validate_tables(nodes, edges, tx)

    def test_cli_bad_config_reports_json_without_traceback_or_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'bad.json'
            config.write_text(json.dumps({**read_config(), 'priority_weights': None}), encoding='utf-8')
            out = root / 'out'
            result = subprocess.run([sys.executable, '-m', 'moneygraph', '--config', str(config),
                                     '--data', str(root / 'missing'), '--out', str(out)],
                                    capture_output=True, encoding='utf-8', timeout=30)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, '')
            self.assertIn('error', json.loads(result.stderr))
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
