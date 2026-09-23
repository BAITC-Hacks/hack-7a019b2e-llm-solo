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
function transaction(row_id = 0, extra = {}) {
  return { row_id, src: gid, dst: other, date: '2026-07-13', sum_kzt: 99.95, cents: 9995, ...extra };
}
function response(body) {
  return { ok: true, status: 200, text: async () => JSON.stringify(body) };
}
function localFile(body, name = 'graph.json') {
  return { name, size: 100, text: async () => JSON.stringify(body) };
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
    setTimeout: (...args) => setTimeout(...args).unref(), clearTimeout, console, URL, Blob, navigator: {} };
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(require.resolve('./app.js'), 'utf8') +
    '\nthis.testing = {state, installGraph, openNode, showTransactions, loadDefault, loadLocalFile, timingDetail, cash};', sandbox);
  return { ...sandbox.testing, elements };
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('analysis metrics preserve observed zero, absent values and all role matches', () => {
  const graph = M.normalize(fixture({ temporal_in_fraction: 0, temporal_out_fraction: 0,
    temporal_strict_in_fraction: 0, temporal_strict_out_fraction: 0, temporal_matched_kzt: 0,
    temporal_window_days: 2, peripheral_reason_text: 'Нет совместимого объёма', matched_roles: ['transit', 'distributor'],
    priority_parts: { flow: .2, bridge: 0 }, external_in_kzt: 100.01, external_out_kzt: 99.95 }));
  assert.equal(graph.nodes[0].temporal_in_fraction, 0);
  assert.equal(graph.nodes[0].priority_parts.bridge, 0);
  assert.equal(graph.nodes[0].external_in_kzt, 100.01);
  assert.deepEqual(graph.nodes[0].matched_roles, ['transit', 'distributor']);
  assert.equal(graph.nodes[1].temporal_in_fraction, undefined);
  assert.equal(graph.snapshot_id, 'snapshot-one');
  for (const extra of [{ temporal_in_fraction: null }, { temporal_out_fraction: 1.1 },
    { temporal_matched_kzt: -1 }, { matched_roles: ['guilty'] }, { priority_parts: { flow: '0.2' } }]) {
    assert.throws(() => M.normalize(fixture(extra)));
  }
});

test('transactions retain exact IDs, real dates, cents and duplicates with distinct row IDs', () => {
  const graph = M.normalize({ ...fixture(), transactions: [transaction(0), transaction(1)] });
  assert.equal(graph.transactions.length, 2);
  assert.equal(graph.transactions[0].src, gid);
  assert.equal(graph.transactions[0].date, '2026-07-13');
  assert.equal(graph.transactions[0].cents, 9995);
  assert.equal(graph.transactionsById.get(gid).length, 2);
  assert.equal(graph.transactionsById.get(other).length, 2);
  for (const extra of [{ date: '2026-02-30' }, { date: '2026-07-13T00:00:00Z' }, { cents: 9994 },
    { src: '3', dst: '4' }, { row_id: 1.5 }]) {
    assert.throws(() => M.normalize({ ...fixture(), transactions: [transaction(0, extra)] }));
  }
  assert.throws(() => M.normalize({ ...fixture(), transactions: [transaction(0), transaction(0)] }), /row_id/);
  assert.throws(() => M.normalize({ ...fixture(), transactions: null }), /transactions/);
});

test('failed static JSON load gives an error without silently loading a different file', async () => {
  const requested = [];
  const app = harness(async url => { requested.push(url); return { ok: false, status: 404 }; });
  await settle();
  assert.deepEqual(requested, ['../graph.json']);
  assert.equal(app.state.graph, null);
  assert.match(app.elements.get('#app').innerHTML, /HTTP 404/);
});

test('legacy imports show missing operations explicitly, retain cents and zero fractions', async () => {
  let calls = 0;
  const app = harness(async () => { calls++; return { ok: false, status: 404 }; });
  await settle();
  app.installGraph(M.normalize(fixture({ temporal_in_fraction: 0, temporal_out_fraction: 0,
    temporal_strict_in_fraction: 0, temporal_strict_out_fraction: 0, temporal_window_days: 2 })), 'local.json');
  app.openNode(gid);
  await settle();
  assert.equal(calls, 1);
  assert.match(app.elements.get('#node-detail').innerHTML, /100,01/);
  assert.match(app.elements.get('#node-detail').innerHTML, /Доля входа <strong>0%/);
  assert.match(app.elements.get('#node-detail').innerHTML, /массивом transactions/);
});

