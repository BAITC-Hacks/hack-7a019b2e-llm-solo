"""Checkable motifs backed by snapshot transaction row IDs.

This adapter intentionally does not use motifs.detect or its prose: a directed
cycle proves a closed route of observed links, not a chronological return of
money. Inputs must be the graph and transactions from validate_tables, so export
ordinals match snapshot.make_snapshot, including otherwise identical operations.
"""
from collections import defaultdict

from .motifs import (BURST_MIN_TRANSFERS, CYCLE_MAX_LENGTH, REPEAT_MIN_COUNT,
                     find_cycles)


DAY_LIMITATION = ('Даты представлены календарными днями; порядок операций '
                  'внутри одного дня неизвестен.')


def _fact(kind, summary, rows, **fields):
    return dict(kind=kind, summary=summary,
                transaction_row_ids=[row['row_id'] for row in rows],
                transactions=[dict(row) for row in rows], **fields)


def build_observations(graph, tx):
    """Return motifs and one auditable example per kind for every graph gid.

    All amounts are integer hundredths of KZT; all identifiers inside facts are
    strings. row_id is an ordinal in canonical validated tx, never a bank ID.
    Self-transfers do not count toward cycles or repeat pairs; they count once
    as outgoing operations in a daily burst, never twice as incoming/outgoing.
    Observations neither change roles nor imply wrongdoing.
    """
    result = {int(gid): dict(motifs=[], observed_facts=[], motif_summary='')
              for gid in sorted(graph)}
    pairs, repeats, daily = defaultdict(list), defaultdict(list), defaultdict(list)
    columns = ['src', 'dst', 'date', '_cents']
    for row_id, (source, target, date, cents) in enumerate(
            tx[columns].itertuples(index=False, name=None)):
        src, dst, cents = int(source), int(target), int(cents)
        day = date.date().isoformat()
        row = dict(row_id=row_id, src=str(src), dst=str(dst), date=day, cents=cents)
        daily[(src, day, 'out')].append(row)
        if src != dst:
            pairs[(src, dst)].append(row)
            repeats[(src, dst, cents)].append(row)
            daily[(dst, day, 'in')].append(row)

    cycle_counts, cycle_examples = defaultdict(int), {}
    for cycle in find_cycles(graph, CYCLE_MAX_LENGTH):
        legs = [(src, cycle[(index + 1) % len(cycle)])
                for index, src in enumerate(cycle)]
        if any(pair not in pairs for pair in legs):
            continue
        # Select the first canonical transaction on each edge, without making
        # a chronology claim. Rotating the cycle cannot cure reversed dates.
        rows = [pairs[pair][0] for pair in legs]
        for gid in cycle:
            cycle_counts[gid] += 1
            cycle_examples.setdefault(gid, (cycle, rows))
    for gid, (cycle, rows) in sorted(cycle_examples.items()):
        summary = f'Замкнутый маршрут из {len(cycle)} наблюдаемых направленных связей'
        fact = _fact('cycle', summary, rows, members=[str(gid) for gid in cycle],
                     length=len(cycle), cycle_count=cycle_counts[gid],
                     max_length=CYCLE_MAX_LENGTH, chronology_checked=False,
                     limitations=[
                         'Структурный цикл не доказывает возврат тех же денег. '
                         'На каждом ребре выбран один пример, временная '
                         'совместимость между рёбрами не проверена.', DAY_LIMITATION])
        result[gid]['observed_facts'].append(fact)

    repeat_candidates = defaultdict(list)
    for (src, dst, cents), rows in repeats.items():
        if len(rows) >= REPEAT_MIN_COUNT:
            rank = (-len(rows), -cents, src, dst)
            for gid, direction in ((src, 'out'), (dst, 'in')):
                repeat_candidates[gid].append((rank, src, dst, cents, direction, rows))
    for gid, candidates in sorted(repeat_candidates.items()):
        _, src, dst, cents, direction, rows = min(candidates, key=lambda item: item[0])
        amount = f'{cents // 100}.{cents % 100:02d}'
        side = 'Отправлено' if direction == 'out' else 'Получено'
        summary = f'{side} {len(rows)} переводов по {amount} KZT с одним контрагентом'
        fact = _fact('repeat_amount', summary, rows, src=str(src), dst=str(dst),
                     cents=cents, count=len(rows), group_count=len(candidates),
                     min_count=REPEAT_MIN_COUNT, direction=direction,
                     total_cents=cents * len(rows), limitations=[
                         'Повтор суммы сам по себе не доказывает умышленное '
                         'дробление. Совпадающие строки исходных данных сохранены '
                         'как отдельные операции; row_id не является банковским ID.',
                         DAY_LIMITATION])
        result[gid]['observed_facts'].append(fact)

    burst_candidates = defaultdict(list)
    for (gid, day, direction), rows in daily.items():
        if len(rows) >= BURST_MIN_TRANSFERS:
            rank = (-len(rows), day, direction)
            burst_candidates[gid].append((rank, day, direction, rows))
    for gid, candidates in sorted(burst_candidates.items()):
        _, day, direction, rows = min(candidates, key=lambda item: item[0])
        side = 'Отправлено' if direction == 'out' else 'Получено'
        summary = f'{side} {len(rows)} переводов за {day}'
        fact = _fact('burst', summary, rows, date=day, direction=direction,
                     count=len(rows), burst_count=len(candidates),
                     min_count=BURST_MIN_TRANSFERS,
                     total_cents=sum(row['cents'] for row in rows),
                     self_transfer_count=sum(row['src'] == row['dst'] for row in rows),
                     limitations=[
                         'Это количество операций за день, а не отклонение от '
                         'известного обычного поведения клиента. Перевод самому '
                         'себе учитывается один раз в исходящих.', DAY_LIMITATION])
        result[gid]['observed_facts'].append(fact)

    for node in result.values():
        node['motifs'] = [fact['kind'] for fact in node['observed_facts']]
        node['motif_summary'] = '; '.join(fact['summary'] for fact in node['observed_facts'])
    return result
