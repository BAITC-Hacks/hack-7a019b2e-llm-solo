"""Verify pinned-runtime and input-permutation determinism on the supplied data."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CSV_NAMES = ('nodes_roles.csv', 'clusters.csv', 'top_nodes.csv')


def calculate(python, data, out):
    process = subprocess.run([str(python), '-m', 'moneygraph', '--data', str(data), '--out', str(out)],
                             cwd=ROOT, text=True, encoding='utf-8', capture_output=True, timeout=300)
    if process.returncode:
        raise RuntimeError(process.stderr)
    return json.loads(process.stdout)


def equivalent(left, right):
    hashes = {}
    for name in CSV_NAMES:
        content = (left / name).read_bytes()
        if content != (right / name).read_bytes():
            raise AssertionError(f'CSV differs: {name}')
        hashes[name] = hashlib.sha256(content).hexdigest()
    a, b = [json.loads((p / 'api-snapshot.json').read_text(encoding='utf-8')) for p in (left, right)]
    for key in ('nodes', 'edges', 'transactions', 'clusters', 'top', 'config', 'output_sha256'):
        if a[key] != b[key]:
            raise AssertionError(f'Snapshot content differs: {key}')
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-a', default=sys.executable)
    parser.add_argument('--python-b', required=True)
    parser.add_argument('--data', default=str(ROOT / 'data'))
    parser.add_argument('--out', default=str(ROOT / 'artifacts' / 'verification'))
    args = parser.parse_args()
    data, out = Path(args.data).resolve(), Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    a = calculate(Path(args.python_a).resolve(), data, out / 'python-a')
    b = calculate(Path(args.python_b).resolve(), data, out / 'python-b')
    hashes = equivalent(out / 'python-a', out / 'python-b')
    versions = []
    for exe in (args.python_a, args.python_b):
        versions.append(subprocess.check_output([str(Path(exe).resolve()), '-c',
                        'import sys; print(sys.version.split()[0])'], text=True).strip())
    checks = []
    tables = {name: pd.read_parquet(data / (name + '.parquet')) for name in ('nodes', 'edges', 'transactions')}
    for seed in (7, 91):
        with tempfile.TemporaryDirectory(prefix='shuffled-', dir=out) as temporary:
            shuffled = Path(temporary)
            for name, table in tables.items():
                table.sample(frac=1, random_state=seed).to_parquet(shuffled / (name + '.parquet'), index=False)
            calculate(Path(args.python_a).resolve(), shuffled, out / f'shuffle-{seed}')
            equivalent(out / 'python-a', out / f'shuffle-{seed}')
            checks.append(seed)
    result = dict(status='ok', python_versions=versions, csv_byte_identical=True,
                  derived_snapshot_identical=True, permutation_seeds=checks, output_sha256=hashes,
                  total_seconds=[a['total_seconds'], b['total_seconds']], role_counts=a['role_counts'])
    (out / 'reproducibility.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
