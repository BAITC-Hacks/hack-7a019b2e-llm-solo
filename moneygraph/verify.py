"""Read-only acceptance of a local export against its original Parquet snapshot."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

import networkx as nx
import numpy as np
import pandas as pd

from .analysis import build_graph, read_config
from .io import input_hashes, load, require
from .report import CLUSTER_COLUMNS, ROLE_COLUMNS, TOP_COLUMNS, validate_outputs
from .snapshot import RULE_VERSION, SCHEMA_VERSION, analysis_digest
from .insights import build_overviews, role_sensitivity, sensitivity_profiles
from .witnesses import build_witnesses
from .observations import build_observations


def _json(path):
    def reject_constant(value):
        raise ValueError(f'{path.name}: недопустимое число {value}')
    return json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_constant)


def _csv(path, columns):
    with path.open(encoding='utf-8', newline='') as handle:
        require(next(csv.reader(handle), None) == columns, f'{path.name}: неверная схема CSV')
    return pd.read_csv(path, dtype={'gid': 'int64'}, keep_default_na=False)


def _equal(actual, expected, message):
    # Python considers True == 1, but a boolean cannot replace a JSON integer.
    require(isinstance(actual, (bool, np.bool_)) == isinstance(expected, (bool, np.bool_)), message)
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and actual.keys() == expected.keys(), message)
        for key in expected:
            _equal(actual[key], expected[key], message)
        return
    if isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), message)
        for left, right in zip(actual, expected):
            _equal(left, right, message)
        return
    require(actual == expected, message)


def _money(value):
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0, 'Выход: недопустимая сумма')
    cents = round(value * 100)
    require(abs(value * 100 - cents) < .001, 'Выход: потеря денежных сотых')
    return cents


def verify(data='data', out='artifacts', config_path=None):
    """Validate persisted files, without recalculating roles or changing any files.

    This checks consistency and completeness, not the truth of role hypotheses.
    Hashes detect accidental mixing; they are not a digital signature.
    """
    out = Path(out)
    hashes = input_hashes(data)
    nodes, edges, tx = load(data)
    cfg = read_config(config_path)
    snapshot = _json(out / 'graph.json')
    run = _json(out / 'run.json')
    require(isinstance(snapshot, dict) and isinstance(run, dict), 'Выход: ожидается JSON-объект')
    _equal(snapshot['schema_version'], SCHEMA_VERSION, 'graph.json: неподдерживаемая схема')
    _equal(snapshot['rule_version'], RULE_VERSION, 'graph.json: другая версия правил')
    _equal(snapshot['config'], cfg, 'graph.json: конфигурация отличается; укажите --config текущего расчёта')
    _equal(run['config'], cfg, 'run.json: конфигурация отличается')
    _equal(snapshot['summary']['input_sha256'], hashes, 'graph.json: исходные файлы изменились')
    _equal(run['summary']['input_sha256'], hashes, 'run.json: исходные файлы изменились')

    csvs = {'nodes_roles.csv': ROLE_COLUMNS, 'clusters.csv': CLUSTER_COLUMNS, 'top_nodes.csv': TOP_COLUMNS}
    tables = {name: _csv(out / name, columns) for name, columns in csvs.items()}
    digests = {name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in csvs}
    _equal(snapshot['output_sha256'], digests, 'CSV и graph.json принадлежат разным расчётам или повреждены')
    identity = dict(schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION,
                    input_sha256=hashes, config=cfg, output_sha256=digests,
                    analysis_sha256=snapshot['analysis_sha256'])
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                           allow_nan=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    _equal(snapshot['snapshot_id'], digest, 'graph.json: неверный snapshot_id')

    records = snapshot['nodes']
    require(isinstance(records, list) and len(records) == len(nodes), 'graph.json: нарушено покрытие узлов')
    expected_ids = [str(gid) for gid in nodes.gid]
    _equal([row['gid'] for row in records], expected_ids, 'graph.json: порядок, тип или значение gid изменены')
    frame = pd.DataFrame(records)
    frame['gid'] = frame.gid.astype('int64')
    indexed = frame.set_index('gid')
    roles, clusters, top = (tables[name] for name in csvs)
    validate_outputs(nodes, frame, clusters, top)
    # Compare actual CSV values, not only their declared hashes.
    for row, saved in zip(frame[ROLE_COLUMNS].to_dict('records'), roles.to_dict('records')):
        _equal(saved, row, 'nodes_roles.csv: значения отличаются от JSON')
    _equal(len(roles), len(frame), 'nodes_roles.csv: нарушено покрытие узлов')
    for original in nodes.itertuples(index=False):
        saved = indexed.loc[original.gid]
        _equal(saved.depth, original.depth, 'graph.json: изменён depth')
        _equal(saved.is_seed, original.is_seed, 'graph.json: изменён is_seed')

    expected_edges = [dict(src=str(row['src']), dst=str(row['dst']), sum_kzt=int(row['_cents']) / 100,
                           cents=int(row['_cents']), n_tx=int(row['n_tx']), depth=int(row['depth']))
                      for row in edges.to_dict('records')]
    _equal(snapshot['edges'], expected_edges, 'graph.json: связи не совпадают с исходными Parquet')
    expected_tx = [dict(row_id=i, src=str(row['src']), dst=str(row['dst']),
                        date=row['date'].date().isoformat(), cents=int(row['_cents']),
                        sum_kzt=int(row['_cents']) / 100) for i, row in enumerate(tx.to_dict('records'))]
    _equal(snapshot['transactions'], expected_tx, 'graph.json: операции, даты или повторы не совпадают с Parquet')

    graph = build_graph(nodes, edges)
    structural = graph.copy()
    structural.remove_edges_from(list(nx.selfloop_edges(structural)))
    for row in records:
        gid = int(row['gid'])
        for direction in ('in', 'out'):
            degree = getattr(graph, direction + '_degree')
            _equal(_money(row[direction + '_kzt']), degree(gid, weight='cents'), 'graph.json: неверный оборот узла')
            _equal(row[direction + '_tx'], degree(gid, weight='n_tx'), 'graph.json: неверное число операций узла')
            _equal(row[direction + '_deg'], getattr(structural, direction + '_degree')(gid), 'graph.json: неверное число контрагентов')
        _equal(row['truncated_by_depth'], row['depth'] == cfg['max_depth'] and structural.out_degree(gid) == 0,
               'graph.json: неверный флаг обрыва')
    expected_components = {gid: i for i, group in enumerate(sorted(nx.weakly_connected_components(graph), key=min)) for gid in group}
    for row in records:
        _equal(row['component_id'], expected_components[int(row['gid'])], 'graph.json: неверная компонента')
    ordered = frame.sort_values(['priority_score', 'gid'], ascending=[False, True])
    _equal(top.gid.tolist(), ordered.head(len(top)).gid.tolist(), 'top_nodes.csv: нарушена сортировка по приоритету и gid')

    expected_clusters = clusters.to_dict('records')
    for row in expected_clusters:
        cid = row['cluster_id']
        members = set(frame.loc[frame.cluster_id == cid, 'gid'])
        _equal(row['n_nodes'], len(members), 'clusters.csv: неверный размер отдельного кластера')
        _equal(row['n_seed'], int(indexed.loc[sorted(members), 'is_seed'].sum()), 'clusters.csv: неверное число seed')
        cents = sum(attrs['cents'] for src, dst, attrs in graph.edges(data=True) if src in members and dst in members)
        _equal(_money(row['sum_kzt_internal']), cents, 'clusters.csv: неверный внутренний оборот')
        leaders = json.loads(row['top_gids'])
        _equal(leaders, ordered.loc[ordered.cluster_id == cid, 'gid'].head(5).tolist(), 'clusters.csv: неверные лидеры')
        row['top_gids'] = [str(gid) for gid in leaders]
    _equal(snapshot['clusters'], expected_clusters, 'clusters.csv: значения отличаются от JSON')
    expected_top = top.to_dict('records')
    for row in expected_top:
        row['gid'] = str(row['gid'])
    _equal(snapshot['top'], expected_top, 'top_nodes.csv: значения отличаются от JSON')
    _equal(_json(out / 'metrics.json'), records, 'metrics.json: метрики принадлежат другому снимку')

    # Reconstruct row references and summaries from the original transfers. This
    # checks content independently of the stored hash (which is not a signature).
    witnesses = build_witnesses(graph, frame, tx, cfg)
    observations = build_observations(graph, tx)
    stability = role_sensitivity(frame, cfg)
    for row in records:
        gid = int(row['gid'])
        for key in ('bridge_evidence', 'temporal_evidence'):
            _equal(row[key], witnesses[gid][key], f'graph.json: неверные переводы-свидетели {key}')
        for key in ('motifs', 'observed_facts', 'motif_summary'):
            _equal(row[key], observations[gid][key], f'graph.json: неверный наблюдаемый факт {key}')
        _equal(row['role_stability'], stability[gid], 'graph.json: неверная устойчивость роли')
        require(row['role'] != 'coordinator' or bool(row['bridge_evidence']),
                'graph.json: у coordinator отсутствует направленный путь')
        _equal(row['role_stability']['profiles']['base'], row['role'], 'graph.json: роль не соответствует правилам')
        for name, metric in (('matched_cents', 'temporal_matched_kzt'),
                             ('strict_matched_cents', 'temporal_strict_matched_kzt')):
            _equal(row['temporal_evidence'][name], _money(row[metric]),
                   'graph.json: объём переводов-свидетелей не соответствует метрике')
    for key, expected in build_overviews(graph, frame).items():
        _equal(snapshot[key], expected, f'graph.json: неверный обзор {key}')
    _equal(snapshot['sensitivity_profiles'], sensitivity_profiles(cfg), 'graph.json: неверные профили порогов')
    _equal(snapshot['analysis_sha256'], analysis_digest(snapshot), 'graph.json: неверный хеш объяснений')

    totals = dict(n_nodes=len(nodes), n_edges=len(edges), n_transactions=len(tx),
                  n_seed=int(nodes.is_seed.sum()), n_clusters=len(clusters),
                  n_components=nx.number_weakly_connected_components(graph), n_isolates=nx.number_of_isolates(graph),
                  n_truncated=int(frame.truncated_by_depth.sum()),
                  sum_kzt=sum(int(value) for value in edges['_cents']) / 100,
                  date_from=tx.date.min().date().isoformat() if len(tx) else None,
                  date_to=tx.date.max().date().isoformat() if len(tx) else None,
                  role_counts={str(key): int(value) for key, value in frame.role.value_counts().sort_index().items()})
    for key, expected in totals.items():
        _equal(snapshot['summary'][key], expected, f'graph.json: неверный summary.{key}')
        _equal(run['summary'][key], expected, f'run.json: неверный summary.{key}')
    seconds = run['summary']['total_seconds']
    require(isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
            and math.isfinite(seconds) and 0 <= seconds <= 300, 'run.json: полный расчёт должен занимать ≤300 секунд')
    html = (out / 'report.html').read_text(encoding='utf-8')
    require('<html' in html.lower() and '<script' in html.lower() and 'const DATA = {' in html
            and '/*__DATA__*/' not in html,
            'report.html: нет готового HTML-экрана с данными')
    _equal(input_hashes(data), hashes, 'Исходные файлы изменились во время проверки')
    return dict(status='ok', snapshot_id=snapshot['snapshot_id'], **totals,
                total_seconds=seconds, output_sha256=digests,
                checks=['input_hashes', 'csv_schemas', 'csv_json_consistency', 'all_nodes',
                        'transactions_and_duplicates', 'money_and_counts', 'clusters', 'ranking',
                        'witnesses', 'observed_facts', 'role_stability', 'component_overviews', 'runtime'])


def main():
    parser = argparse.ArgumentParser(description='Приёмка готовых CSV/JSON по исходным Parquet, без записи файлов')
    parser.add_argument('--data', default='data')
    parser.add_argument('--out', default='artifacts')
    parser.add_argument('--config')
    args = parser.parse_args()
    try:
        result = verify(args.data, args.out, args.config)
    except (ValueError, OSError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        print(json.dumps(dict(status='error', error=f'Проверка не пройдена: {exc}'), ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
