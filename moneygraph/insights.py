"""Declared threshold sensitivity and views that preserve small components.

These summaries reuse the calculated graph and node features. They do not
recompute centrality, communities, temporal matching or the priority ranking.
"""
from collections import Counter
from copy import deepcopy
import math
from numbers import Integral


_COUNT_THRESHOLDS = (
    'consolidator_min_payers', 'consolidator_min_seeds',
    'distributor_min_payees', 'coordinator_min_degree',
    'coordinator_min_seeds', 'coordinator_min_other_clusters',
)
_MOTIFS = ('burst', 'cycle', 'repeat_amount')
_SCOPE = (
    'Три профиля порогов ролей: base, relaxed, strict; признаки, кластеры, '
    'seed, окно времени, порядок ролей и приоритеты фиксированы.'
)
_LIMITATION = (
    'Совпадение ролей в этих профилях не доказывает истинность роли и не '
    'проверяет устойчивость к другим порогам, неполноте данных или порядку '
    'переводов внутри дня.'
)


def sensitivity_profiles(cfg):
    """Return complete independent configs for three documented scenarios.

    Count thresholds use floor(0.8*x) / ceil(1.2*x), with positive counts
    staying at least one. The distributor ratio uses factors 0.8 / 1.2.
    The transit ratio interval expands/contracts around its current midpoint
    by 1.2 / 0.8. Its matched-volume threshold uses 0.9 / 1.1, capped at one.
    The coordinator quantile moves by -/+0.05, clipped to [0, 1].
    These are explicit diagnostic scenarios, not fitted confidence bounds.
    """
    profiles = {'base': deepcopy(cfg)}
    midpoint = (cfg['transit_ratio_min'] + cfg['transit_ratio_max']) / 2
    half_width = (cfg['transit_ratio_max'] - cfg['transit_ratio_min']) / 2
    for name, factor in (('relaxed', 0.8), ('strict', 1.2)):
        settings = deepcopy(cfg)
        relaxed = name == 'relaxed'
        for key in _COUNT_THRESHOLDS:
            value = cfg[key]
            rounded = math.floor(value * factor) if relaxed else math.ceil(value * factor)
            settings[key] = max(1, rounded) if value > 0 else 0
        settings['distributor_fan_ratio'] = cfg['distributor_fan_ratio'] * factor
        width = half_width * (1.2 if relaxed else 0.8)
        settings['transit_ratio_min'] = max(0.0, midpoint - width)
        settings['transit_ratio_max'] = midpoint + width
        settings['transit_min_matched_fraction'] = min(
            1.0, cfg['transit_min_matched_fraction'] * (0.9 if relaxed else 1.1))
        settings['coordinator_centrality_quantile'] = min(1.0, max(
            0.0, cfg['coordinator_centrality_quantile'] + (-0.05 if relaxed else 0.05)))
        profiles[name] = settings
    return profiles


def role_sensitivity(frame, cfg):
    """Return {int(gid): role_stability} using the same classify as production.

    Each role_stability contains stable, profiles (profile -> primary role),
    scope and limitation. No mutation of input rows or configuration occurs.
    The base role is recomputed, so callers can verify it against their frame.
    """
    # Local import allows analysis to enrich its output with these summaries.
    from .analysis import classify

    if frame.empty:
        return {}
    if not frame.gid.is_unique:
        raise ValueError('Sensitivity: duplicate gid')
    active = frame.in_deg + frame.out_deg > 0
    rows = sorted(frame.to_dict('records'), key=lambda row: int(row['gid']))
    results = {int(row['gid']): {} for row in rows}
    for name, settings in sensitivity_profiles(cfg).items():
        threshold = (float(frame.loc[active, 'betweenness'].quantile(
            settings['coordinator_centrality_quantile'])) if active.any() else 0.0)
        for row in rows:
            results[int(row['gid'])][name] = classify(row, settings, threshold)[0]
    return {
        gid: dict(stable=len(set(roles.values())) == 1, profiles=roles,
                  scope=_SCOPE, limitation=_LIMITATION)
        for gid, roles in results.items()
    }


def build_overviews(graph, frame):
    """Return all component summaries and rankings of observed motif members.

    Ranking is the existing priority descending, then numeric gid ascending.
    Components retain isolates; amounts sum edge integer cents exactly once.
    All exported gid values are decimal strings, including long int64 values.
    Motif counts count nodes, not the number of cycles or transactions.
    """
    rows = frame.to_dict('records')
    indexed = {int(row['gid']): row for row in rows}
    if len(indexed) != len(rows) or set(indexed) != set(graph.nodes):
        raise ValueError('Overviews: frame must cover every graph node exactly once')
    rows.sort(key=lambda row: (-float(row['priority_score']), int(row['gid'])))
    grouped = {}
    motif_members = {name: [] for name in _MOTIFS}
    for row in rows:
        cid = int(row['component_id'])
        grouped.setdefault(cid, []).append(row)
        motifs = row.get('motifs', [])
        if not isinstance(motifs, list) or any(not isinstance(name, str) or not name for name in motifs):
            raise ValueError('Overviews: motifs must be a list of nonempty names')
        for name in sorted(set(motifs)):
            motif_members.setdefault(name, []).append(str(int(row['gid'])))

    edge_counts = Counter()
    edge_cents = Counter()
    for src, dst, attrs in graph.edges(data=True):
        cid = int(indexed[src]['component_id'])
        if cid != int(indexed[dst]['component_id']):
            raise ValueError('Overviews: edge crosses declared components')
        cents = attrs.get('cents')
        if isinstance(cents, bool) or not isinstance(cents, Integral) or cents < 0:
            raise ValueError('Overviews: edge cents must be a nonnegative integer')
        edge_counts[cid] += 1
        edge_cents[cid] += int(cents)

    components = []
    for cid, members in sorted(grouped.items()):
        roles = Counter(row['role'] for row in members)
        motifs = Counter(name for row in members for name in set(row.get('motifs', [])))
        components.append(dict(
            component_id=cid, n_nodes=len(members),
            n_seed=sum(bool(row['is_seed']) for row in members),
            n_edges=edge_counts[cid], sum_kzt_internal=edge_cents[cid] / 100,
            top_gids=[str(int(row['gid'])) for row in members[:10]],
            role_counts=dict(sorted(roles.items())),
            motif_counts={name: motifs[name] for name in sorted(motif_members)},
        ))
    return dict(components=components, motif_rankings={
        name: dict(n_nodes=len(gids), top_gids=gids[:20])
        for name, gids in sorted(motif_members.items())
    })
