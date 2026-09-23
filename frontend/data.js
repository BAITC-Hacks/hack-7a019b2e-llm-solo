/* Shared, dependency-free data contract. IDs always stay strings. */
(function (root) {
  'use strict';
  const roles = {
    coordinator: { label: 'Координатор', color: '#ec8a63' },
    consolidator: { label: 'Консолидатор', color: '#c8b479' },
    distributor: { label: 'Распределитель', color: '#b6a3c9' },
    transit: { label: 'Транзит', color: '#87b3c3' },
    terminal: { label: 'Получатель', color: '#97b69f' },
    peripheral: { label: 'Периферия', color: '#a8a9a1' }
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
  function normalize(raw) {
    if (!raw || !Array.isArray(raw.nodes) || !Array.isArray(raw.edges) || !raw.nodes.length) {
      throw new Error('Файл должен содержать непустой массив nodes и массив edges.');
    }
    const nodes = raw.nodes.map(n => {
      if (!n || !Object.hasOwn(roles, n.role)) throw new Error('Неизвестная роль узла.');
      const cluster = String(n.cluster_id ?? '');
      if (!/^-?\d+$/.test(cluster)) throw new Error('Укажите cluster_id каждого узла.');
      return {
        gid: identifier(n.gid), role: n.role, cluster_id: cluster,
        role_score: score(n.role_score, 'role_score'),
        priority_score: score(n.priority_score, 'priority_score'),
        evidence: String(n.evidence ?? ''), depth: nonnegative(n.depth, 'depth'),
        is_seed: n.is_seed === true || n.is_seed === 1 || n.is_seed === 'True',
        in_kzt: nonnegative(n.in_kzt, 'in_kzt'), out_kzt: nonnegative(n.out_kzt, 'out_kzt'),
        in_deg: 0, out_deg: 0, truncated_by_depth: n.truncated_by_depth === true
      };
    });
    const byId = new Map(nodes.map(n => [n.gid, n]));
    if (byId.size !== nodes.length) throw new Error('Обнаружены повторяющиеся GID.');
    const incoming = new Map(nodes.map(n => [n.gid, []]));
    const outgoing = new Map(nodes.map(n => [n.gid, []]));
    const edges = raw.edges.map(e => {
      const edge = { src: identifier(e.src), dst: identifier(e.dst),
        sum_kzt: nonnegative(e.sum_kzt, 'sum_kzt'), n_tx: nonnegative(e.n_tx, 'n_tx') };
      if (!byId.has(edge.src) || !byId.has(edge.dst)) throw new Error('Ребро ссылается на отсутствующий GID.');
      outgoing.get(edge.src).push(edge);
      incoming.get(edge.dst).push(edge);
      return edge;
    });
    nodes.forEach(n => {
      n.in_deg = new Set(incoming.get(n.gid).map(e => e.src)).size;
      n.out_deg = new Set(outgoing.get(n.gid).map(e => e.dst)).size;
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
    return { nodes, edges, byId, incoming, outgoing, clusters };
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
  const api = { roles, parse, normalize, neighborhood, csv };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MoneyGraph = api;
})(typeof window !== 'undefined' ? window : globalThis);
