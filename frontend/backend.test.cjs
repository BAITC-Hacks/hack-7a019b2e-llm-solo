const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const M = require('./data.js');

const gid = '100000000000000001';
const other = '100000000000000002';
function fixture(extra = {}) {
  return { snapshot_id: 'snapshot-one', nodes: [
    { gid, role: 'transit', cluster_id: 0, role_score: .7, priority_score: .8, in_kzt: 100.01, out_kzt: 99.95, ...extra },
    { gid: other, role: 'peripheral', cluster_id: 0, role_score: .2, priority_score: .1 }
  ], edges: [{ src: gid, dst: other, sum_kzt: 99.95, n_tx: 1 }] };
}
function page(id = 1, src = gid, dst = other) {
  return { items: [{ row_id: id, src, dst, date: '2026-07-13', sum_kzt: 99.95, cents: 9995 }], total: 1 };
}
function response(body, snapshot = 'snapshot-one') {
  return { ok: true, status: 200, headers: new Headers({ 'X-Snapshot-ID': snapshot }), text: async () => JSON.stringify(body) };
}
function harness(fetch) {
  const elements = new Map();
  const document = {
    querySelector(selector) {
      if (selector === '#graph-svg' || selector === '#graph-viewport') return null;
      if (!elements.has(selector)) elements.set(selector, { innerHTML: '', textContent: '', value: '', open: false,
        classList: { add() {}, toggle() {} }, addEventListener() {},
        showModal() { this.open = true; }, close() { this.open = false; } });
      return elements.get(selector);
    },
    querySelectorAll: () => [], addEventListener() {}
  };
  const sandbox = { window: { MoneyGraph: M, scrollTo() {} }, document, fetch, Intl, AbortController,
    setTimeout, clearTimeout, console, URL, Blob, navigator: {} };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(require.resolve('./app.js'), 'utf8') +
    '\nthis.testing = {state, installGraph, openNode, loadTransactions, cancelTransactions, loadDefault, timingDetail, cash};', sandbox);
  return { ...sandbox.testing, elements };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('analysis metrics preserve observed zero, absent values and all role matches', () => {
  const graph = M.normalize(fixture({ temporal_in_fraction: 0, temporal_out_fraction: 0,
    temporal_strict_in_fraction: 0, temporal_strict_out_fraction: 0, temporal_matched_kzt: 0,
    temporal_window_days: 2, peripheral_reason_text: 'Нет совместимого объёма', matched_roles: ['transit', 'distributor'],
    priority_parts: { flow: .2, bridge: 0 } }));
  assert.equal(graph.nodes[0].temporal_in_fraction, 0);
  assert.equal(graph.nodes[0].priority_parts.bridge, 0);
  assert.deepEqual(graph.nodes[0].matched_roles, ['transit', 'distributor']);
  assert.equal(graph.nodes[1].temporal_in_fraction, undefined);
  assert.equal(graph.snapshot_id, 'snapshot-one');
  for (const extra of [{ temporal_in_fraction: null }, { temporal_out_fraction: 1.1 },
    { temporal_matched_kzt: -1 }, { matched_roles: ['guilty'] }, { priority_parts: { flow: '0.2' } }]) {
    assert.throws(() => M.normalize(fixture(extra)));
  }
});

test('transactions retain exact IDs, real dates, cents and duplicate operations with distinct row IDs', () => {
  const graph = M.normalize(fixture());
  const raw = page(); raw.items.push({ ...raw.items[0], row_id: 2 }); raw.total = 2;
  const normalized = M.transactions(raw, gid, graph);
  assert.equal(normalized.items.length, 2);
  assert.equal(normalized.items[0].src, gid);
  assert.equal(normalized.items[0].date, '2026-07-13');
  assert.equal(normalized.items[0].cents, 9995);
  for (const extra of [{ date: '2026-02-30' }, { date: '2026-07-13T00:00:00Z' }, { cents: 9994 },
    { src: '3', dst: '4' }, { row_id: 1.5 }]) {
    assert.throws(() => M.transactions({ items: [{ ...page().items[0], ...extra }], total: 1 }, gid, graph));
  }
});

test('failed API graph load gives an error and never falls back to a stale file', async () => {
  const requested = [];
  const app = harness(async url => { requested.push(url); return { ok: false, status: 503 }; });
  await settle();
  assert.deepEqual(requested, ['/api/graph']);
  assert.equal(app.state.graph, null);
  assert.match(app.elements.get('#app').innerHTML, /HTTP 503/);
});

test('local imports never request unrelated transactions and card keeps cents and zero fractions', async () => {
  let calls = 0;
  const app = harness(async () => { calls++; return { ok: false, status: 503 }; });
  await settle();
  app.installGraph(M.normalize(fixture({ temporal_in_fraction: 0, temporal_out_fraction: 0,
    temporal_strict_in_fraction: 0, temporal_strict_out_fraction: 0, temporal_window_days: 2 })), 'local.json');
  app.openNode(gid);
  await settle();
  assert.equal(calls, 1);
  assert.match(app.elements.get('#node-detail').innerHTML, /100,01/);
  assert.match(app.elements.get('#node-detail').innerHTML, /Доля входа <strong>0%/);
  assert.match(app.elements.get('#node-detail').innerHTML, /Полные операции не загружены/);
});

test('slow response from previous card cannot overwrite currently selected node', async () => {
  const pending = [];
  const app = harness(url => url === '/api/graph' ? Promise.resolve(response(fixture())) :
    new Promise(resolve => pending.push({ url, resolve })));
  await settle();
  app.openNode(gid);
  app.openNode(other);
  pending[1].resolve(response(page(2)));
  await settle();
  assert.equal(app.state.transactions.items[0].row_id, 2);
  pending[0].resolve(response(page(1)));
  await settle();
  assert.equal(app.state.selected, other);
  assert.equal(app.state.transactions.items[0].row_id, 2);
  assert.match(pending[1].url, /offset=0&limit=100$/);
  app.cancelTransactions();
});

test('server snapshot changes are visible and transaction page can be retried', async () => {
  const app = harness(url => Promise.resolve(url === '/api/graph' ? response(fixture()) : response(page(), 'new-snapshot')));
  await settle();
  app.openNode(gid);
  await settle();
  assert.match(app.state.transactions.error, /Набор на сервере изменился/);
  assert.match(app.elements.get('#node-detail').innerHTML, /Повторить загрузку переводов/);
  app.cancelTransactions();
});

test('transaction pagination requests the requested offset and preserves total', async () => {
  const urls = [];
  const app = harness(url => {
    urls.push(url);
    if (url === '/api/graph') return Promise.resolve(response(fixture()));
    const offset = Number(new URL(url, 'http://127.0.0.1').searchParams.get('offset'));
    const items = Array.from({ length: offset === 0 ? 100 : 1 }, (_, index) => ({ ...page().items[0], row_id: offset + index + 1 }));
    return Promise.resolve(response({ items, total: 101 }));
  });
  await settle();
  app.openNode(gid);
  await settle();
  assert.equal(app.state.transactions.items.length, 100);
  await app.loadTransactions(100);
  assert.equal(app.state.transactions.total, 101);
  assert.equal(app.state.transactions.items[0].row_id, 101);
  assert.match(urls.at(-1), /offset=100&limit=100$/);
  assert.match(app.elements.get('#node-detail').innerHTML, /101–101 из 101/);
  app.cancelTransactions();
});
