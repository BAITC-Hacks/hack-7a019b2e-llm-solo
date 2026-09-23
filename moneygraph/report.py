"""Validated CSV exports and a completely self-contained, offline HTML viewer."""
import json
import math
import os
from pathlib import Path
import tempfile

ROLE_COLUMNS = ['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence']
CLUSTER_COLUMNS = ['cluster_id', 'n_nodes', 'n_seed', 'sum_kzt_internal', 'top_gids', 'hypothesis']
TOP_COLUMNS = ['rank', 'gid', 'role', 'priority_score', 'why']


def validate_outputs(nodes, frame, clusters, top):
    from .analysis import ROLES
    from .io import require
    require(len(frame) == len(nodes) and frame.gid.is_unique and set(frame.gid) == set(nodes.gid), 'Выход: нарушено покрытие gid')
    require(frame.role.isin(ROLES).all(), 'Выход: неизвестная роль')
    require(frame[['role_score', 'priority_score']].apply(lambda s: s.between(0, 1).all()).all(), 'Выход: score вне [0,1]')
    require(frame.evidence.str.len().between(1, 200).all(), 'Выход: evidence должен быть 1–200 символов')
    require(not frame[ROLE_COLUMNS].isna().any().any(), 'Выход: пустые обязательные поля')
    require(set(frame.cluster_id) == set(clusters.cluster_id) and clusters.cluster_id.is_unique, 'Выход: кластеры не согласованы')
    require(clusters.n_nodes.sum() == len(nodes) and clusters.n_seed.sum() == nodes.is_seed.sum(), 'Выход: неверные размеры кластеров')
    require(clusters.hypothesis.str.len().gt(0).all(), 'Выход: нет гипотез кластеров')
    require(len(top) >= min(20, len(nodes)) and top.gid.is_unique, 'Выход: недостаточный/повторяющийся top')
    require(top['rank'].tolist() == list(range(1, len(top) + 1)), 'Выход: неверный rank')
    require(top.priority_score.is_monotonic_decreasing and top.why.str.len().gt(0).all(), 'Выход: неверный top')
    expected = frame.set_index('gid').loc[top.gid]
    require(expected.role.tolist() == top.role.tolist() and expected.priority_score.tolist() == top.priority_score.tolist(), 'Выход: top не совпадает с ролями')
    require(not ((frame.role == 'terminal') & (frame.truncated_by_depth | frame.is_seed)).any(), 'Выход: ложный terminal')


def _clean(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def export(out_dir, graph, frame, clusters, top, summary, config):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csvs = {
        'nodes_roles.csv': frame[ROLE_COLUMNS].to_csv(index=False, float_format='%.6f', lineterminator='\n'),
        'clusters.csv': clusters[CLUSTER_COLUMNS].to_csv(index=False, lineterminator='\n'),
        'top_nodes.csv': top[TOP_COLUMNS].to_csv(index=False, float_format='%.6f', lineterminator='\n'),
    }
    # Never encode int64 identifiers as JS Number. This dataset exceeds 2**53.
    records = frame.to_dict('records')
    for row in records:
        row['gid'] = str(row['gid'])
    cluster_records = clusters.to_dict('records')
    for row in cluster_records:
        row['top_gids'] = [str(gid) for gid in json.loads(row['top_gids'])]
    top_records = top.to_dict('records')
    for row in top_records:
        row['gid'] = str(row['gid'])
    payload = _clean(dict(nodes=records,
        edges=[dict(src=str(src), dst=str(dst), sum_kzt=attrs['cents'] / 100, n_tx=attrs['n_tx'])
               for src, dst, attrs in graph.edges(data=True)],
        clusters=cluster_records, top=top_records, summary=summary, config=config, exports=csvs))
    data_json = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':')).replace('<', '\\u003c')
    template = (Path(__file__).parent / 'viewer.html').read_text(encoding='utf-8')
    page = template.replace('/*__DATA__*/', data_json)
    files = {**csvs, 'report.html': page,
             'metrics.json': json.dumps(_clean(records), ensure_ascii=False, allow_nan=False, indent=2),
             'run.json': json.dumps(dict(summary=summary, config=config), ensure_ascii=False, allow_nan=False, indent=2)}
    for name, content in files.items():
        fd, temp = tempfile.mkstemp(prefix='.write-', dir=out)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
                handle.write(content)
            os.replace(temp, out / name)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return out / 'report.html'
