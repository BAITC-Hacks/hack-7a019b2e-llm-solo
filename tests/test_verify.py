"""Acceptance must fail for incomplete, stale and inconsistent on-disk exports."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import pandas as pd

from moneygraph.__main__ import run
from moneygraph.verify import verify
from test_moneygraph import fixture


class ExportAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.out = self.root / 'out'
        nodes, edges, tx = fixture()
        tx = pd.concat([tx, tx.iloc[[0]]], ignore_index=True)
        edges.loc[0, 'sum_kzt'] *= 2
        edges.loc[0, 'n_tx'] *= 2
        offset = 100000000000000001
        nodes['gid'] += offset
        for table in (edges, tx):
            table['src'] += offset
            table['dst'] += offset
        for name, table in zip(('nodes', 'edges', 'transactions'), (nodes, edges, tx)):
            table.to_parquet(self.root / (name + '.parquet'), index=False)
        run(self.root, self.out)

    def change_snapshot(self, change):
        path = self.out / 'graph.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        change(value)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def test_valid_bundle_is_verified_without_writing_files(self):
        before = {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        result = verify(self.root, self.out)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual((result['n_nodes'], result['n_transactions'], result['n_isolates']), (5, 4, 1))
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob('*') if path.is_file()})

    def test_deleted_duplicate_transaction_is_detected(self):
        self.change_snapshot(lambda data: data['transactions'].pop(0))
        with self.assertRaisesRegex(ValueError, 'операции'):
            verify(self.root, self.out)

    def test_loss_of_gid_precision_is_detected(self):
        self.change_snapshot(lambda data: data['nodes'][0].update(gid=float(data['nodes'][0]['gid'])))
        with self.assertRaisesRegex(ValueError, 'gid'):
            verify(self.root, self.out)

    def test_wrong_node_money_is_detected(self):
        self.change_snapshot(lambda data: data['nodes'][0].update(out_kzt=0))
        with self.assertRaisesRegex(ValueError, 'оборот'):
            verify(self.root, self.out)

    def test_booleans_cannot_replace_numeric_json_fields(self):
        original = (self.out / 'graph.json').read_bytes()
        changes = [lambda data: data.update(schema_version=True),
                   lambda data: data['transactions'][1].update(row_id=True),
                   lambda data: data['edges'][1].update(n_tx=True)]
        for change in changes:
            with self.subTest(change=change):
                (self.out / 'graph.json').write_bytes(original)
                self.change_snapshot(change)
                with self.assertRaises(ValueError):
                    verify(self.root, self.out)

    def test_empty_html_is_rejected(self):
        (self.out / 'report.html').write_text('', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'HTML'):
            verify(self.root, self.out)

    def test_csv_modification_is_detected(self):
        path = self.out / 'top_nodes.csv'
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaisesRegex(ValueError, 'CSV и graph.json'):
            verify(self.root, self.out)

    def test_changed_source_files_are_detected(self):
        path = self.root / 'transactions.parquet'
        table = pd.read_parquet(path)
        table['date'] = '2026-07-15'
        table.to_parquet(path, index=False)
        with self.assertRaisesRegex(ValueError, 'исходные файлы изменились'):
            verify(self.root, self.out)

    def test_corrupt_or_missing_export_cli_fails_without_traceback(self):
        for value in ('{invalid', 'null', '{}', '{"schema_version":NaN}'):
            with self.subTest(value=value):
                (self.out / 'graph.json').write_text(value, encoding='utf-8')
                process = subprocess.run([sys.executable, '-m', 'moneygraph.verify', '--data', str(self.root),
                                          '--out', str(self.out)], capture_output=True, text=True, encoding='utf-8', timeout=20)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(process.stdout, '')
                self.assertNotIn('Traceback', process.stderr)
                self.assertEqual(json.loads(process.stderr)['status'], 'error')

    def test_empty_transfers_and_all_isolates_are_valid(self):
        for name in ('edges', 'transactions'):
            path = self.root / (name + '.parquet')
            pd.read_parquet(path).iloc[:0].to_parquet(path, index=False)
        run(self.root, self.out)
        result = verify(self.root, self.out)
        self.assertEqual(result['n_isolates'], 5)
        self.assertEqual(result['n_transactions'], 0)


if __name__ == '__main__':
    unittest.main()
