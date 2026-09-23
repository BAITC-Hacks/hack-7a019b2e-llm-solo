"""Observable motifs: events a card can name, instead of only metric values.

A motif is a fact about specific transfers — their gids, dates and amounts — so an
analyst can open it and check it. Nothing here scores or accuses: a money cycle is
a cycle, not laundering, and repeated equal amounts are repeated equal amounts.

Everything is deterministic. `nx.simple_cycles` does not enumerate in a stable order
across different insertion orders of the same edges, so cycles are canonicalised and
sorted before anything downstream sees them.
"""
from collections import defaultdict
import networkx as nx

CYCLE_MAX_LENGTH = 6
REPEAT_MIN_COUNT = 3
BURST_MIN_TRANSFERS = 5

MOTIF_LABELS = {
    'cycle': 'Возврат по кругу',
    'repeat_amount': 'Повтор одинаковой суммы',
    'burst': 'Всплеск за один день',
}


def _canonical(cycle):
    """Rotate a cycle so its smallest gid leads; rotations are the same cycle."""
    start = cycle.index(min(cycle))
    return tuple(cycle[start:] + cycle[:start])


def find_cycles(graph, max_length=CYCLE_MAX_LENGTH):
    """Directed cycles of length 2..max_length, canonical and stably ordered."""
    found = {_canonical(cycle) for cycle in nx.simple_cycles(graph, length_bound=max_length)
             if len(cycle) >= 2}
    return sorted(found, key=lambda cycle: (len(cycle), cycle))


def _pair_index(tx):
    """cents and dates per directed pair, each list sorted by date then amount."""
    pairs = defaultdict(list)
    for row in tx.itertuples(index=False):
        src, dst = int(row.src), int(row.dst)
        if src == dst:
            continue
        pairs[(src, dst)].append((row.date.date(), int(round(row.sum_kzt * 100))))
    for transfers in pairs.values():
        transfers.sort()
    return pairs


def _describe_cycle(cycle, pairs):
    """One concrete leg per hop: the earliest transfer that closes it."""
    legs = []
    for index, src in enumerate(cycle):
        dst = cycle[(index + 1) % len(cycle)]
        transfers = pairs.get((src, dst))
        if not transfers:
            return None
        date, cents = transfers[0]
        legs.append(dict(src=str(src), dst=str(dst), date=date.isoformat(),
                         sum_kzt=cents / 100, cents=cents, n_tx=len(transfers)))
    amounts = [leg['cents'] for leg in legs]
    dates = [leg['date'] for leg in legs]
    span = (max(dates), min(dates))
    return dict(
        members=[str(gid) for gid in cycle],
        length=len(cycle),
        legs=legs,
        # 1.0 means every hop moved the same amount — the tightest possible round trip.
        amount_agreement=round(min(amounts) / max(amounts), 4) if max(amounts) else 0.0,
        span_days=(_date(span[0]) - _date(span[1])).days,
        first_date=min(dates), last_date=max(dates))


def _date(text):
    from datetime import date
    year, month, day = (int(part) for part in text.split('-'))
    return date(year, month, day)


def _cycle_rank(described):
    """Most convincing example first: equal amounts, then tight in time, then short."""
    return (-described['amount_agreement'], described['span_days'], described['length'],
            tuple(described['members']))


def repeated_amounts(tx, min_count=REPEAT_MIN_COUNT):
    """Directed pairs that exchanged the exact same amount at least min_count times."""
    counts = defaultdict(int)
    for row in tx.itertuples(index=False):
        src, dst = int(row.src), int(row.dst)
        if src == dst:
            continue
        counts[(src, dst, int(round(row.sum_kzt * 100)))] += 1
    found = [dict(src=str(src), dst=str(dst), cents=cents, sum_kzt=cents / 100, count=count)
             for (src, dst, cents), count in counts.items() if count >= min_count]
    found.sort(key=lambda item: (-item['count'], -item['cents'], item['src'], item['dst']))
    return found


