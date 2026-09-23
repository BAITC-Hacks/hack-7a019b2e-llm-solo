import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from moneygraph.__main__ import run
from test_moneygraph import fixture


class SnapshotTests(unittest.TestCase):
    def prepare(self, root, shuffled=False):
        n, e, tx = fixture()
        # The same transaction may genuinely occur twice. Preserve both.
        tx = pd.concat([tx, tx.iloc[[0]]], ignore_index=True)
        e.loc[0, 'sum_kzt'] *= 2
        e.loc[0, 'n_tx'] *= 2
        offset = 100000000000000001
        n['gid'] += offset
        for table in (e, tx):
            table['src'] += offset
            table['dst'] += offset
        for name, table in zip(('nodes', 'edges', 'transactions'), (n, e, tx)):
            if shuffled:
                table = table.sample(frac=1, random_state=91)
            table.to_parquet(root / (name + '.parquet'), index=False)

    def test_snapshot_covers_data_duplicates_and_binds_csvs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            out = root / 'out'
            run(root, out)
            snapshot = json.loads((out / 'graph.json').read_text(encoding='utf-8'))
            self.assertEqual(snapshot['schema_version'], 1)
            self.assertEqual(len(snapshot['snapshot_id']), 64)
            self.assertEqual(len(snapshot['nodes']), 5)
            self.assertEqual(len(snapshot['transactions']), 4)
            self.assertEqual([t['row_id'] for t in snapshot['transactions']], list(range(4)))
            first, second = snapshot['transactions'][:2]
            self.assertNotEqual(first['row_id'], second['row_id'])
            self.assertEqual({k: v for k, v in first.items() if k != 'row_id'},
                             {k: v for k, v in second.items() if k != 'row_id'})
            for node in snapshot['nodes']:
                self.assertIsInstance(node['gid'], str)
            for edge in snapshot['edges']:
                self.assertIsInstance(edge['src'], str)
                self.assertIsInstance(edge['dst'], str)
                self.assertIn(edge['depth'], (1, 2))
            for name, digest in snapshot['output_sha256'].items():
                self.assertEqual(hashlib.sha256((out / name).read_bytes()).hexdigest(), digest)
            roles = pd.read_csv(out / 'nodes_roles.csv', dtype={'gid': str}).set_index('gid')
            for node in snapshot['nodes']:
                self.assertEqual(roles.loc[node['gid'], 'role'], node['role'])
                self.assertEqual(roles.loc[node['gid'], 'evidence'], node['evidence'])
                self.assertEqual(roles.loc[node['gid'], 'priority_score'], node['priority_score'])
            self.assertEqual(sum(t['cents'] for t in snapshot['transactions']),
                             sum(e['cents'] for e in snapshot['edges']))
            for name in ('index.html', 'app.js', 'data.js', 'styles.css'):
                self.assertTrue((out / 'frontend' / name).is_file(), name)

    def test_export_ordinals_and_csvs_are_invariant_to_input_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.prepare(root)
            run(root, root / 'a')
            self.prepare(root, shuffled=True)
            run(root, root / 'b')
            a, b = [json.loads((root / child / 'graph.json').read_text(encoding='utf-8')) for child in ('a', 'b')]
            for key in ('nodes', 'edges', 'transactions', 'clusters', 'top', 'output_sha256'):
                self.assertEqual(a[key], b[key], key)
            # Input byte hashes may differ after a Parquet rewrite; derived data must not.
            for name in a['output_sha256']:
                self.assertEqual((root / 'a' / name).read_bytes(), (root / 'b' / name).read_bytes())
