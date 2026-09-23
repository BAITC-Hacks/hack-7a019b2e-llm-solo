/* Shared, dependency-free data contract. IDs always stay strings. */
(function (root) {
  'use strict';
  const roles = {
    coordinator: { label: 'Структурный посредник', color: '#ec8a63' },
    consolidator: { label: 'Кандидат на сбор', color: '#c8b479' },
    distributor: { label: 'Кандидат на распределение', color: '#b6a3c9' },
    transit: { label: 'Кандидат на транзит', color: '#87b3c3' },
    terminal: { label: 'Наблюдаемый конец цепочки', color: '#97b69f' },
    peripheral: { label: 'Роль не установлена', color: '#a8a9a1' }
  };
  function parse(text) {
    return JSON.parse(text.replace(/"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g,
      token => /^-?\d{16,}$/.test(token) ? '"' + token + '"' : token));
  }
  function identifier(value) {
    if (typeof value === 'number' && !Number.isSafeInteger(value)) {
      throw new Error('GID потерял точность. Передавайте идентификаторы строками.');
    }
    const id = String(value ?? '');
    if (!/^\d+$/.test(id)) throw new Error('GID должен быть целочисленным идентификатором.');
    return id;
  }
  function nonnegative(value, field) {
    const number = Number(value ?? 0);
    if (!Number.isFinite(number) || number < 0) throw new Error('Некорректное поле: ' + field);
    return number;
  }
  function score(value, field) {
    const number = nonnegative(value, field);
    if (number > 1) throw new Error(field + ' должен находиться между 0 и 1.');
    return number;
  }
  function integer(value, field) {
    const number = nonnegative(value, field);
    if (!Number.isSafeInteger(number)) throw new Error('Некорректное целое поле: ' + field);
    return number;
  }
  // Optional analysis fields stay absent when absent: zero is an observation,
  // not a replacement for a metric missing from an older imported snapshot.
  function analysisFields(n) {
    const result = {};
    for (const key of ['temporal_2d', 'temporal_in_fraction', 'temporal_out_fraction',
      'temporal_strict_in_fraction', 'temporal_strict_out_fraction']) {
      if (Object.hasOwn(n, key)) {
        if (typeof n[key] !== 'number') throw new Error('Некорректное поле: ' + key);
        result[key] = score(n[key], key);
      }
    }
    for (const key of ['temporal_matched_kzt', 'temporal_strict_matched_kzt', 'temporal_same_day_matched_kzt',
      'external_in_kzt', 'external_out_kzt', 'betweenness']) {
      if (Object.hasOwn(n, key)) {
        if (typeof n[key] !== 'number') throw new Error('Некорректное поле: ' + key);
        result[key] = nonnegative(n[key], key);
      }
    }
    for (const key of ['in_tx', 'out_tx', 'active_days', 'seed_reach', 'other_clusters',
      'component_id', 'temporal_window_days']) {
      if (Object.hasOwn(n, key)) {
        if (typeof n[key] !== 'number') throw new Error('Некорректное поле: ' + key);
        result[key] = integer(n[key], key);
      }
    }
    if (Object.hasOwn(n, 'pass_through')) {
      if (n.pass_through !== null && typeof n.pass_through !== 'number') throw new Error('Некорректное поле: pass_through');
      result.pass_through = n.pass_through === null ? null : nonnegative(n.pass_through, 'pass_through');
    }
    for (const key of ['peripheral_reason', 'peripheral_reason_text', 'limitations', 'next_query']) {
      if (Object.hasOwn(n, key)) {
        if (typeof n[key] !== 'string') throw new Error('Некорректное поле: ' + key);
        result[key] = n[key];
      }
    }
    if (Object.hasOwn(n, 'matched_roles')) {
      if (!Array.isArray(n.matched_roles) || n.matched_roles.some(role => !Object.hasOwn(roles, role))) {
        throw new Error('Некорректное поле: matched_roles');
      }
      result.matched_roles = [...n.matched_roles];
    }
    if (Object.hasOwn(n, 'priority_parts')) {
      if (!n.priority_parts || Array.isArray(n.priority_parts) || typeof n.priority_parts !== 'object') {
        throw new Error('Некорректное поле: priority_parts');
      }
      result.priority_parts = Object.fromEntries(Object.entries(n.priority_parts).map(([key, value]) => {
        if (typeof value !== 'number') throw new Error('Некорректное поле: priority_parts');
        return [key, score(value, 'priority_parts.' + key)];
      }));
    }
    return result;
  }
  function normalize(raw) {
    if (!raw || !Array.isArray(raw.nodes) || !Array.isArray(raw.edges) || !raw.nodes.length) {
      throw new Error('Файл должен содержать непустой массив nodes и массив edges.');
    }
    if (raw.schema_version !== undefined && raw.schema_version !== 1) {
      throw new Error('Неподдерживаемая schema_version. Ожидается версия 1.');
    }
    const nodes = raw.nodes.map(n => {
      if (!n || !Object.hasOwn(roles, n.role)) throw new Error('Неизвестная роль узла.');
      const cluster = String(n.cluster_id ?? '');
      if (!/^-?\d+$/.test(cluster)) throw new Error('Укажите cluster_id каждого узла.');
      return {
        gid: identifier(n.gid), role: n.role, cluster_id: cluster,
        role_score: score(n.role_score, 'role_score'),
        priority_score: score(n.priority_score, 'priority_score'),
        evidence: String(n.evidence ?? ''), depth: integer(n.depth, 'depth'),
        is_seed: n.is_seed === true || n.is_seed === 1 || n.is_seed === 'True',
        in_kzt: nonnegative(n.in_kzt, 'in_kzt'), out_kzt: nonnegative(n.out_kzt, 'out_kzt'),
        in_deg: 0, out_deg: 0, truncated_by_depth: n.truncated_by_depth === true,
        ...analysisFields(n)
      };
    });
    const byId = new Map(nodes.map(n => [n.gid, n]));
    if (byId.size !== nodes.length) throw new Error('Обнаружены повторяющиеся GID.');
    const incoming = new Map(nodes.map(n => [n.gid, []]));
    const outgoing = new Map(nodes.map(n => [n.gid, []]));
    const edges = raw.edges.map(e => {
      const edge = { src: identifier(e.src), dst: identifier(e.dst),
        sum_kzt: nonnegative(e.sum_kzt, 'sum_kzt'), n_tx: integer(e.n_tx, 'n_tx') };
      if (!byId.has(edge.src) || !byId.has(edge.dst)) throw new Error('Ребро ссылается на отсутствующий GID.');
      outgoing.get(edge.src).push(edge);
      incoming.get(edge.dst).push(edge);
      return edge;
    });
    nodes.forEach(n => {
      n.in_deg = new Set(incoming.get(n.gid).map(e => e.src).filter(id => id !== n.gid)).size;
      n.out_deg = new Set(outgoing.get(n.gid).map(e => e.dst).filter(id => id !== n.gid)).size;
      n.truncated_by_depth = n.truncated_by_depth || (n.depth >= 4 && !n.out_deg);
    });
    const clusters = new Map();
    nodes.forEach(n => {
      if (!clusters.has(n.cluster_id)) clusters.set(n.cluster_id, { id: n.cluster_id, nodes: [], internal: 0, seeds: 0 });
      const c = clusters.get(n.cluster_id);
      c.nodes.push(n);
      c.seeds += Number(n.is_seed);
    });
    edges.forEach(e => {
      const id = byId.get(e.src).cluster_id;
      if (id === byId.get(e.dst).cluster_id) clusters.get(id).internal += e.sum_kzt;
    });
    if (raw.snapshot_id !== undefined && (typeof raw.snapshot_id !== 'string' || !raw.snapshot_id)) {
      throw new Error('Некорректное поле: snapshot_id');
    }
    const graph = { nodes, edges, byId, incoming, outgoing, clusters, snapshot_id: raw.snapshot_id,
      hasTransactions: Object.hasOwn(raw, 'transactions'), transactions: [],
      transactionsById: new Map(nodes.map(n => [n.gid, []])) };
    if (graph.hasTransactions) {
      graph.transactions = normalizeTransactions(raw.transactions, graph);
      for (const tx of graph.transactions) {
        graph.transactionsById.get(tx.src).push(tx);
        if (tx.dst !== tx.src) graph.transactionsById.get(tx.dst).push(tx);
      }
    }
    if (raw.schema_version === 1) validateSnapshot(raw, graph);
    return graph;
  }
  function validateSnapshot(raw, graph) {
    if (!graph.hasTransactions || !graph.snapshot_id) {
      throw new Error('Снимок версии 1 должен содержать transactions и snapshot_id.');
    }
    const centsFor = (value, field) => {
      if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 ||
          !Number.isSafeInteger(Math.round(value * 100)) || Math.abs(value * 100 - Math.round(value * 100)) > .001) {
        throw new Error('Некорректная точность суммы: ' + field);
      }
      return Math.round(value * 100);
    };
    const totals = new Map(graph.nodes.map(n => [n.gid, { incoming: 0, outgoing: 0, inCount: 0, outCount: 0 }]));
    const byPair = new Map();
    const add = (record, key, value) => {
      record[key] += value;
      if (!Number.isSafeInteger(record[key])) throw new Error('Сумма выходит за безопасный численный диапазон.');
    };
    for (const edge of raw.edges) {
      if (typeof edge.src !== 'string' || typeof edge.dst !== 'string' ||
          typeof edge.cents !== 'number' || typeof edge.n_tx !== 'number') {
        throw new Error('Некорректные типы ребра в снимке версии 1.');
      }
      const cents = integer(edge.cents, 'edge.cents'), count = integer(edge.n_tx, 'edge.n_tx');
      if (centsFor(edge.sum_kzt, 'edge.sum_kzt') !== cents) throw new Error('Сумма ребра не совпадает с cents.');
      const key = edge.src + ',' + edge.dst;
      if (byPair.has(key)) throw new Error('Повторяется направленная пара рёбер.');
      byPair.set(key, { cents, count });
      add(totals.get(edge.src), 'outgoing', cents); add(totals.get(edge.dst), 'incoming', cents);
      add(totals.get(edge.src), 'outCount', count); add(totals.get(edge.dst), 'inCount', count);
    }
    const txPairs = new Map();
    for (const tx of graph.transactions) {
      const key = tx.src + ',' + tx.dst;
      if (!txPairs.has(key)) txPairs.set(key, { cents: 0, count: 0 });
      add(txPairs.get(key), 'cents', tx.cents); add(txPairs.get(key), 'count', 1);
    }
    if (txPairs.size !== byPair.size || [...byPair].some(([key, value]) =>
      txPairs.get(key)?.cents !== value.cents || txPairs.get(key)?.count !== value.count)) {
      throw new Error('Суммы или количества transactions не совпадают с edges.');
    }
    for (const node of raw.nodes) {
      if (typeof node.gid !== 'string' || typeof node.in_tx !== 'number' || typeof node.out_tx !== 'number') {
        throw new Error('Некорректные типы узла в снимке версии 1.');
      }
      const total = totals.get(node.gid);
      if (centsFor(node.in_kzt, 'node.in_kzt') !== total.incoming ||
          centsFor(node.out_kzt, 'node.out_kzt') !== total.outgoing ||
          integer(node.in_tx, 'node.in_tx') !== total.inCount || integer(node.out_tx, 'node.out_tx') !== total.outCount) {
        throw new Error('Обороты или количества операций узла не совпадают с edges.');
      }
    }
  }
  function normalizeTransactions(raw, graph) {
    if (!Array.isArray(raw)) throw new Error('Поле transactions должно быть массивом.');
    const items = raw.map(tx => {
      if (!tx || typeof tx.row_id !== 'number' || typeof tx.cents !== 'number' || typeof tx.sum_kzt !== 'number') {
        throw new Error('Некорректные поля перевода.');
      }
      const src = identifier(tx.src), dst = identifier(tx.dst);
      if (!graph.byId.has(src) || !graph.byId.has(dst)) {
        throw new Error('Перевод ссылается на отсутствующий GID.');
      }
      if (typeof tx.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(tx.date) ||
          !Number.isFinite(Date.parse(tx.date)) || new Date(tx.date).toISOString().slice(0, 10) !== tx.date) {
        throw new Error('Дата перевода должна иметь формат YYYY-MM-DD.');
      }
      const cents = integer(tx.cents, 'cents');
      const amount = nonnegative(tx.sum_kzt, 'sum_kzt');
      if (Math.abs(amount * 100 - cents) > 0.001) throw new Error('Сумма перевода не согласована с cents.');
      return { row_id: integer(tx.row_id, 'row_id'), src, dst, date: tx.date, cents, sum_kzt: amount };
    });
    if (new Set(items.map(tx => tx.row_id)).size !== items.length) {
      throw new Error('Повторяется row_id перевода.');
    }
    return items.sort((a, b) => a.date.localeCompare(b.date) || a.row_id - b.row_id);
  }
  function transactionPage(graph, gid, offset = 0, limit = 100) {
    if (!graph.byId.has(gid)) throw new Error('Клиент не найден.');
    integer(offset, 'offset'); integer(limit, 'limit');
    if (limit < 1 || limit > 500) throw new Error('limit должен быть от 1 до 500.');
    const items = graph.transactionsById.get(gid);
    return { items: items.slice(offset, offset + limit), total: items.length, offset };
  }
  function neighborhood(graph, gid) {
    const ids = new Set([gid]);
    (graph.incoming.get(gid) || []).forEach(e => ids.add(e.src));
    (graph.outgoing.get(gid) || []).forEach(e => ids.add(e.dst));
    return { nodes: [...ids].map(id => graph.byId.get(id)).filter(Boolean),
      edges: graph.edges.filter(e => ids.has(e.src) && ids.has(e.dst)) };
  }
  function csv(rows) {
    const columns = ['rank', 'gid', 'role', 'priority_score', 'why'];
    const quote = value => '"' + String(value).replace(/"/g, '""') + '"';
    const lines = rows.map((n, i) => [i + 1, n.gid, n.role, n.priority_score, n.evidence].map(quote).join(','));
    return '\uFEFF' + [columns.join(','), ...lines].join('\r\n');
  }
  const api = { roles, parse, normalize, transactionPage, neighborhood, csv };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MoneyGraph = api;
})(typeof window !== 'undefined' ? window : globalThis);
