"""Deterministic graph metrics, community detection, rules and ranking."""
from collections import deque
import json
import math
from pathlib import Path
import networkx as nx
import pandas as pd
from .witnesses import build_witnesses
from .observations import build_observations
from .insights import role_sensitivity

ROLES = ('consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral')
PRECEDENCE = ('coordinator', 'distributor', 'consolidator', 'transit', 'terminal', 'peripheral')
ROLE_LABELS = {'consolidator': 'Консолидация', 'transit': 'Транзит', 'distributor': 'Распределение',
               'terminal': 'Конечный получатель?', 'coordinator': 'Координация?', 'peripheral': 'Периферия'}


def _finite_number(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def read_config(path=None):
    default_path = Path(__file__).resolve().parent.parent / 'config' / 'roles.json'
    cfg = json.loads(Path(path or default_path).read_text(encoding='utf-8'))
    expected = json.loads(default_path.read_text(encoding='utf-8'))
    if not isinstance(cfg, dict):
        raise ValueError('Config должен быть JSON-объектом')
    if set(cfg) != set(expected):
        raise ValueError('Набор параметров config должен соответствовать config/roles.json')
    if cfg['centrality_mode'] not in ('exact', 'approximate'):
        raise ValueError('centrality_mode: ожидается exact или approximate')
    if cfg['coordinator_require_bridge'] is not True:
        raise ValueError('coordinator_require_bridge: требуется true; координатору нужен направленный путь')
    for key, value in cfg.items():
        if key not in ('priority_weights', 'centrality_mode', 'coordinator_require_bridge') and (not _finite_number(value) or value < 0):
            raise ValueError(f'Неверный параметр {key}')
    for key in ('max_depth', 'random_seed', 'betweenness_samples', 'consolidator_min_payers',
                'consolidator_min_seeds', 'distributor_min_payees', 'coordinator_min_degree',
                'coordinator_min_seeds', 'coordinator_min_other_clusters', 'transit_window_days'):
        if not isinstance(cfg[key], int):
            raise ValueError(f'{key}: ожидается целое число')
    if cfg['max_depth'] != 4 or cfg['betweenness_samples'] < 1 or cfg['louvain_resolution'] <= 0:
        raise ValueError('Неверная глубина, число samples или resolution')
    if cfg['transit_ratio_min'] > cfg['transit_ratio_max'] or not 0 <= cfg['coordinator_centrality_quantile'] <= 1:
        raise ValueError('Неверный диапазон порогов')
    if not 0 < cfg['transit_min_matched_fraction'] <= 1:
        raise ValueError('transit_min_matched_fraction: ожидается число в (0, 1]')
    weights = cfg['priority_weights']
    if (not isinstance(weights, dict) or set(weights) != set(expected['priority_weights'])
            or any(not _finite_number(v) or v < 0 for v in weights.values())
            or abs(sum(weights.values()) - 1) > 1e-9):
        raise ValueError('Веса приоритета должны быть неотрицательны и в сумме давать 1')
    return cfg


def build_graph(nodes, edges):
    graph = nx.DiGraph()
    graph.add_nodes_from(int(gid) for gid in nodes.gid)
    for row in edges.itertuples():
        graph.add_edge(int(row.src), int(row.dst), sum_kzt=float(row.sum_kzt),
                       cents=int(round(row.sum_kzt * 100)), n_tx=int(row.n_tx))
    return graph


def communities(graph, cfg):
    projection = nx.Graph()
    projection.add_nodes_from(sorted(graph))
    for src, dst, attrs in sorted(graph.edges(data=True)):
        if src != dst and attrs['cents'] > 0:
            old = projection.get_edge_data(src, dst, {}).get('weight', 0)
            projection.add_edge(src, dst, weight=old + attrs['cents'])
    groups = []
    for component in sorted(nx.connected_components(projection), key=min):
        subgraph = projection.subgraph(sorted(component)).copy()
        if len(component) == 1:
            groups.append(set(component))
        else:
            groups.extend(nx.community.louvain_communities(subgraph, weight='weight',
                          resolution=cfg['louvain_resolution'], seed=cfg['random_seed']))
    groups.sort(key=min)
    return {gid: number for number, group in enumerate(groups) for gid in group}


def temporal_details(tx, window_days=2):
    """Two independent FIFO scenarios, with and without same-day matches.

    This is compatibility of observed volumes, not tracing identified money.
    Daily integer aggregation supports split/merged transfers. In each scenario
    an incoming cent and an outgoing cent can each be matched at most once.
    Self-transfers contribute neither to matching nor to its denominators.
    """
    daily = {}
    for row in tx.itertuples(index=False):
        if row.src == row.dst:
            continue
        day = row.date.toordinal()
        cents = int(round(row.sum_kzt * 100))
        daily.setdefault(int(row.dst), {}).setdefault(day, [0, 0])[0] += cents
        daily.setdefault(int(row.src), {}).setdefault(day, [0, 0])[1] += cents
    result = {}
    for gid, days in daily.items():
        total_in = sum(amounts[0] for amounts in days.values())
        total_out = sum(amounts[1] for amounts in days.values())
        scenario = {}
        for allow_same_day in (True, False):
            pending = deque()
            matched = same_day = 0
            for day, (incoming, outgoing) in sorted(days.items()):
                while pending and pending[0][0] < day - window_days:
                    pending.popleft()
                if incoming and allow_same_day:
                    pending.append([day, incoming])
                while outgoing and pending:
                    amount = min(outgoing, pending[0][1])
                    outgoing -= amount
                    pending[0][1] -= amount
                    matched += amount
                    if pending[0][0] == day:
                        same_day += amount
                    if pending[0][1] == 0:
                        pending.popleft()
                if incoming and not allow_same_day:
                    pending.append([day, incoming])
            scenario[allow_same_day] = (matched, same_day)
        matched, same_day = scenario[True]
        strict_matched = scenario[False][0]
        result[gid] = dict(
            temporal_matched_kzt=matched / 100,
            temporal_in_fraction=matched / total_in if total_in else 0.0,
            temporal_out_fraction=matched / total_out if total_out else 0.0,
            temporal_strict_matched_kzt=strict_matched / 100,
            temporal_strict_in_fraction=strict_matched / total_in if total_in else 0.0,
            temporal_strict_out_fraction=strict_matched / total_out if total_out else 0.0,
            temporal_same_day_matched_kzt=same_day / 100,
            temporal_window_days=window_days, active_days=len(days))
    return result


def temporal_matching(tx):
    """Compatibility API retained: input share in 0–2 days and active days."""
    return {gid: (values['temporal_in_fraction'], values['active_days'])
            for gid, values in temporal_details(tx, 2).items()}


def features(nodes, graph, tx, cfg):
    clusters = communities(graph, cfg)
    # Amount is not a distance. Directed, unweighted shortest paths measure structural brokerage.
    structural = graph.copy()
    structural.remove_edges_from(list(nx.selfloop_edges(structural)))
    samples = None if cfg['centrality_mode'] == 'exact' else min(cfg['betweenness_samples'], len(graph))
    centrality = nx.betweenness_centrality(structural, k=samples,
                                         weight=None, normalized=True, seed=cfg['random_seed']) if graph.number_of_edges() else dict.fromkeys(graph, 0.0)
    seed_sets = {gid: set() for gid in graph}
    for seed in sorted(int(x) for x in nodes.loc[nodes.is_seed, 'gid']):
        for target in nx.single_source_shortest_path_length(structural, seed, cutoff=cfg['max_depth']):
            if target != seed:
                seed_sets[target].add(seed)
    timing = temporal_details(tx, cfg['transit_window_days'])
    legacy_timing = temporal_matching(tx) if cfg['transit_window_days'] != 2 else None
    empty_timing = dict(temporal_matched_kzt=0.0, temporal_in_fraction=0.0,
        temporal_out_fraction=0.0, temporal_strict_matched_kzt=0.0,
        temporal_strict_in_fraction=0.0, temporal_strict_out_fraction=0.0,
        temporal_same_day_matched_kzt=0.0, temporal_window_days=cfg['transit_window_days'], active_days=0)
    component_of = {gid: i for i, c in enumerate(sorted(nx.weakly_connected_components(graph), key=min)) for gid in c}
    rows = []
    for node in nodes.itertuples(index=False):
        gid = int(node.gid)
        din, dout = structural.in_degree(gid), structural.out_degree(gid)
        incoming, outgoing = graph.in_degree(gid, weight='cents'), graph.out_degree(gid, weight='cents')
        external_in = structural.in_degree(gid, weight='cents')
        external_out = structural.out_degree(gid, weight='cents')
        ratio = external_out / external_in if external_in else None
        temporal = timing.get(gid, empty_timing)
        others = {clusters[x] for x in set(structural.predecessors(gid)) | set(structural.successors(gid))} - {clusters[gid]}
        rows.append(dict(gid=gid, depth=int(node.depth), is_seed=bool(node.is_seed),
            in_deg=din, out_deg=dout, in_kzt=incoming / 100, out_kzt=outgoing / 100,
            external_in_kzt=external_in / 100, external_out_kzt=external_out / 100,
            in_tx=int(graph.in_degree(gid, weight='n_tx')), out_tx=int(graph.out_degree(gid, weight='n_tx')),
            pass_through=ratio, truncated_by_depth=bool(node.depth == cfg['max_depth'] and dout == 0),
            seed_reach=len(seed_sets[gid]), betweenness=float(centrality[gid]),
            other_clusters=len(others), cluster_id=clusters[gid], component_id=component_of[gid],
            temporal_2d=(legacy_timing.get(gid, (0, 0))[0] if legacy_timing is not None
                         else temporal['temporal_in_fraction']), **temporal))
    return pd.DataFrame(rows)


def peripheral_reason(row):
    """Why no substantive rule is assigned, without asserting innocence or guilt."""
    if row['in_deg'] + row['out_deg'] == 0:
        return 'isolated', 'Нет наблюдаемых связей с другими клиентами'
    if row['truncated_by_depth']:
        return 'truncated', 'Обрыв наблюдения на предельной глубине'
    if row['is_seed']:
        return 'incomplete_seed', 'Вход seed неполон; отношение потоков неприменимо'
    if row.get('external_in_kzt', row['in_kzt']) <= 0:
        return 'no_observed_input', 'Нет положительного наблюдаемого входа'
    return 'no_rule', 'Ни одно правило финансовой роли не выполнено'


def classify(row, cfg, centrality_threshold):
    ratio = row['pass_through']
    reliable_ratio = not row['is_seed'] and not row['truncated_by_depth'] and row.get('external_in_kzt', row['in_kzt']) > 0
    temporal_in = row.get('temporal_in_fraction', row.get('temporal_2d', 0.0))
    temporal_out = row.get('temporal_out_fraction', temporal_in / ratio if reliable_ratio and ratio else 0.0)
    active = row['in_deg'] + row['out_deg']
    conditions = {
        'coordinator': active >= cfg['coordinator_min_degree'] and row['betweenness'] > 0
            and row['betweenness'] >= centrality_threshold
            and row['other_clusters'] >= cfg['coordinator_min_other_clusters']
            and row['seed_reach'] >= cfg['coordinator_min_seeds']
            and bool(row.get('bridge_evidence')),
        'distributor': row['out_deg'] >= cfg['distributor_min_payees']
            and row['out_deg'] >= cfg['distributor_fan_ratio'] * max(row['in_deg'], 1),
        'consolidator': row['in_deg'] >= cfg['consolidator_min_payers'] and row['seed_reach'] >= cfg['consolidator_min_seeds'],
        'transit': reliable_ratio and row['in_deg'] > 0 and row['out_deg'] > 0
            and cfg['transit_ratio_min'] <= ratio <= cfg['transit_ratio_max']
            and min(temporal_in, temporal_out) >= cfg['transit_min_matched_fraction'],
        'terminal': reliable_ratio and row['out_deg'] == 0,
        'peripheral': True,
    }
    matched = [role for role in PRECEDENCE if conditions[role] and role != 'peripheral']
    role = matched[0] if matched else 'peripheral'
    extras = {
        'coordinator': [row['seed_reach'] >= 5, row['other_clusters'] >= 3, row['active_days'] >= 3],
        'distributor': [row['out_deg'] >= 10, row['active_days'] >= 3, row['out_tx'] >= 2 * max(row['out_deg'], 1)],
        'consolidator': [row['in_deg'] >= 10, row['seed_reach'] >= 3, reliable_ratio and ratio <= 0.3],
        'transit': [0.9 <= ratio <= 1.1 if reliable_ratio else False,
                    min(row.get('temporal_strict_in_fraction', 0), row.get('temporal_strict_out_fraction', 0)) >= cfg['transit_min_matched_fraction'],
                    row['active_days'] >= 3],
        'terminal': [row['in_deg'] >= 2, row['in_tx'] >= 3, row['active_days'] >= 3],
    }
    score = 0.2 if role == 'peripheral' else round(0.6 + 0.1 * sum(extras[role]), 2)
    if row['truncated_by_depth']:
        score = min(score, 0.6)
    snippets = {
        'coordinator': f"Направленный путь между кластерами; посредничество {row['betweenness']:.3g}; {row['other_clusters']} чужих кластеров; {row['seed_reach']} seed-путей",
        'distributor': f"Веер: {row['out_deg']} получателей, {row['out_tx']} переводов; {row['in_deg']} плательщиков",
        'consolidator': f"Сбор: {row['in_deg']} плательщиков, {row['in_tx']} переводов; {row['seed_reach']} seed-путей",
        'transit': f"Пропуск {ratio:.3f}; ≤{cfg['transit_window_days']} дн.: {temporal_in:.1%} входа, {temporal_out:.1%} выхода; внутри дня порядок неизвестен" if reliable_ratio else '',
        'terminal': f"Вход {row['in_kzt']:,.0f} KZT от {row['in_deg']} клиентов, 0 исходящих; depth={row['depth']}",
        'peripheral': f"{row['in_deg']} входящих, {row['out_deg']} исходящих связей; {peripheral_reason(row)[1]}",
    }
    limitation = ('Обрыв depth=4, удержание неизвестно' if row['truncated_by_depth'] else
                  'Вход seed неполон' if row['is_seed'] else 'Полный баланс неизвестен')
    evidence = f"Гипотеза: {snippets[role]}. {limitation}."
    if len(evidence) > 200:
        evidence = f"Гипотеза: {snippets[role][:150-len(limitation)]}… {limitation}."
    return role, score, evidence, matched


def percentile(series):
    result = series.rank(method='average', pct=True)
    return result.where(series > 0, 0.0)


def analyze(nodes, edges, tx, cfg):
    graph = build_graph(nodes, edges)
    frame = features(nodes, graph, tx, cfg)
    witnesses = build_witnesses(graph, frame, tx, cfg)
    observations = build_observations(graph, tx)
    for key in ('bridge_evidence', 'temporal_evidence'):
        frame[key] = [witnesses[int(gid)][key] for gid in frame.gid]
    for key in ('motifs', 'observed_facts', 'motif_summary'):
        frame[key] = [observations[int(gid)][key] for gid in frame.gid]
    active = frame.in_deg + frame.out_deg > 0
    centrality_threshold = float(frame.loc[active, 'betweenness'].quantile(cfg['coordinator_centrality_quantile'])) if active.any() else 0.0
    classified = [classify(row, cfg, centrality_threshold) for row in frame.to_dict('records')]
    frame['role'], frame['role_score'], frame['evidence'], frame['matched_roles'] = zip(*classified)
    reasons = [peripheral_reason(row) if row['role'] == 'peripheral' else ('', '')
               for row in frame.to_dict('records')]
    frame['peripheral_reason'], frame['peripheral_reason_text'] = zip(*reasons)
    stability = role_sensitivity(frame, cfg)
    frame['role_stability'] = [stability[int(gid)] for gid in frame.gid]
    frame['limitations'] = [
        ('Обрыв на depth=4: продолжение цепочки не наблюдается. ' if row.truncated_by_depth else '')
        + ('Вход seed неполон. ' if row.is_seed else '')
        + 'Роль — гипотеза по неполной выборке. Совместимость дат и сумм не доказывает движение тех же денег.'
        for row in frame.itertuples()]
    frame['next_query'] = [
        'Запросить продолжение исходящих связей за четвёртым коленом.' if row.truncated_by_depth else
        'Запросить полный входящий поток, включая межбанк и соседние периоды.' if row.is_seed else
        'Уточнить время операций внутри дня и проверить указанные переводы.' if row.role == 'transit' else
        'Проверить указанный путь и назначение переводов между участниками.' if row.role == 'coordinator' else
        'Проверить операции наблюдаемого мотива и их назначение.' if row.motifs else
        'Запросить операции соседних периодов и межбанковские переводы.'
        for row in frame.itertuples()]
    components = pd.DataFrame(index=frame.index)
    components['seed_reach'] = (frame.seed_reach / 5).clip(0, 1)
    for name, values in [('flow', frame[['in_kzt', 'out_kzt']].max(axis=1)),
                         ('bridge', frame.betweenness), ('fan', frame[['in_deg', 'out_deg']].max(axis=1))]:
        components[name] = 0.0
        components.loc[active, name] = percentile(values[active])
    components['role_support'] = frame.role_score.where(frame.role != 'peripheral', 0)
    weighted = components.mul(pd.Series(cfg['priority_weights']))
    frame['priority_score'] = weighted.sum(axis=1).where(active, 0).round(6)
    frame['priority_parts'] = weighted.to_dict('records')
    ordered = frame.sort_values(['priority_score', 'gid'], ascending=[False, True]).copy()
    top = ordered.head(max(20, min(50, len(frame))))[['gid', 'role', 'priority_score', 'evidence']].copy()
    labels = {'seed_reach': 'связь с seed', 'flow': 'оборот', 'bridge': 'посредничество', 'fan': 'контрагенты', 'role_support': 'роль'}
    top['why'] = [row.evidence + ' Приоритет: ' + ', '.join(f'{labels[k]} +{v:.3f}' for k, v in
                 sorted(row.priority_parts.items(), key=lambda x: (-x[1], x[0]))[:2]) for row in ordered.head(len(top)).itertuples()]
    top = top.drop(columns='evidence')
    top.insert(0, 'rank', range(1, len(top) + 1))
    groups = []
    for cid, group in frame.groupby('cluster_id', sort=True):
        members = set(group.gid)
        internal = sum(attrs['cents'] for src, dst, attrs in graph.edges(data=True) if src in members and dst in members)
        leaders = ordered[ordered.cluster_id == cid].head(5).gid.tolist()
        counts = group.role.value_counts()
        dominant = str(counts.index[0])
        groups.append(dict(cluster_id=int(cid), n_nodes=len(group), n_seed=int(group.is_seed.sum()),
            sum_kzt_internal=internal / 100, top_gids=json.dumps(leaders),
            hypothesis=f"Структурное сообщество: {ROLE_LABELS[dominant].lower()} у {int(counts.iloc[0])} из {len(group)} узлов; seed={int(group.is_seed.sum())}. Гипотеза для проверки."))
    return graph, frame, pd.DataFrame(groups), top
