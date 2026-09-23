"""Strict input validation. Adapted from the organizers' starter, with numeric reconciliation."""
from pathlib import Path
import hashlib
import numpy as np
import pandas as pd


class DataError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DataError(message)


def _integers(frame, columns, name):
    for col in columns:
        require(pd.api.types.is_integer_dtype(frame[col]) and not pd.api.types.is_bool_dtype(frame[col]),
                f'{name}.{col}: ожидается целочисленный тип, без преобразования через float')


def validate_tables(nodes, edges, tx):
    tables = [(nodes, 'nodes', ['gid', 'depth', 'is_seed']),
              (edges, 'edges', ['src', 'dst', 'sum_kzt', 'n_tx', 'depth']),
              (tx, 'transactions', ['src', 'dst', 'date', 'sum_kzt'])]
    for frame, name, cols in tables:
        require(set(cols).issubset(frame.columns), f'{name}: нужны колонки {cols}')
        require(not frame[cols].isna().any().any(), f'{name}: пустые обязательные значения')
    require(len(nodes) > 0, 'nodes: пустой список узлов')
    _integers(nodes, ['gid', 'depth'], 'nodes')
    _integers(edges, ['src', 'dst', 'n_tx', 'depth'], 'edges')
    _integers(tx, ['src', 'dst'], 'transactions')
    require(nodes.gid.is_unique, 'nodes: повторяющийся gid')
    require(pd.api.types.is_bool_dtype(nodes.is_seed), 'nodes.is_seed: ожидается bool')
    require(nodes.depth.between(0, 4).all(), 'nodes.depth: ожидается 0–4')
    require((nodes.is_seed == (nodes.depth == 0)).all(), 'nodes: is_seed не соответствует depth=0')
    require(edges.depth.between(1, 4).all(), 'edges.depth: ожидается 1–4')
    require((edges.n_tx > 0).all(), 'edges.n_tx: ожидается положительное число')
    require(not edges.duplicated(['src', 'dst']).any(), 'edges: дубликаты пар src,dst')
    gids = set(nodes.gid)
    for frame, name in [(edges, 'edges'), (tx, 'transactions')]:
        require(set(frame.src).union(frame.dst).issubset(gids), f'{name}: неизвестный gid')
        require(pd.api.types.is_numeric_dtype(frame.sum_kzt), f'{name}.sum_kzt: ожидается число')
        require(np.isfinite(frame.sum_kzt).all() and (frame.sum_kzt >= 0).all(), f'{name}: недопустимая сумма')
        cents = frame.sum_kzt.to_numpy(dtype=float) * 100
        require((np.abs(cents - np.rint(cents)) < 0.001).all(), f'{name}: точность суммы больше двух знаков')
        require((cents < 2**53).all(), f'{name}: сумма вне безопасного диапазона')
    tx = tx.copy()
    tx['date'] = pd.to_datetime(tx.date, errors='coerce', format='ISO8601')
    require(tx.date.notna().all(), 'transactions.date: неверная дата')
    # Compare exact integer minor units, rather than trusting only matching pairs.
    tx['_cents'] = np.rint(tx.sum_kzt * 100).astype('int64')
    edges = edges.copy()
    edges['_cents'] = np.rint(edges.sum_kzt * 100).astype('int64')
    agg = tx.groupby(['src', 'dst'], sort=True).agg(cents=('_cents', 'sum'), count=('_cents', 'size')).reset_index()
    merged = edges.merge(agg, on=['src', 'dst'], how='outer', indicator=True)
    require((merged._merge == 'both').all(), 'edges/transactions: пары не совпадают')
    require((merged._cents == merged.cents).all(), 'edges/transactions: суммы не совпадают')
    require((merged.n_tx == merged['count']).all(), 'edges/transactions: число переводов не совпадает')
    return (nodes.sort_values('gid').reset_index(drop=True),
            edges.sort_values(['src', 'dst']).reset_index(drop=True),
            tx.sort_values(['date', 'src', 'dst', '_cents']).reset_index(drop=True))


def load(data_dir):
    path = Path(data_dir)
    try:
        tables = [pd.read_parquet(path / f'{name}.parquet') for name in ('nodes', 'edges', 'transactions')]
    except (OSError, ValueError) as exc:
        raise DataError(f'Не удалось прочитать три Parquet из {path}: {exc}') from exc
    return validate_tables(*tables)


def input_hashes(data_dir):
    return {f'{name}.parquet': hashlib.sha256((Path(data_dir) / f'{name}.parquet').read_bytes()).hexdigest()
            for name in ('nodes', 'edges', 'transactions')}
