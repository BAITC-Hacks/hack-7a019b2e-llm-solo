'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { webcrypto, createHash } = require('node:crypto');
const A = require('./agent.js');
const schema = require('./agent-answer.schema.json');
const gid = '100000000000000001';
const neighbor = '100000000000000002';
const ids = new Set([gid, neighbor]);
const digest = 'a'.repeat(64);
const request = () => A.makeRequest({ datasetHash: digest, nodeGid: gid, question: '  Объясни роль  ' }, webcrypto);
const answer = () => ({ summary: 'Нужна проверка.', facts: [{ text: 'Синтетический факт для теста.', node_ids: [gid, neighbor] }],
  hypotheses: [{ text: 'Рабочая гипотеза.', node_ids: [gid] }], limitations: [{ text: 'Нет дат.', node_ids: [] }], next_steps: [] });
const result = req => ({ schema_version: A.VERSION, request_id: req.request_id, dataset_sha256: req.dataset_sha256,
  node_gid: req.node_gid, conversation_id: 'trace_test_session_001', answer: answer() });
const response = (body, status = 200, type = 'application/json') => ({ status, ok: status >= 200 && status < 300,
  headers: new Headers({ 'content-type': type }), text: async () => typeof body === 'string' ? body : JSON.stringify(body) });
const isError = code => error => error instanceof A.AgentError && error.code === code;