def bursts(tx, min_transfers=BURST_MIN_TRANSFERS):
    """Calendar days on which one client sent, or received, min_transfers or more."""
    counts = defaultdict(int)
    for row in tx.itertuples(index=False):
        src, dst = int(row.src), int(row.dst)
        day = row.date.date().isoformat()
        counts[(src, day, 'out')] += 1
        if dst != src:
            counts[(dst, day, 'in')] += 1
    found = [dict(gid=str(gid), date=day, direction=direction, count=count)
             for (gid, day, direction), count in counts.items() if count >= min_transfers]
    found.sort(key=lambda item: (-item['count'], item['date'], item['gid'], item['direction']))
    return found


def _summary(motif, detail):
    if motif == 'cycle':
        legs = detail['legs']
        amount = f"{legs[0]['sum_kzt']:,.0f}".replace(',', ' ')
        when = ('за один день ' + detail['first_date']) if detail['span_days'] == 0 \
            else f"с {detail['first_date']} по {detail['last_date']}"
        equal = ' одной и той же суммой' if detail['amount_agreement'] == 1.0 else ''
        return (f"Средства вернулись отправителю по кругу через {detail['length']} клиентов "
                f"{when}{equal}; первый шаг {amount} KZT")
    if motif == 'repeat_amount':
        amount = f"{detail['sum_kzt']:,.0f}".replace(',', ' ')
        side = 'отправлено' if detail['role'] == 'src' else 'получено'
        return (f"{side} {detail['count']} переводов ровно по {amount} KZT "
                f"с одним контрагентом")
    burst = f"{detail['count']} переводов за {detail['date']}"
    return ('Отправлено ' if detail['direction'] == 'out' else 'Получено ') + burst


def detect(graph, tx, max_length=CYCLE_MAX_LENGTH, min_repeat=REPEAT_MIN_COUNT,
           min_burst=BURST_MIN_TRANSFERS):
    """Per-gid motifs with one concrete, checkable example of each.

    Returns {gid: {'motifs': [...], 'motif_detail': {...}, 'motif_summary': str}}.
    A gid with no motif is absent, which is not the same as a motif of size zero.
    """
    pairs = _pair_index(tx)
    per_node = defaultdict(lambda: {'motifs': set(), 'motif_detail': {}})

    best_cycle = {}
    for cycle in find_cycles(graph, max_length):
        described = _describe_cycle(cycle, pairs)
        if described is None:
            continue
        rank = _cycle_rank(described)
        for gid in cycle:
            if gid not in best_cycle or rank < best_cycle[gid][0]:
                best_cycle[gid] = (rank, described)
    for gid, (_, described) in best_cycle.items():
        per_node[gid]['motifs'].add('cycle')
        per_node[gid]['motif_detail']['cycle'] = described

    for repeat in repeated_amounts(tx, min_repeat):
        for gid_text, role in ((repeat['src'], 'src'), (repeat['dst'], 'dst')):
            gid = int(gid_text)
            node = per_node[gid]
            if 'repeat_amount' in node['motif_detail']:
                continue
            node['motifs'].add('repeat_amount')
            node['motif_detail']['repeat_amount'] = dict(repeat, role=role)

    for burst in bursts(tx, min_burst):
        gid = int(burst['gid'])
        node = per_node[gid]
        if 'burst' in node['motif_detail']:
            continue
        node['motifs'].add('burst')
        node['motif_detail']['burst'] = burst

    result = {}
    for gid, node in per_node.items():
        motifs = sorted(node['motifs'])
        leading = 'cycle' if 'cycle' in motifs else motifs[0]
        result[gid] = dict(
            motifs=motifs,
            motif_detail={name: node['motif_detail'][name] for name in motifs},
            motif_summary=_summary(leading, node['motif_detail'][leading]))
    return result