test('slow default file response cannot overwrite a more recent local import', async () => {
  let resolveDefault;
  const app = harness(() => new Promise(resolve => { resolveDefault = resolve; }));
  const local = { ...fixture(), snapshot_id: 'local-choice', transactions: [transaction()] };
  await app.loadLocalFile(localFile(local));
  assert.equal(app.state.graph.snapshot_id, 'local-choice');
  resolveDefault(response(fixture()));
  await settle();
  assert.equal(app.state.graph.snapshot_id, 'local-choice');
  assert.equal(app.state.graph.transactions.length, 1);
});

test('a corrupt imported transaction does not replace the working graph', async () => {
  const app = harness(async () => response(fixture()));
  await settle();
  const graph = app.state.graph;
  await app.loadLocalFile(localFile({ ...fixture(), transactions: [transaction(0, { cents: -1 })] }));
  assert.equal(app.state.graph, graph);
  assert.match(app.elements.get('#toast').textContent, /Не удалось открыть файл/);
});

test('transaction pagination uses local indices and makes no additional requests', async () => {
  const raw = { ...fixture(), transactions: Array.from({ length: 101 }, (_, index) => transaction(index)) };
  let calls = 0;
  const app = harness(async () => { calls++; return response(raw); });
  await settle();
  app.openNode(gid);
  assert.match(app.elements.get('#node-detail').innerHTML, /1–100 из 101/);
  app.showTransactions(100);
  assert.match(app.elements.get('#node-detail').innerHTML, /101–101 из 101/);
  assert.equal(M.transactionPage(app.state.graph, gid, 100).items[0].row_id, 100);
  assert.equal(calls, 1);
  app.openNode(other);
  assert.equal(app.state.transactionOffset, 0);
  assert.match(app.elements.get('#node-detail').innerHTML, /1–100 из 101/);
});

test('the latest selected local file wins even if previous file reading finishes later', async () => {
  const app = harness(async () => ({ ok: false, status: 404 }));
  await settle();
  let finishOld;
  const oldRead = app.loadLocalFile({ name: 'old.json', size: 100, text: () => new Promise(resolve => { finishOld = resolve; }) });
  await app.loadLocalFile(localFile({ ...fixture(), snapshot_id: 'latest' }, 'latest.json'));
  finishOld(JSON.stringify(fixture()));
  await oldRead;
  assert.equal(app.state.graph.snapshot_id, 'latest');
});

test('transaction index is ordered by date and row ID; self-transfer is indexed once', () => {
  const graph = M.normalize({ ...fixture(), transactions: [
    transaction(5), transaction(3), transaction(2, { dst: gid, date: '2026-07-12' })
  ] });
  assert.deepEqual(M.transactionPage(graph, gid).items.map(tx => tx.row_id), [2, 3, 5]);
  assert.equal(M.transactionPage(graph, other).total, 2);
  assert.throws(() => M.transactionPage(graph, gid, -1));
  assert.throws(() => M.transactionPage(graph, gid, 0, 0));
  assert.throws(() => M.transactionPage(graph, '999'));
});

test('versioned snapshot reconciles transactions, directed edges and node totals', () => {
  const valid = { ...fixture(), schema_version: 1, transactions: [transaction()] };
  Object.assign(valid.edges[0], { cents: 9995 });
  Object.assign(valid.nodes[0], { in_kzt: 0, out_kzt: 99.95, in_tx: 0, out_tx: 1 });
  Object.assign(valid.nodes[1], { in_kzt: 99.95, out_kzt: 0, in_tx: 1, out_tx: 0 });
  assert.equal(M.normalize(valid).transactions.length, 1);
  for (const mutate of [
    raw => { raw.schema_version = 2; },
    raw => { delete raw.transactions; },
    raw => { raw.transactions = []; },
    raw => { raw.edges[0].cents++; },
    raw => { raw.edges[0].n_tx++; },
    raw => { raw.edges.push({ ...raw.edges[0] }); },
    raw => { raw.nodes[0].out_kzt = 99.94; },
    raw => { raw.nodes[1].in_tx = 2; },
    raw => { raw.nodes[1].in_kzt = 99.951; }
  ]) {
    const raw = structuredClone(valid); mutate(raw);
    assert.throws(() => M.normalize(raw));
  }
});