test('request preserves int64 GIDs as strings, trims question, uses no key or graph', () => {
  const req = request();
  assert.equal(req.node_gid, gid);
  assert.equal(typeof req.node_gid, 'string');
  assert.equal(req.question, 'Объясни роль');
  assert.equal(req.conversation_id, null);
  assert.match(req.request_id, /^[a-f0-9-]{36}$/);
  assert.deepEqual(Object.keys(req).sort(), ['schema_version', 'request_id', 'dataset_sha256', 'node_gid', 'conversation_id', 'question'].sort());
});
test('empty, huge and invalid-context requests are rejected', () => {
  for (const question of ['', '   ', 'a'.repeat(2001)]) {
    assert.throws(() => A.makeRequest({ datasetHash: digest, nodeGid: gid, question }, webcrypto), isError('invalid_question'));
  }
  assert.throws(() => A.makeRequest({ datasetHash: null, nodeGid: gid, question: 'x' }, webcrypto), isError('insecure_context'));
  assert.throws(() => A.makeRequest({ datasetHash: digest, nodeGid: Number(gid), question: 'x' }, webcrypto), isError('invalid_response'));
});
test('fingerprint hashes exact file bytes, not parsed or reserialized JSON', async () => {
  const bytes = new TextEncoder().encode('\uFEFF{ "gid": "' + gid + '" }\r\n');
  assert.equal(await A.fingerprint(bytes, webcrypto), createHash('sha256').update(bytes).digest('hex'));
  assert.notEqual(await A.fingerprint(bytes, webcrypto), await A.fingerprint(new TextEncoder().encode('{}'), webcrypto));
  await assert.rejects(A.fingerprint(bytes, {}), isError('insecure_context'));
});
test('valid structured response and exported JSON schema agree', () => {
  const req = request(), data = result(req);
  assert.equal(A.validateResponse(data, req, ids), data);
  assert.deepEqual(Object.keys(data.answer).sort(), schema.required.slice().sort());
  assert.equal(schema.additionalProperties, false);
  assert.equal(schema.$defs.fact.properties.node_ids.minItems, 1);
});
test('wrong request, dataset, node, version or conversation cannot enter current dossier', () => {
  const req = request();
  for (const [key, value] of [['request_id', 'other'], ['dataset_sha256', 'b'.repeat(64)], ['node_gid', neighbor], ['schema_version', 'v2'], ['conversation_id', 123]]) {
    assert.throws(() => A.validateResponse({ ...result(req), [key]: value }, req, ids), isError('invalid_response'));
  }
  req.conversation_id = 'trace_previous_session';
  assert.throws(() => A.validateResponse(result(req), req, ids), isError('invalid_response'));
});
test('invalid shapes, oversized output, unknown and numeric citations are rejected', () => {
  const req = request();
  const changes = [
    data => { data.answer.facts[0].node_ids = ['999']; },
    data => { data.answer.facts[0].node_ids = [Number(gid)]; },
    data => { data.answer.facts[0].node_ids = [gid, gid]; },
    data => { data.answer.facts[0].node_ids = []; },
    data => { data.answer.facts[0].text = ' '; },
    data => { data.answer.summary = 'a'.repeat(1501); },
    data => { data.answer.hypotheses = Array(9).fill({ text: 'x', node_ids: [] }); },
    data => { delete data.answer.limitations; },
    data => { data.answer.facts[0].url = 'https://example.com'; },
    data => { data.api_key = 'must-not-be-part-of-response'; },
    data => { data.answer = null; }
  ];
  for (const change of changes) {
    const data = result(req); change(data);
    assert.throws(() => A.validateResponse(data, req, ids), isError('invalid_response'));
  }
});
test('model text is escaped; GID buttons are local actions, never model HTML', () => {
  const data = answer();
  data.summary = '<script>alert("x")</script>';
  data.facts[0].text = '<img src=x onerror="alert(1)"> & test';
  const markup = A.answerMarkup(data);
  assert.ok(!markup.includes('<script>') && !markup.includes('<img'));
  assert.ok(markup.includes('&lt;script&gt;') && markup.includes('&amp; test'));
  assert.ok(markup.includes('data-ai-node="' + gid + '"'));
  assert.ok(!markup.includes('href='));
  assert.ok(markup.includes('Не указано в ответе.'));
});
test('transport only posts same-origin, respects repeated request ID, validates output', async () => {
  const req = request(), calls = [];
  const send = A.createTransport(async (url, options) => {
    calls.push({ url, options }); return response(result(JSON.parse(options.body)));
  });
  assert.equal((await send(req, ids)).answer.summary, 'Нужна проверка.');
  await send(req, ids);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].url, '/api/ai/analyze');
  assert.equal(calls[0].options.method, 'POST');
  assert.equal(calls[0].options.credentials, 'same-origin');
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal(calls[0].options.body, calls[1].options.body);
  assert.deepEqual(Object.keys(calls[0].options.headers).sort(), ['Accept', 'Content-Type']);
});
test('HTTP errors are mapped to safe UI text, server details never leaked', async () => {
  for (const [status, code] of [[404, 'unavailable'], [405, 'unavailable'], [501, 'unavailable'], [503, 'unavailable'],
    [401, 'unauthorized'], [403, 'unauthorized'], [409, 'dataset_mismatch'], [410, 'conversation_expired'],
    [422, 'refused'], [429, 'rate_limited'], [504, 'timeout'], [500, 'server'], [502, 'server']]) {
    const send = A.createTransport(async () => response('server-private-debug', status));
    await assert.rejects(send(request(), ids), error => isError(code)(error) && !error.message.includes('server-private-debug'));
  }
});
test('HTML fallback, malformed JSON, huge body and network failure are explicit errors', async () => {
  for (const bad of [response('<html>SPA</html>', 200, 'text/html'), response('{bad'), response(' '.repeat(100001)), response({})]) {
    await assert.rejects(A.createTransport(async () => bad)(request(), ids), isError('invalid_response'));
  }
  await assert.rejects(A.createTransport(async () => { throw new Error('private network detail'); })(request(), ids), isError('network'));
});
test('abort cancels pending fetch and does not convert cancellation to network failure', async () => {
  const controller = new AbortController();
  let started;
  const ready = new Promise(resolve => { started = resolve; });
  const send = A.createTransport(async (url, { signal }) => {
    started();
    return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true }));
  });
  const resultPromise = send(request(), ids, controller.signal);
  const check = assert.rejects(resultPromise, { name: 'AbortError' });
  await ready; controller.abort(); await check;
});
test('late response is discarded even when an injected transport ignores cancellation', async () => {
  const req = request(), controller = new AbortController();
  let finish;
  const send = A.createTransport(async () => new Promise(resolve => { finish = resolve; }));
  const resultPromise = send(req, ids, controller.signal);
  const check = assert.rejects(resultPromise, { name: 'AbortError' });
  controller.abort(); finish(response(result(req))); await check;
});
test('timeout is bounded and distinguishable from cancellation', async () => {
  const send = A.createTransport(async (url, { signal }) => new Promise((resolve, reject) =>
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })), 10);
  await assert.rejects(send(request(), ids), isError('timeout'));
});
