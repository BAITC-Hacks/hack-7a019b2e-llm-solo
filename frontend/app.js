'use strict';

const M = window.MoneyGraph;
const $ = selector => document.querySelector(selector);
const state = { graph: null, view: 'overview', query: '', role: '', cluster: '',
  focus: null, selected: null, page: 1, sort: 'priority_score', desc: true, all: false, color: 'role', loading: true,
  source: 'local', transactions: null };
const number = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 });
const money = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmt = value => number.format(Number(value) || 0);
const cash = value => money.format(value) + ' ₸';
const pct = value => decimal.format(value * 100);
const shortCash = value => value >= 1e6 ? decimal.format(value / 1e6) + ' млн ₸' :
  value >= 1e3 ? decimal.format(value / 1e3) + ' тыс. ₸' : cash(value);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const paths = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  network: '<circle cx="5" cy="6" r="3"/><circle cx="19" cy="6" r="3"/><circle cx="12" cy="19" r="3"/><path d="m7 8 4 8m6-8-4 8M8 6h8"/>',
  priority: '<path d="M5 19V9m7 10V4m7 15v-6M3 4h4M1 6h4"/>',
  clusters: '<rect x="3" y="3" width="6" height="6" rx="2"/><rect x="15" y="15" width="6" height="6" rx="2"/><rect x="15" y="3" width="6" height="6" rx="2"/><path d="M9 6h6m3 3v6M6 9v9h9"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  filter: '<path d="M4 7h16M7 12h10m-7 5h4"/>',
  reset: '<path d="M4 10a8 8 0 1 1 1 7M4 4v6h6"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/>',
  upload: '<path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/>',
  download: '<path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5"/>',
  arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
  users: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m1-17a3 3 0 0 1 0 6m2 5a5 5 0 0 1 3 4v2"/>',
  wallet: '<rect x="3" y="5" width="18" height="15" rx="3"/><path d="M3 8V5a2 2 0 0 1 2-2h13m-1 9h4v5h-4a2.5 2.5 0 0 1 0-5Z"/>',
  warning: '<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v1"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  expand: '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>'
};
function icon(name) { return '<svg viewBox="0 0 24 24" class="icon" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + (paths[name] || paths.network) + '</svg>'; }
document.querySelectorAll('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
function badge(n) { return '<span class="role-badge" style="--role-color:' + M.roles[n.role].color + '"><i></i>' + M.roles[n.role].label + '</span>'; }
function sortRows(rows) {
  return [...rows].sort((a, b) => {
    const diff = state.sort === 'gid' ? a.gid.localeCompare(b.gid) : a[state.sort] - b[state.sort];
    return (state.desc ? -diff : diff) || a.gid.localeCompare(b.gid);
  });
}
function filtered() {
  const query = state.query.trim().toLocaleLowerCase();
  return state.graph.nodes.filter(n => (!query || (query === 'depth=4' ? n.truncated_by_depth : (n.gid + ' ' + n.role + ' ' + M.roles[n.role].label + ' ' + n.evidence).toLocaleLowerCase().includes(query))) &&
    (!state.role || n.role === state.role) && (!state.cluster || n.cluster_id === state.cluster));
}
function heading(title, subtitle, action) {
  return '<div class="panel-heading"><div><h2>' + title + '</h2><p>' + subtitle + '</p></div>' + (action || '') + '</div>';
}
function empty(title, message, action) {
  return '<div class="empty-state"><div class="empty-icon">' + icon('search') + '</div><h2>' + esc(title) + '</h2><p>' + esc(message) + '</p>' + (action || '') + '</div>';
}
function kpis(rows) {
  const ids = new Set(rows.map(n => n.gid));
  const links = state.graph.edges.filter(e => ids.has(e.src) && ids.has(e.dst));
  const seeds = rows.filter(n => n.is_seed).length;
  const clusters = new Set(rows.map(n => n.cluster_id)).size;
  return '<section class="kpis" aria-label="Показатели выборки">' + [
    ['users', 'Клиентов в сети', fmt(rows.length), '<span class="small-dot"></span>' + fmt(seeds) + ' исходных клиентов', ''],
    ['network', 'Связей между клиентами', fmt(links.length), fmt(links.reduce((sum, e) => sum + e.n_tx, 0)) + ' отдельных переводов', ''],
    ['wallet', 'Оборот внутри выборки', shortCash(links.reduce((sum, e) => sum + e.sum_kzt, 0)), 'По наблюдаемым переводам', 'money-kpi'],
    ['clusters', 'Финансовых кластеров', fmt(clusters), 'Группы связанных клиентов', '']
  ].map(([glyph, label, value, detail, cls], i) => '<article class="kpi ' + cls + '"><div class="kpi-top"><span class="metric-index">0' + (i + 1) + '</span><span>' + label + '</span></div><strong>' + value + '</strong><p>' + detail + '</p></article>').join('') + '</section>';
}
function rolePanel(rows) {
  const counts = Object.fromEntries(Object.keys(M.roles).map(role => [role, rows.filter(n => n.role === role).length]));
  const total = rows.length || 1;
  return '<section class="role-panel">' + heading('Анатомия сети', 'Роль — гипотеза алгоритма') +
    '<div class="role-composition" aria-hidden="true">' + Object.keys(counts).map(role => '<i style="width:' + counts[role] / total * 100 + '%;background:' + M.roles[role].color + '"></i>').join('') + '</div>' +
    '<div class="role-list">' + Object.keys(M.roles).map((role, i) => '<button data-role="' + role + '" class="role-row"><span class="role-number">0' + (i + 1) + '</span><span><i style="background:' + M.roles[role].color + '"></i>' + M.roles[role].label + '</span><b>' + fmt(counts[role]) + '</b><small>' + Math.round(counts[role] / total * 100) + '%</small></button>').join('') + '</div></section>';
}
function leadPanel(rows) {
  const n = [...rows].sort((a, b) => b.priority_score - a.priority_score || a.gid.localeCompare(b.gid))[0];
  if (!n) return '';
  return '<section class="lead-panel"><div class="note-label"><span class="note-pin" aria-hidden="true"></span> НАЧАЛЬНАЯ ТОЧКА <span>↗</span></div><h2>Начните с этого узла</h2><button class="lead-gid" data-node="' + n.gid + '">' + n.gid + icon('arrow') + '</button><div class="lead-meta">' + badge(n) + '<span><b>' + pct(n.priority_score) + '</b> / 100</span></div><p class="lead-evidence">' + esc(n.evidence || 'Обоснование не передано источником.') + '</p><button class="text-button" data-focus="' + n.gid + '">Раскрыть окружение ' + icon('arrow') + '</button></section>';
}
function table(rows, compact) {
  if (!rows.length) return empty('Ничего не найдено', 'Попробуйте другой GID или сбросьте фильтры.', '<button class="button secondary" data-action="reset">Сбросить фильтры</button>');
  const pageSize = 20;
  const pageCount = Math.ceil(rows.length / pageSize);
  state.page = Math.max(1, Math.min(state.page, pageCount));
  const start = compact ? 0 : (state.page - 1) * pageSize;
  const shown = rows.slice(start, start + (compact ? 5 : pageSize));
  const sortHeader = (label, key) => '<th aria-sort="' + (state.sort === key ? (state.desc ? 'descending' : 'ascending') : 'none') + '"><button data-sort="' + key + '">' + label + '<span>' + (state.sort === key ? (state.desc ? '↓' : '↑') : '↕') + '</span></button></th>';
  return '<div class="table-wrap"><table class="data-table"><thead><tr><th class="rank-column">#</th>' +
    sortHeader('Клиент / GID', 'gid') + '<th>Роль в сети</th>' + sortHeader('Приоритет', 'priority_score') +
    (compact ? sortHeader('Входящий оборот', 'in_kzt') : sortHeader('Входящий оборот', 'in_kzt') + sortHeader('Исходящий оборот', 'out_kzt') + '<th>Кластер</th>') +
    '<th><span class="sr-only">Действия</span></th></tr></thead><tbody>' +
    shown.map((n, i) => '<tr><td class="row-rank">' + String(start + i + 1).padStart(2, '0') + '</td><td><button class="gid-button" data-node="' + n.gid + '">' + n.gid + '</button><div class="node-subtitle">' + (n.is_seed ? '<span class="seed-tag">SEED</span>' : 'Уровень ' + n.depth) + (n.truncated_by_depth ? '<span class="truncation-tag">Граница данных</span>' : '') + '</div></td><td>' + badge(n) + '</td><td><div class="score-cell"><b>' + pct(n.priority_score) + '<small>/100</small></b><span><i style="width:' + n.priority_score * 100 + '%"></i></span></div></td><td class="amount">' + cash(n.in_kzt) + '</td>' +
      (compact ? '' : '<td class="amount">' + cash(n.out_kzt) + '</td><td><button class="cluster-link" data-cluster="' + n.cluster_id + '">#' + n.cluster_id + '</button></td>') +
      '<td><button class="row-open icon-button" data-node="' + n.gid + '" aria-label="Карточка клиента ' + n.gid + '">' + icon('chevron') + '</button></td></tr>').join('') +
    '</tbody></table></div>' + (compact ? '<div class="table-footer"><span>Первые 5 клиентов по выбранной сортировке</span><button class="text-button" data-view="priority">Весь список ' + icon('arrow') + '</button></div>' :
      '<div class="table-footer"><span>' + fmt(start + 1) + '–' + fmt(Math.min(start + pageSize, rows.length)) + ' из ' + fmt(rows.length) + '</span><div class="pagination"><button class="button secondary small" data-page="-1" ' + (state.page === 1 ? 'disabled' : '') + '>Назад</button><span>' + state.page + ' / ' + pageCount + '</span><button class="button secondary small" data-page="1" ' + (state.page === pageCount ? 'disabled' : '') + '>Далее</button></div></div>');
}
function hash(text) { let value = 2166136261; for (const c of text) value = Math.imul(value ^ c.charCodeAt(0), 16777619); return value >>> 0; }
function clusterColor(id) { return 'hsl(' + (hash(id) % 360) + ' 42% 49%)'; }
let sceneCache = new Map();
function scene(preview) {
  const exact = state.graph.byId.has(state.query.trim()) ? state.query.trim() : null;
  const focus = exact || state.focus;
  let nodes, edges, total, context = false;
  if (focus) {
    ({ nodes, edges } = M.neighborhood(state.graph, focus));
    total = nodes.length; context = true;
  } else {
    nodes = filtered().sort((a, b) => b.priority_score - a.priority_score || a.gid.localeCompare(b.gid));
    total = nodes.length;
    nodes = nodes.slice(0, preview ? 150 : state.all ? nodes.length : 180);
    const ids = new Set(nodes.map(n => n.gid));
    edges = state.graph.edges.filter(e => ids.has(e.src) && ids.has(e.dst));
  }
  const cacheKey = nodes.map(n => n.gid).join(',');
  let positions = sceneCache.get(cacheKey);
  if (!positions) {
    const groups = [...new Set(nodes.map(n => n.cluster_id))];
    const centers = new Map(groups.map((id, i) => [id, { x: 450 + Math.cos(i * 2.39996) * Math.sqrt(i / Math.max(groups.length, 1)) * 350, y: 240 + Math.sin(i * 2.39996) * Math.sqrt(i / Math.max(groups.length, 1)) * 190 }]));
    const used = new Map();
    positions = nodes.map(n => {
      const center = centers.get(n.cluster_id), i = used.get(n.cluster_id) || 0;
      used.set(n.cluster_id, i + 1);
      const a = hash(n.gid) / 4294967296 * Math.PI * 2;
      return { id: n.gid, x: center.x + Math.cos(a) * (14 + Math.sqrt(i) * 10), y: center.y + Math.sin(a) * (14 + Math.sqrt(i) * 10) };
    });
    const indices = new Map(positions.map((p, i) => [p.id, i]));
    const pairs = edges.map(e => [indices.get(e.src), indices.get(e.dst)]);
    if (nodes.length <= 250) {
      for (let step = 0; step < 180; step++) {
        const dx = new Float64Array(nodes.length), dy = new Float64Array(nodes.length);
        for (let i = 0; i < positions.length; i++) {
          for (let j = i + 1; j < positions.length; j++) {
            let x = positions[i].x - positions[j].x, y = positions[i].y - positions[j].y;
            if (x === 0 && y === 0) x = .1;
            const d2 = Math.max(x * x + y * y, 30);
            const force = 200 / d2;
            dx[i] += x * force; dy[i] += y * force; dx[j] -= x * force; dy[j] -= y * force;
          }
        }
        pairs.forEach(([a, b]) => {
          const x = positions[b].x - positions[a].x, y = positions[b].y - positions[a].y;
          const distance = Math.hypot(x, y) || 1;
          const force = (distance - 55) * .06;
          dx[a] += x / distance * force; dy[a] += y / distance * force;
          dx[b] -= x / distance * force; dy[b] -= y / distance * force;
        });
        positions.forEach((p, i) => {
          const center = centers.get(nodes[i].cluster_id);
          const cooling = 8 * (1 - step / 180) + .15;
          p.x += Math.max(-cooling, Math.min(cooling, dx[i] + (center.x - p.x) * .008));
          p.y += Math.max(-cooling, Math.min(cooling, dy[i] + (center.y - p.y) * .008));
        });
      }
    }
    if (positions.length) {
      const xs = positions.map(p => p.x), ys = positions.map(p => p.y);
      const minX = Math.min(...xs), minY = Math.min(...ys);
      const rangeX = Math.max(...xs) - minX || 1, rangeY = Math.max(...ys) - minY || 1;
      const scaleX = Math.min(770 / rangeX, 2.4), scaleY = Math.min(350 / rangeY, 2.4);
      positions.forEach(p => { p.x = 450 + (p.x - minX - rangeX / 2) * scaleX; p.y = 225 + (p.y - minY - rangeY / 2) * scaleY; });
    }
    if (sceneCache.size > 8) sceneCache.clear();
    sceneCache.set(cacheKey, positions);
  }
  return { nodes, edges, positions, total, focus, context };
}
function graphPanel(preview) {
  const s = scene(preview);
  const positions = new Map(s.positions.map(p => [p.id, p]));
  const radius = n => (n.gid === s.focus ? 12 : 3.2 + n.priority_score * 9);
  const id = preview ? 'preview-arrow' : 'network-arrow';
  const edgeMarkup = s.edges.map(e => {
    const a = positions.get(e.src), b = positions.get(e.dst);
    if (e.src === e.dst) return '<path class="graph-edge" data-src="' + e.src + '" data-dst="' + e.dst + '" d="M' + a.x + ' ' + (a.y - 6) + 'c-25-25 25-25 5 0" marker-end="url(#' + id + ')"><title>' + e.src + ' → ' + e.dst + ' · ' + cash(e.sum_kzt) + '</title></path>';
    const d = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    const r = radius(state.graph.byId.get(e.dst)) + 5, sourceR = radius(state.graph.byId.get(e.src));
    return '<path class="graph-edge" data-src="' + e.src + '" data-dst="' + e.dst + '" d="M' + (a.x + (b.x - a.x) / d * sourceR) + ' ' + (a.y + (b.y - a.y) / d * sourceR) + 'L' + (b.x - (b.x - a.x) / d * r) + ' ' + (b.y - (b.y - a.y) / d * r) + '" marker-end="url(#' + id + ')" style="stroke-width:' + Math.min(2.4, .55 + Math.log1p(e.sum_kzt) / 14) + '"><title>' + e.src + ' → ' + e.dst + ' · ' + cash(e.sum_kzt) + '</title></path>';
  }).join('');
  const nodes = s.nodes.map((n, i) => {
    const p = positions.get(n.gid), r = radius(n), fill = state.color === 'cluster' && !preview ? clusterColor(n.cluster_id) : M.roles[n.role].color;
    return '<g class="graph-node" role="button" tabindex="0" aria-label="Клиент ' + n.gid + ', ' + M.roles[n.role].label + '" data-node="' + n.gid + '" transform="translate(' + p.x + ',' + p.y + ')">' +
      (n.is_seed ? '<circle class="seed-ring" r="' + (r + 3) + '"/>' : '') +
      (n.gid === s.focus ? '<circle class="focus-ring" r="' + (r + 7) + '"/>' : '') +
      '<circle r="' + r + '" fill="' + fill + '"/><title>' + n.gid + ' · ' + M.roles[n.role].label + '</title>' +
      (n.gid === s.focus || (i < 7 && !preview) ? '<text y="' + (-r - 8) + '" text-anchor="middle">…' + n.gid.slice(-7) + '</text>' : '') + '</g>';
  }).join('');
  const modeText = s.context ? 'Окружение клиента · ' + fmt(s.nodes.length) + ' узлов' : fmt(s.nodes.length) + ' из ' + fmt(s.total) + ' узлов · ' + fmt(s.edges.length) + ' связей';
  return '<section class="panel graph-panel ' + (preview ? 'preview' : 'full-graph') + '">' +
    heading('<span class="chart-index">A—01</span>' + (s.context ? 'Связи выбранного клиента' : 'Карта денежных потоков'), modeText,
      preview ? '<button class="text-button" data-view="network">Исследовать ' + icon('arrow') + '</button>' :
      '<div class="segmented"><button data-color="role" aria-pressed="' + (state.color === 'role') + '" class="' + (state.color === 'role' ? 'selected' : '') + '">По ролям</button><button data-color="cluster" aria-pressed="' + (state.color === 'cluster') + '" class="' + (state.color === 'cluster' ? 'selected' : '') + '">По кластерам</button></div>') +
    (s.context ? '<div class="context-strip">GID ' + s.focus + ' · Все прямые соседи показаны независимо от фильтров.</div>' : '') +
    '<div class="graph-canvas"><div class="map-register" aria-hidden="true"><span>TRACE / NETWORK STUDY</span><span>НАПРАВЛЕННЫЕ СВЯЗИ ↗</span></div>' + (!s.nodes.length ? empty('Нет узлов для отображения', 'Сбросьте фильтры или уточните поиск.') :
      '<svg id="graph-svg" class="graph-svg" viewBox="0 0 900 450" aria-label="Направленный граф переводов"><defs><marker id="' + id + '" viewBox="0 0 6 6" refX="5" refY="3" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M0 0 6 3 0 6Z" fill="#a3b7ac"/></marker></defs><g id="graph-viewport">' + edgeMarkup + nodes + '</g></svg>') +
    (!preview && s.nodes.length ? '<div class="zoom-tools"><button class="icon-button" data-zoom="in" aria-label="Приблизить">' + icon('plus') + '</button><span id="zoom-value">100%</span><button class="icon-button" data-zoom="out" aria-label="Отдалить">' + icon('minus') + '</button><button class="icon-button" data-zoom="fit" aria-label="Показать весь граф">' + icon('expand') + '</button></div>' : '') +
    '<div class="canvas-caption"><i></i>' + (preview ? 'Нажмите на узел, чтобы открыть карточку' : 'Колесо — масштаб · перетаскивание — навигация') + '</div></div>' +
    '<div class="graph-legend">' + (state.color === 'cluster' && !preview ? [...new Set(s.nodes.map(n => n.cluster_id))].slice(0, 12).map(id => '<span><i style="background:' + clusterColor(id) + '"></i>Кластер #' + id + '</span>').join('') + (new Set(s.nodes.map(n => n.cluster_id)).size > 12 ? '<span>Остальные — в карточках клиентов</span>' : '') : Object.keys(M.roles).map(role => '<span><i style="background:' + M.roles[role].color + '"></i>' + M.roles[role].label + '</span>').join('')) + '</div>' +
    (!preview ? '<div class="graph-footer"><span>Кольцо — исходный клиент (seed). Стрелка — направление перевода.</span>' +
      (s.context ? '<button class="text-button" data-action="reset">Вернуться к сети</button>' : '<button class="text-button" data-action="all">' + (state.all ? 'Показать ключевые узлы' : 'Показать все ' + fmt(s.total) + ' узлов') + icon('arrow') + '</button>') + '</div>' : '') + '</section>';
}
function overview(rows) {
  const cut = rows.filter(n => n.truncated_by_depth).length;
  return kpis(rows) + '<div class="analysis-grid">' + graphPanel(true) + '<aside class="analyst-rail" aria-label="Заметки аналитика">' + leadPanel(rows) + rolePanel(rows) + '</aside></div>' +
    '<div class="data-notice"><span class="notice-index">! / ПРИМЕЧАНИЕ</span><div><strong>' + fmt(cut) + ' узлов на границе наблюдения</strong><span>На 4-м уровне обход заканчивается. Отсутствие исходящих не доказывает, что деньги остались на счёте.</span></div><button class="text-button" data-action="boundary">Проверить границу ' + icon('arrow') + '</button></div>' +
    '<section class="panel ledger-panel">' + heading('<span class="section-label">B / РЕЕСТР</span>Очередь внимания', 'Приоритет и роль рассчитаны аналитическим пайплайном', '<span class="label-pill">ПЕРВЫЕ 5</span>') + table(sortRows(rows), true) + '</section>';
}
function clusterView(rows) {
  const ids = new Set(rows.map(n => n.cluster_id));
  const groups = [...state.graph.clusters.values()].filter(c => ids.has(c.id)).sort((a, b) => b.nodes.length - a.nodes.length);
  if (!groups.length) return empty('Кластеры не найдены', 'Попробуйте изменить фильтры.');
  return '<div class="section-line"><span><strong>' + groups.length + '</strong> кластеров в выборке</span><small>Показатели рассчитаны по полному составу каждого кластера</small></div><div class="cluster-grid">' +
    groups.map(c => {
      const top = [...c.nodes].sort((a, b) => b.priority_score - a.priority_score).slice(0, 3);
      const dominant = Object.keys(M.roles).sort((a, b) => c.nodes.filter(n => n.role === b).length - c.nodes.filter(n => n.role === a).length)[0];
      return '<article class="panel cluster-card"><div class="cluster-card-top"><span class="cluster-symbol" style="--cluster-color:' + clusterColor(c.id) + '">' + icon('clusters') + '</span><span class="label-pill">' + c.seeds + ' SEED</span></div><h2>Кластер #' + c.id + '</h2><div class="cluster-metrics"><div><strong>' + fmt(c.nodes.length) + '</strong><span>клиентов</span></div><div><strong>' + shortCash(c.internal) + '</strong><span>внутренний оборот</span></div></div><p class="cluster-description">Чаще встречается: ' + M.roles[dominant].label.toLowerCase() + '. ' + (c.seeds > 1 ? 'Общий контур нескольких исходных клиентов.' : 'Назначение группы требует проверки.') + '</p><div class="cluster-top-nodes">' + top.map(n => '<button data-node="' + n.gid + '" title="' + n.gid + '">…' + n.gid.slice(-8) + '</button>').join('') + '</div><button class="cluster-explore" data-cluster="' + c.id + '">Исследовать кластер ' + icon('arrow') + '</button></article>';
    }).join('') + '</div>';
}
function render() {
  const titles = { overview: ['Денежный след.', 'От отдельного перевода — к структуре связей.', 'Обзор'],
    network: ['Связи решают.', 'Раскройте окружение клиента. Проследите направление денег.', 'Граф переводов'],
    priority: ['В фокусе проверки.', 'Не обвинение. Обоснованная точка для следующего вопроса.', 'Приоритет проверки'],
    clusters: ['Контуры сети.', 'Кто связан между собой — и что проходит внутри группы.', 'Кластеры'] };
  const [title, description, crumb] = titles[state.view];
  $('#page-title').textContent = title; $('#page-description').textContent = description; $('#breadcrumb').textContent = crumb;
  $('#section-index').textContent = '0' + (Object.keys(titles).indexOf(state.view) + 1);
  document.querySelectorAll('.nav-item').forEach(b => { b.classList.toggle('active', b.dataset.view === state.view); if (b.dataset.view === state.view) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current'); });
  if (!state.graph) {
    if (!state.loading) $('#app').innerHTML = empty('Подключите данные расследования', state.error || 'Загрузите graph.json с результатами анализа.', '<button class="button primary" data-action="import">' + icon('upload') + 'Выбрать файл</button><button class="text-button" data-action="retry">Повторить загрузку</button>');
    return;
  }
  const rows = filtered();
  $('#export-btn').disabled = !rows.length;
  $('#filter-summary').hidden = !state.query && !state.role && !state.cluster;
  $('#filter-summary').textContent = 'Найдено ' + fmt(rows.length) + ' из ' + fmt(state.graph.nodes.length) + ' клиентов';
  $('#app').innerHTML = state.view === 'overview' ? overview(rows) : state.view === 'network' ? graphPanel(false) :
    state.view === 'priority' ? '<section class="panel">' + heading('Клиенты для проверки', fmt(rows.length) + ' клиентов · нажмите на GID для подробностей', '<span class="label-pill">ПРИОРИТЕТ ≠ ВИНОВНОСТЬ</span>') + table(sortRows(rows), false) + '</section>' : clusterView(rows);
  bindGraphHighlight();
  if (state.view === 'network') bindGraph();
}
function timingDetail(n) {
  if (n.temporal_in_fraction === undefined || n.temporal_out_fraction === undefined) {
    return '<p class="muted">Временные метрики не переданы источником. Месячного отношения выхода к входу недостаточно для вывода о транзите.</p>';
  }
  const windowLabel = n.temporal_window_days === undefined ? 'в окне расчёта' : 'за 0–' + n.temporal_window_days + ' календарных дней';
  return '<h3>Время и объём</h3><div class="evidence"><p>Совместимый объём ' + windowLabel +
    (n.temporal_matched_kzt === undefined ? '' : ': <strong>' + cash(n.temporal_matched_kzt) + '</strong>') +
    '. Доля входа <strong>' + pct(n.temporal_in_fraction) + '%</strong>; доля выхода <strong>' + pct(n.temporal_out_fraction) + '%</strong>.' +
    (n.temporal_strict_in_fraction === undefined || n.temporal_strict_out_fraction === undefined ? '' :
      '<br>Без совпадений внутри дня: ' + pct(n.temporal_strict_in_fraction) + '% входа и ' + pct(n.temporal_strict_out_fraction) + '% выхода.') +
    '<br>Сопоставление допускает дробление и объединение сумм. Очерёдность внутри дня и происхождение тех же денег неизвестны.</p></div>';
}
function priorityDetail(n) {
  if (!n.priority_parts) return '';
  const labels = { seed_reach: 'Связь с seed', flow: 'Оборот', bridge: 'Посредничество', fan: 'Контрагенты', role_support: 'Правило роли' };
  return '<p class="score-explanation">Вклад в приоритет: ' + Object.entries(n.priority_parts)
    .map(([key, value]) => esc(labels[key] || key) + ' ' + pct(value) + ' / 100').join('; ') + '.</p>';
}
function transactionDetail(n) {
  const title = '<h3>Отдельные переводы</h3>';
  if (state.source !== 'api') return title + '<p class="muted">Локальный graph.json содержит агрегированные связи. Полные операции не загружены; запрос к другому набору на сервере не выполняется.</p>';
  const page = state.transactions;
  if (!page || page.loading) return title + '<p class="muted" role="status">Загружаем переводы…</p>';
  if (page.error) return title + '<div class="detail-warning"><p>' + esc(page.error) + '</p></div><button class="button secondary small" data-tx-offset="' + page.offset + '">Повторить загрузку переводов</button>';
  const rows = page.items.map(tx => '<tr><td>' + tx.date + '<br><small>Строка ' + tx.row_id + '</small></td><td>' +
    (tx.src === n.gid && tx.dst === n.gid ? 'Самоперевод' : tx.src === n.gid ? 'Исходящий' : 'Входящий') +
    '<br><button class="gid-button" data-node="' + (tx.src === n.gid ? tx.dst : tx.src) + '">' + (tx.src === n.gid ? tx.dst : tx.src) +
    '</button></td><td class="amount">' + cash(tx.sum_kzt) + '</td></tr>').join('');
  return title + '<p class="score-explanation">Даты без времени суток. Номер строки — идентификатор внутри этой выгрузки, не банковский ID. Совпадающие операции сохранены.</p>' +
    (rows ? '<div class="table-wrap"><table class="data-table"><thead><tr><th>Дата / строка</th><th>Направление / контрагент</th><th>Сумма</th></tr></thead><tbody>' + rows + '</tbody></table></div>' : '<p class="muted">Переводов в наборе нет.</p>') +
    '<div class="table-footer"><span>' + (page.total ? page.offset + 1 : 0) + '–' + (page.offset + page.items.length) + ' из ' + page.total + '</span><div class="pagination">' +
    '<button class="button secondary small" data-tx-offset="' + Math.max(0, page.offset - 100) + '" ' + (page.offset === 0 ? 'disabled' : '') + '>Назад</button>' +
    '<button class="button secondary small" data-tx-offset="' + (page.offset + 100) + '" ' + (page.offset + page.items.length >= page.total ? 'disabled' : '') + '>Далее</button></div></div>';
}
function renderDetail() {
  const n = state.graph.byId.get(state.selected);
  if (!n) return;
  const connections = [...state.graph.incoming.get(n.gid).map(e => ({ ...e, id: e.src, direction: 'Входящий' })),
    ...state.graph.outgoing.get(n.gid).map(e => ({ ...e, id: e.dst, direction: 'Исходящий' }))].sort((a, b) => b.sum_kzt - a.sum_kzt);
  $('#node-detail').innerHTML = '<div class="detail-top"><span class="eyebrow">ПРОФИЛЬ КЛИЕНТА</span><button class="icon-button" data-action="close" aria-label="Закрыть карточку">' + icon('close') + '</button></div>' +
    '<h2 id="detail-title">' + n.gid + '</h2><div class="detail-tags">' + badge(n) + '<span class="label-pill">УРОВЕНЬ ' + n.depth + '</span>' + (n.is_seed ? '<span class="seed-tag">SEED</span>' : '') + '<button class="icon-button" data-action="copy" aria-label="Скопировать GID">' + icon('copy') + '</button></div>' +
    '<div class="detail-score"><div><span>Приоритет проверки</span><strong>' + pct(n.priority_score) + '<small>/ 100</small></strong></div><div><span>Сила правила роли</span><strong>' + pct(n.role_score) + '<small>/ 100</small></strong></div></div><p class="score-explanation">Скор отражает критерии алгоритма, а не вероятность виновности.</p>' + priorityDetail(n) +
    '<h3>Почему этот узел</h3><div class="evidence">' + icon('search') + '<p>' + esc(n.evidence || 'Обоснование не передано источником данных.') + '</p></div>' +
    (n.peripheral_reason_text ? '<p class="score-explanation">' + esc(n.peripheral_reason_text) + '</p>' : '') +
    (n.matched_roles?.length ? '<p class="score-explanation">Сработавшие правила: ' + n.matched_roles.map(role => esc(M.roles[role].label)).join(', ') + '.</p>' : '') +
    (n.limitations ? '<div class="detail-warning"><p>' + esc(n.limitations) + '</p></div>' : '') +
    (n.truncated_by_depth ? '<div class="detail-warning">' + icon('warning') + '<p><strong>Граница выгрузки</strong>Нет исходящих на 4-м уровне. Для вывода о получателе нужны переводы за пределами обхода.</p></div>' : '') +
    (n.is_seed ? '<div class="detail-warning"><p><strong>Неполный входящий поток</strong>Для seed-клиента видны не все поступления. Обороты не являются балансом счёта.</p></div>' : '') +
    '<h3>Движение средств</h3><div class="flow-cards"><div><span>Входящий оборот</span><strong>' + cash(n.in_kzt) + '</strong><small>От ' + n.in_deg + ' контрагентов</small></div><div><span>Исходящий оборот</span><strong>' + cash(n.out_kzt) + '</strong><small>На ' + n.out_deg + ' контрагентов</small></div></div>' +
    (typeof n.pass_through === 'number' ? '<p class="score-explanation">Наблюдаемый выход / вход за весь период: ' + n.pass_through.toFixed(3) + '. Обороты не являются балансом счёта.</p>' : '') + timingDetail(n) +
    '<button class="button primary full-width" data-focus="' + n.gid + '">' + icon('network') + 'Показать связи на графе</button>' +
    '<div class="detail-section-heading"><h3>Связи за весь период</h3><span>' + connections.length + '</span></div><div class="connection-list">' +
    (connections.length ? connections.map(e => '<button class="connection" data-node="' + e.id + '"><span><strong>' + e.id + '</strong><small>' + e.direction + ' · ' + e.n_tx + ' переводов</small></span><b>' + cash(e.sum_kzt) + '</b>' + icon('chevron') + '</button>').join('') : '<p class="muted">Переводы отсутствуют в доступной выборке.</p>') +
    '</div>' + transactionDetail(n) + '<div class="detail-footer">Кластер #' + n.cluster_id + ' · Обезличенный идентификатор</div>';
}
let transactionTicket = 0, transactionController;
function cancelTransactions() {
  ++transactionTicket;
  transactionController?.abort();
  state.transactions = null;
}
async function loadTransactions(offset = 0) {
  if (state.source !== 'api' || !state.selected) return;
  cancelTransactions();
  const ticket = transactionTicket, graph = state.graph, gid = state.selected;
  const controller = transactionController = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  const current = () => ticket === transactionTicket && graph === state.graph && gid === state.selected && state.source === 'api';
  state.transactions = { loading: true, offset }; renderDetail();
  try {
    const response = await fetch('/api/nodes/' + encodeURIComponent(gid) + '/transactions?offset=' + offset + '&limit=100', { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error('Не удалось загрузить переводы (HTTP ' + response.status + ').');
    if (graph.snapshot_id && response.headers.get('X-Snapshot-ID') !== graph.snapshot_id) {
      throw new Error('Набор на сервере изменился. Обновите страницу перед просмотром переводов.');
    }
    const page = M.transactions(M.parse(await response.text()), gid, graph);
    if (page.items.length > 100 || page.items.length !== Math.min(100, Math.max(0, page.total - offset))) {
      throw new Error('Ответ не соответствует запрошенной странице переводов.');
    }
    if (!current()) return;
    state.transactions = { ...page, offset }; renderDetail();
  } catch (error) {
    if (!current()) return;
    state.transactions = { offset, error: error.name === 'AbortError' ? 'Сервер не ответил за 15 секунд. Повторите загрузку.' : error.message };
    renderDetail();
  } finally { clearTimeout(timer); }
}
function openNode(id) {
  if (!state.graph.byId.has(id)) return;
  cancelTransactions();
  state.selected = id; renderDetail();
  if (!$('#node-dialog').open) $('#node-dialog').showModal();
  $('#node-dialog').scrollTop = 0;
  if (state.source === 'api') loadTransactions();
}
let toastTimer;
function toast(message) {
  $('#toast').textContent = message; $('#toast').hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 4200);
}
function syncFilters() {
  $('#search').value = state.query; $('#role-filter').value = state.role; $('#cluster-filter').value = state.cluster;
}
function reset() { state.query = state.role = state.cluster = ''; state.focus = null; state.page = 1; syncFilters(); render(); }
function switchView(view) { state.view = view; state.page = 1; render(); window.scrollTo({ top: 0, behavior: 'instant' }); }
function installGraph(graph, name, source = 'local') {
  cancelTransactions();
  state.graph = graph; state.loading = false; state.error = ''; state.selected = null; state.focus = null; state.source = source;
  if ($('#node-dialog').open) $('#node-dialog').close();
  sceneCache.clear();
  $('#cluster-filter').innerHTML = '<option value="">Все кластеры</option>' +
    [...graph.clusters.keys()].sort((a, b) => Number(a) - Number(b)).map(id => '<option value="' + id + '">Кластер #' + id + '</option>').join('');
  $('#nav-cluster-count').textContent = graph.clusters.size;
  $('#scope-depth').textContent = graph.nodes.reduce((depth, n) => Math.max(depth, n.depth), 0);
  $('#source-status').textContent = fmt(graph.nodes.length) + ' клиентов · данные загружены';
  $('#source-status').title = name;
  $('.top-status').classList.add('connected');
  reset();
}
let loadTicket = 0, loadController;
async function loadDefault() {
  const ticket = ++loadTicket;
  loadController?.abort();
  const controller = loadController = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  state.loading = true; state.error = '';
  $('#source-status').textContent = 'Подключение к локальному API';
  if (!state.graph) $('#app').innerHTML = '<div class="loading-state"><span class="spinner"></span><h2>Загружаем сеть</h2><p>Локальный API /api/graph</p></div>';
  try {
    const response = await fetch('/api/graph', { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error('API /api/graph недоступен (HTTP ' + response.status + '). Запустите локальный backend или импортируйте graph.json.');
    const graph = M.normalize(M.parse(await response.text()));
    if (ticket === loadTicket) installGraph(graph, '/api/graph', 'api');
  } catch (error) {
    if (ticket !== loadTicket) return;
    state.loading = false; state.error = error.name === 'AbortError' ? 'Локальный API не ответил за 15 секунд. Повторите загрузку или импортируйте graph.json.' : error.message;
    $('#source-status').textContent = 'Ожидаем данные'; render();
  } finally { clearTimeout(timer); }
}
function downloadList() {
  const rows = sortRows(filtered());
  const url = URL.createObjectURL(new Blob([M.csv(rows)], { type: 'text/csv;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'trace_priority.csv'; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('Экспортировано клиентов: ' + fmt(rows.length));
}
let graphWasDragged = false;
let graphController;
function bindGraphHighlight() {
  const svg = $('#graph-svg');
  if (!svg) return;
  let active = null;
  const highlight = gid => {
    if (gid === active) return;
    active = gid;
    const neighbors = new Set([gid]);
    if (gid) {
      state.graph.incoming.get(gid).forEach(e => neighbors.add(e.src));
      state.graph.outgoing.get(gid).forEach(e => neighbors.add(e.dst));
    }
    svg.querySelectorAll('.graph-node').forEach(el => el.classList.toggle('is-muted', !!gid && !neighbors.has(el.dataset.node)));
    svg.querySelectorAll('.graph-edge').forEach(el => {
      const linked = !!gid && (el.dataset.src === gid || el.dataset.dst === gid);
      el.classList.toggle('is-linked', linked);
      el.classList.toggle('is-muted', !!gid && !linked);
    });
  };
  svg.addEventListener('pointerover', event => highlight(event.target.closest('.graph-node')?.dataset.node || null));
  svg.addEventListener('pointerleave', () => highlight(null));
  svg.addEventListener('focusin', event => highlight(event.target.closest('.graph-node')?.dataset.node || null));
  svg.addEventListener('focusout', () => highlight(null));
}
function bindGraph() {
  const svg = $('#graph-svg'), viewport = $('#graph-viewport');
  if (!svg || !viewport) { graphController = null; return; }
  let zoom = 1, x = 0, y = 0, drag = null;
  const transform = () => { viewport.setAttribute('transform', 'translate(' + x + ',' + y + ') scale(' + zoom + ')'); $('#zoom-value').textContent = Math.round(zoom * 100) + '%'; };
  const point = event => { const p = svg.createSVGPoint(); p.x = event.clientX; p.y = event.clientY; return p.matrixTransform(svg.getScreenCTM().inverse()); };
  const scale = (factor, center) => { const next = Math.max(.35, Math.min(zoom * factor, 8)); x = center.x - (center.x - x) * next / zoom; y = center.y - (center.y - y) * next / zoom; zoom = next; transform(); };
  graphController = action => { if (action === 'fit') { zoom = 1; x = y = 0; transform(); } else scale(action === 'in' ? 1.3 : 1 / 1.3, { x: 450, y: 225 }); };
  svg.addEventListener('wheel', event => { event.preventDefault(); scale(event.deltaY < 0 ? 1.12 : 1 / 1.12, point(event)); }, { passive: false });
  svg.addEventListener('pointerdown', event => { if (event.button !== 0) return; graphWasDragged = false; drag = { start: point(event), x, y }; });
  svg.addEventListener('pointermove', event => { if (!drag) return; const p = point(event); if (Math.hypot(p.x - drag.start.x, p.y - drag.start.y) > 4) { graphWasDragged = true; svg.setPointerCapture(event.pointerId); } if (graphWasDragged) { x = drag.x + p.x - drag.start.x; y = drag.y + p.y - drag.start.y; transform(); } });
  const end = () => { drag = null; setTimeout(() => graphWasDragged = false, 0); };
  svg.addEventListener('pointerup', end); svg.addEventListener('pointercancel', end);
  svg.addEventListener('pointerleave', event => { if (!svg.hasPointerCapture(event.pointerId)) drag = null; });
}
document.addEventListener('click', async event => {
  const button = event.target.closest('button, [data-node]');
  if (!button || button.disabled) return;
  const data = button.dataset;
  if (data.txOffset !== undefined) { loadTransactions(Number(data.txOffset)); return; }
  if (data.node) { if (!graphWasDragged) openNode(data.node); return; }
  if (data.view) { switchView(data.view); return; }
  if (data.role) { state.role = data.role; state.focus = null; state.page = 1; syncFilters(); render(); return; }
  if (data.cluster) { state.cluster = data.cluster; state.query = ''; state.focus = null; state.view = 'network'; syncFilters(); render(); return; }
  if (data.focus) { state.focus = data.focus; state.query = data.focus; state.role = state.cluster = ''; state.view = 'network'; $('#node-dialog').close(); syncFilters(); render(); return; }
  if (data.sort) { state.desc = state.sort === data.sort ? !state.desc : true; state.sort = data.sort; state.page = 1; render(); return; }
  if (data.page) { state.page += Number(data.page); render(); return; }
  if (data.color) { state.color = data.color; render(); return; }
  if (data.zoom) { graphController?.(data.zoom); return; }
  const action = data.action || ({ 'import-btn': 'import', 'reset-btn': 'reset', 'export-btn': 'export' }[button.id]);
  if (action === 'import') $('#graph-file').click();
  else if (action === 'reset') reset();
  else if (action === 'export') downloadList();
  else if (action === 'close') $('#node-dialog').close();
  else if (action === 'retry') loadDefault();
  else if (action === 'all') { state.all = !state.all; render(); }
  else if (action === 'boundary') { state.query = ''; state.role = ''; state.cluster = ''; state.view = 'priority'; state.query = 'depth=4'; syncFilters(); render(); }
  else if (action === 'copy') { try { await navigator.clipboard.writeText(state.selected); toast('GID скопирован'); } catch { toast('Выделите GID в карточке и скопируйте его вручную.'); } }
});
$('#node-dialog').addEventListener('click', event => { if (event.target === $('#node-dialog')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) event.target.close(); } });
$('#node-dialog').addEventListener('close', cancelTransactions);
document.addEventListener('keydown', event => {
  if (event.key === '/' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName) && !$('#node-dialog').open) { event.preventDefault(); $('#search').focus(); }
  if ((event.key === 'Enter' || event.key === ' ') && event.target.classList.contains('graph-node')) { event.preventDefault(); openNode(event.target.dataset.node); }
});
let searchTimer;
$('#search').addEventListener('input', event => {
  state.query = event.target.value; state.focus = null; state.page = 1;
  clearTimeout(searchTimer); searchTimer = setTimeout(render, 140);
});
$('#role-filter').innerHTML += Object.entries(M.roles).map(([role, info]) => '<option value="' + role + '">' + info.label + '</option>').join('');
$('#role-filter').addEventListener('change', event => { state.role = event.target.value; state.page = 1; state.focus = null; render(); });
$('#cluster-filter').addEventListener('change', event => { state.cluster = event.target.value; state.page = 1; state.focus = null; render(); });
$('#graph-file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 30 * 1024 * 1024) throw new Error('Выберите файл размером до 30 МБ.');
    const graph = M.normalize(M.parse(await file.text()));
    ++loadTicket; loadController?.abort(); installGraph(graph, file.name); toast('Данные загружены: ' + fmt(graph.nodes.length) + ' клиентов');
  } catch (error) { toast('Не удалось открыть файл. ' + error.message); }
  event.target.value = '';
});
loadDefault();
