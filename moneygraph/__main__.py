import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
import time
import networkx as nx
from .analysis import analyze, read_config
from .io import load, input_hashes
from .report import export, validate_outputs
from .files import atomic_text


def run(data, out, config_path=None):
    started = time.perf_counter()
    cfg = read_config(config_path)
    hashes = input_hashes(data)
    nodes, edges, tx = load(data)
    graph, frame, clusters, top = analyze(nodes, edges, tx, cfg)
    validate_outputs(nodes, frame, clusters, top)
    summary = dict(n_nodes=len(nodes), n_edges=len(edges), n_transactions=len(tx), n_seed=int(nodes.is_seed.sum()),
        n_clusters=len(clusters), n_components=nx.number_weakly_connected_components(graph),
        n_isolates=nx.number_of_isolates(graph), n_truncated=int(frame.truncated_by_depth.sum()),
        sum_kzt=sum(attrs['cents'] for _, _, attrs in graph.edges(data=True)) / 100,
        date_from=str(tx.date.min().date()) if len(tx) else None, date_to=str(tx.date.max().date()) if len(tx) else None,
        role_counts={str(k): int(v) for k, v in frame.role.value_counts().sort_index().items()},
        analysis_seconds=round(time.perf_counter() - started, 3), input_sha256=hashes,
        versions={name: importlib.metadata.version(name) for name in ('pandas', 'numpy', 'pyarrow', 'networkx')},
        warnings=['Граф ограничен 4 коленами, одним месяцем и внутрибанковскими переводами ≥5000 KZT.',
                  'Связь с seed и роль — гипотеза для проверки, не утверждение о виновности.',
                  'Сумма рёбер — оборот; одни и те же средства могут учитываться на нескольких шагах.'])
    if len(tx) and (tx.sum_kzt < 5000).any():
        summary['warnings'].append('В наборе есть транзакции ниже заявленного порога 5000 KZT; они не удалены.')
    if len(tx) and tx.date.dt.to_period('M').nunique() > 1:
        summary['warnings'].append('В наборе больше одного месяца; проверьте соответствие параметрам выгрузки.')
    if (edges.src == edges.dst).any():
        summary['warnings'].append('Самопереводы учтены в суммах, исключены из структурных связей и временного сопоставления.')
    if input_hashes(data) != hashes:
        raise ValueError('Исходные файлы изменились во время расчёта; повторите запуск на одном снимке')
    page = export(out, graph, frame, clusters, top, summary, cfg, edges=edges, transactions=tx)
    summary['total_seconds'] = round(time.perf_counter() - started, 3)
    # The HTML shows analysis time; run.json includes the complete export wall time.
    atomic_text(Path(out) / 'run.json', json.dumps(dict(summary=summary, config=cfg), ensure_ascii=False, allow_nan=False, indent=2))
    return summary, page


def main():
    parser = argparse.ArgumentParser(description='Граф денег: Parquet → роли, кластеры, приоритеты и локальный HTML')
    parser.add_argument('--data', default='data')
    parser.add_argument('--out', default='artifacts')
    parser.add_argument('--config')
    parser.add_argument('--serve', action='store_true', help='После расчёта запустить просмотр на 127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    try:
        summary, page = run(args.data, args.out, args.config)
        print(json.dumps(dict(status='ok', report=str(page.resolve()), **summary), ensure_ascii=False, indent=2))
        if args.serve:
            from .server import make_server
            server = make_server(args.out, args.port)
            print(f'Просмотр: http://127.0.0.1:{args.port}/report.html', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
