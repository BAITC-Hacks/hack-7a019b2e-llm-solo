const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parse, normalize, neighborhood, csv } = require('./data.js');

const node = (gid, cluster_id = 0, extra = {}) => ({
  gid, cluster_id, role: 'transit', role_score: 0.5, priority_score: 0.25, ...extra
});
const fixture = () => ({
  nodes: [node('100000000000000001'), node('100000000000000002'), node('3', 1), node('4', 2)],
  edges: [
    { src: '100000000000000001', dst: '100000000000000002', sum_kzt: 100, n_tx: 2 },
    { src: '3', dst: '100000000000000001', sum_kzt: 75, n_tx: 1 }
  ]
});

test('numeric int64 GIDs remain distinct and exact', () => {
  const parsed = parse('{"nodes":[100000000000000001,100000000000000002],"score":0.123456789012345678,"scientific":1.23e-18,"safe":123}');
  assert.deepEqual(parsed.nodes, ['100000000000000001', '100000000000000002']);
  assert.equal(parsed.score, 0.123456789012345678);
  assert.equal(parsed.scientific, 1.23e-18);
  assert.equal(parsed.safe, 123);
});

test('quoted evidence is never rewritten by lossless parsing', () => {
  const evidence = 'gid 100000000000000001; "quoted"; \\escaped; <script> text';
  assert.equal(parse(JSON.stringify({ evidence })).evidence, evidence);
});

test('degrees, direct neighbors and cluster turnover use actual edges', () => {
  const graph = normalize(fixture());
  assert.equal(graph.byId.get('100000000000000001').in_deg, 1);
  assert.equal(graph.byId.get('100000000000000001').out_deg, 1);
  assert.equal(graph.clusters.get('0').internal, 100);
  assert.equal(graph.clusters.get('1').internal, 0);
  const near = neighborhood(graph, '100000000000000001');
  assert.equal(near.nodes.length, 3);
  assert.equal(near.edges.length, 2);
  assert.equal(near.nodes.some(n => n.gid === '4'), false);
});

test('depth four without outgoing edges is a data boundary', () => {
  const raw = fixture();
  raw.nodes[1].depth = 4;
  raw.nodes[0].depth = 4;
  const graph = normalize(raw);
  assert.equal(graph.nodes[1].truncated_by_depth, true);
  assert.equal(graph.nodes[0].truncated_by_depth, false);
});

test('unsafe JS identifiers, duplicates and dangling edges are rejected', () => {
  assert.throws(() => normalize({ nodes: [node(100000000000000001)], edges: [] }), /точность/);
  assert.throws(() => normalize({ nodes: [node('1'), node('1')], edges: [] }), /повторяющиеся/);
  assert.throws(() => normalize({ nodes: [node('1')], edges: [{ src: '1', dst: '2' }] }), /отсутствующий/);
});

test('invalid scores, money and unknown roles do not enter the UI', () => {
  for (const extra of [{ priority_score: 1.01 }, { role_score: -1 }, { role_score: 'NaN' }, { in_kzt: -10 }, { role: 'admin' }]) {
    assert.throws(() => normalize({ nodes: [node('1', 0, extra)], edges: [] }));
  }
  assert.throws(() => normalize({ nodes: [], edges: [] }));
});

test('CSV keeps exact IDs, required columns and quoted multiline evidence', () => {
  const evidence = 'Line 1, "quote"\nLine 2';
  const text = csv([node('100000000000000001', 0, { evidence })]);
  assert.ok(text.startsWith('\uFEFFrank,gid,role,priority_score,why\r\n'));
  assert.ok(text.includes('"100000000000000001"'));
  assert.ok(text.includes('"Line 1, ""quote""\nLine 2"'));
});
