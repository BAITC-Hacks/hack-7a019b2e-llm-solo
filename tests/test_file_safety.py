import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from moneygraph.files import atomic_text
from moneygraph.io import input_hashes


class FileSafetyTests(unittest.TestCase):
    def test_failed_replacement_keeps_previous_result_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run.json'
            path.write_text('previous', encoding='utf-8')
            with patch('moneygraph.files.os.replace', side_effect=OSError('simulated failure')):
                with self.assertRaises(OSError):
                    atomic_text(path, 'next')
            self.assertEqual(path.read_text(), 'previous')
            self.assertEqual([file.name for file in path.parent.iterdir()], ['run.json'])

    def test_streamed_hash_matches_file_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b'chunk-test' * 120000
            for name in ('nodes', 'edges', 'transactions'):
                (root / (name + '.parquet')).write_bytes(content)
            self.assertEqual(set(input_hashes(root).values()), {hashlib.sha256(content).hexdigest()})
