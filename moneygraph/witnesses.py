"""Concrete transaction references for structural and temporal hypotheses.

Inputs must already be validated and canonically ordered by ``validate_tables``.
Transaction row IDs are export ordinals, exactly as in ``snapshot.make_snapshot``;
they are not bank transaction IDs. No claim about identity of money is made.
"""
from collections import deque


BRIDGE_LIMITATION = (
    'Направленный путь связывает разные сообщества. Это структура наблюдаемых '
    'переводов, а не доказательство движения одних денег или координации; '
    'хронологическая совместимость этого пути не утверждается.'
)
TEMPORAL_LIMITATION = (
    'FIFO сопоставляет совместимые объёмы, не отслеживает одни и те же деньги. '
    'Внутри календарного дня порядок неизвестен: основной сценарий ставит входы '
    'перед выходами; strict_matches запрещает совпадения внутри дня. '
    'Самопереводы исключены; полный баланс по этой выборке неизвестен.'
)


def _match(days, window_days, allow_same_day):
    """Allocate cents once per input and output in this independent scenario."""
    pending = deque()
    matches = []
    for day, entries in sorted(days.items()):
        incoming, outgoing = entries
        while pending and pending[0][0] < day - window_days:
            pending.popleft()
        if allow_same_day:
            pending.extend([day, row_id, cents] for row_id, cents in incoming if cents)
        for out_row_id, outgoing_cents in outgoing:
            while outgoing_cents and pending:
                in_day, in_row_id, available = pending[0]
                cents = min(outgoing_cents, available)
                matches.append(dict(in_row_id=in_row_id, out_row_id=out_row_id,
                                    cents=cents, lag_days=day - in_day))
                outgoing_cents -= cents
                pending[0][2] -= cents
                if not pending[0][2]:
                    pending.popleft()
        if not allow_same_day:
            pending.extend([day, row_id, cents] for row_id, cents in incoming if cents)
    return matches


def build_witnesses(graph, frame, tx, cfg):
    """Return a witness dictionary for every graph node, including isolates.

    The bridge witness is the lexicographically smallest simple directed path
    A -> gid -> B whose endpoints belong to different clusters. On each edge
    the first canonical transaction is shown. Temporal witnesses retain every
    allocation, so their totals reproduce ``analysis.temporal_details`` for
    both independent daily FIFO scenarios without truncating the evidence.
    """
    clusters = {int(row.gid): int(row.cluster_id)
                for row in frame[['gid', 'cluster_id']].itertuples(index=False)}
    window_days = cfg['transit_window_days']
    daily = {}
    first_on_edge = {}
    for row_id, row in enumerate(tx.itertuples(index=False)):
        src, dst = int(row.src), int(row.dst)
        if src == dst:
            continue
        cents = int(round(row.sum_kzt * 100))
        day = row.date.toordinal()
        leg = dict(row_id=row_id, src=str(src), dst=str(dst),
                   date=row.date.date().isoformat(), cents=cents)
        first_on_edge.setdefault((src, dst), leg)
        daily.setdefault(dst, {}).setdefault(day, [[], []])[0].append((row_id, cents))
        daily.setdefault(src, {}).setdefault(day, [[], []])[1].append((row_id, cents))

    witnesses = {}
    for gid in sorted(graph):
        gid = int(gid)
        days = daily.get(gid, {})
        matches = _match(days, window_days, allow_same_day=True)
        strict_matches = _match(days, window_days, allow_same_day=False)
        temporal = dict(
            semantic='volume_compatibility', window_days=window_days,
            matches=matches, strict_matches=strict_matches,
            total_in_cents=sum(cents for incoming, _ in days.values() for _, cents in incoming),
            total_out_cents=sum(cents for _, outgoing in days.values() for _, cents in outgoing),
            matched_cents=sum(match['cents'] for match in matches),
            strict_matched_cents=sum(match['cents'] for match in strict_matches),
            same_day_matched_cents=sum(match['cents'] for match in matches if match['lag_days'] == 0),
            limitation=TEMPORAL_LIMITATION,
        )
        bridge = None
        for src in sorted(graph.predecessors(gid)):
            if src == gid or (src, gid) not in first_on_edge:
                continue
            for dst in sorted(graph.successors(gid)):
                if (dst in (src, gid) or clusters[src] == clusters[dst]
                        or (gid, dst) not in first_on_edge):
                    continue
                legs = [dict(first_on_edge[(src, gid)]), dict(first_on_edge[(gid, dst)])]
                bridge = dict(path=[str(src), str(gid), str(dst)],
                              transaction_row_ids=[leg['row_id'] for leg in legs],
                              legs=legs, semantic='structural_path',
                              limitation=BRIDGE_LIMITATION)
                break
            if bridge is not None:
                break
        witnesses[gid] = dict(bridge_evidence=bridge, temporal_evidence=temporal)
    return witnesses
