'use strict';
// Controller tests with a minimal DOM double, not a replacement for visual browser QA.
const test = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { runInNewContext } = require('node:vm');
const { webcrypto } = require('node:crypto');
const A = require('./agent.js');
const M = require('./data.js');
const source = readFileSync(join(__dirname, 'agent-ui.js'), 'utf8');
const html = readFileSync(join(__dirname, 'index.html'), 'utf8');
const gid = '100000000000000001', other = '100000000000000002', digest = 'a'.repeat(64);
class Element {
  constructor(id) { this.id = id; this.value = ''; this.dataset = {}; this.attributes = {}; this.listeners = {}; this.hidden = false; this.disabled = false; this.open = false; this.innerHTML = ''; this.textContent = ''; this.focusCount = 0; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  emit(type, extra = {}) { for (const handler of this.listeners[type] || []) handler({ target: this, preventDefault() {}, ...extra }); }
  setAttribute(key, value) { this.attributes[key] = value; }
  focus() { this.focusCount++; }
  contains() { return false; }
  scrollIntoView() {}
  closest() { return this; }
  show() { this.open = true; }
  close() { this.open = false; this.emit('close'); }
}
function harness() {
  const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map(match => [match[1], new Element(match[1])]));
  const el = id => { assert.ok(elements.has(id), 'Missing real HTML element: ' + id); return elements.get(id); };
  const dialog = el('agent-dialog');
  dialog.querySelector = selector => el(selector.slice(1));
  dialog.querySelectorAll = () => [];
  const calls = [], opened = [], focused = [];
  const window = { MoneyGraph: M, TraceAgent: { ...A,
    makeRequest: options => A.makeRequest(options, webcrypto),
    createTransport: () => (request, knownIds, signal) => new Promise((resolve, reject) => calls.push({ request, knownIds, signal, resolve, reject })) } };
  const document = new Element('document');
  document.querySelector = selector => el(selector.slice(1));
  runInNewContext(source, { window, document, AbortController });
  const assistant = window.TraceAssistant.create({ onOpenNode: id => opened.push(id), onShowGraph: id => focused.push(id) });
  const graph = { byId: new Map([gid, other].map(id => [id, { gid: id, role: 'transit', cluster_id: '1', depth: 2 }])) };
  assistant.reset(graph, digest);
  const click = (id, dataset) => {
    const button = dataset ? new Element('citation') : el(id);
    if (dataset) button.dataset = dataset;
    dialog.emit('click', { target: button });
  };
  const ask = question => { el('agent-question').value = question; el('agent-question').emit('input'); el('agent-form').emit('submit'); };
  const succeed = async (call, text = 'Тестовая записка') => {
    call.resolve({ conversation_id: 'trace_ui_test_session', answer: { summary: text, facts: [{ text: 'Факт', node_ids: [gid] }], hypotheses: [], limitations: [], next_steps: [] } });
    await new Promise(resolve => setImmediate(resolve));
  };
  return { el, assistant, graph, calls, click, ask, succeed, opened, focused, document };
}
test('opening a notebook is free; follow-up carries the same conversation and node', async () => {
  const h = harness(); h.assistant.open(gid);
  assert.equal(h.calls.length, 0);
  h.ask('Первый вопрос');
  assert.equal(h.calls.length, 1);
  assert.equal(h.el('agent-question').disabled, true);
  h.ask('Двойное нажатие');
  assert.equal(h.calls.length, 1);
  await h.succeed(h.calls[0]);
  assert.match(h.el('agent-history').innerHTML, /Тестовая записка/);
  assert.equal(h.el('agent-question').value, '');
  h.ask('Уточнение');
  assert.equal(h.calls[1].request.conversation_id, 'trace_ui_test_session');
  assert.equal(h.calls[1].request.node_gid, gid);
  assert.equal(h.calls[1].request.dataset_sha256, digest);
  await h.succeed(h.calls[1]);
  h.click(null, { aiNode: other });
  assert.equal(h.opened[0], other);
  assert.equal(h.el('agent-dialog').open, false);
});
test('explicit stop cancels, late response is ignored; retry reuses the request ID', async () => {
  const h = harness(); h.assistant.open(gid); h.ask('Вопрос');
  h.click('agent-cancel');
  assert.equal(h.calls[0].signal.aborted, true);
  h.assistant.open(gid); h.click('agent-cancel-retry');
  assert.equal(h.calls[1].request.request_id, h.calls[0].request.request_id);
  await h.succeed(h.calls[0], 'ЗАПОЗДАЛЫЙ ОТВЕТ');
  assert.ok(!h.el('agent-history').innerHTML.includes('ЗАПОЗДАЛЫЙ'));
  await h.succeed(h.calls[1]);
  assert.equal((h.el('agent-history').innerHTML.match(/class="agent-note"/g) || []).length, 1);
});

test('lion opens an empty non-modal chat without sending a request', () => {
  const h = harness();
  h.el('agent-pet-button').emit('click');
  assert.equal(h.el('agent-dialog').open, true);
  assert.equal(h.el('agent-empty').hidden, false);
  assert.equal(h.el('agent-question').disabled, true);
  assert.equal(h.el('agent-send').disabled, true);
  assert.equal(h.calls.length, 0);
  assert.equal(h.el('agent-pet-button').attributes['aria-expanded'], 'true');
  h.click('agent-close');
  assert.equal(h.el('agent-dialog').open, false);
  assert.equal(h.el('agent-pet-button').attributes['aria-expanded'], 'false');
  h.assistant.reset(null, null); h.assistant.open();
  assert.match(h.el('agent-empty-text').textContent, /graph.json/);
});

test('minimizing preserves pending work; a ready answer updates the lion without stealing focus', async () => {
  const h = harness(); h.assistant.open(gid); h.ask('Вопрос');
  assert.equal(h.el('agent-companion').dataset.state, 'thinking');
  h.click('agent-close');
  assert.equal(h.calls[0].signal.aborted, false);
  const focusCount = h.el('agent-question').focusCount;
  await h.succeed(h.calls[0]);
  assert.equal(h.el('agent-dialog').open, false);
  assert.equal(h.el('agent-question').focusCount, focusCount);
  assert.equal(h.el('agent-companion').dataset.state, 'ready');
  assert.match(h.el('agent-pet-status').textContent, /Ответ готов/);
  h.el('agent-pet-button').emit('click');
  assert.equal(h.el('agent-companion').dataset.state, 'idle');
  assert.match(h.el('agent-history').innerHTML, /Тестовая записка/);
  assert.equal(h.calls.length, 1);
});

test('selection synchronizes the pinned context without opening chat or sending; Escape respects node dialog', () => {
  const h = harness(); h.assistant.select(gid);
  assert.equal(h.el('agent-dialog').open, false);
  assert.equal(h.el('agent-gid').textContent, gid);
  assert.equal(h.calls.length, 0);
  h.assistant.open(); h.ask('Вопрос');
  h.assistant.select(other);
  assert.equal(h.calls[0].signal.aborted, true);
  assert.equal(h.el('agent-gid').textContent, other);
  h.el('node-dialog').open = true;
  h.document.emit('keydown', { key: 'Escape' });
  assert.equal(h.el('agent-dialog').open, true);
  h.el('node-dialog').open = false;
  h.document.emit('keydown', { key: 'Escape' });
  assert.equal(h.el('agent-dialog').open, false);
  h.assistant.reset(h.graph, digest);
  assert.equal(h.el('agent-empty').hidden, false);
  assert.equal(h.el('agent-question').value, '');
});
test('drafts and answers are isolated by node; dataset replacement aborts and clears all', async () => {
  const h = harness(); h.assistant.open(gid); h.ask('Для первого узла');
  await h.succeed(h.calls[0]);
  h.el('agent-question').value = 'Черновик'; h.el('agent-question').emit('input');
  h.assistant.open(other);
  assert.equal(h.el('agent-question').value, '');
  assert.equal(h.el('agent-history').innerHTML, '');
  h.ask('Для другого узла');
  assert.equal(h.calls[1].request.conversation_id, null);
  assert.equal(h.calls[1].request.node_gid, other);
  h.assistant.open(gid);
  assert.equal(h.calls[1].signal.aborted, true);
  assert.equal(h.el('agent-question').value, 'Черновик');
  assert.match(h.el('agent-history').innerHTML, /Тестовая записка/);
  await h.succeed(h.calls[1], 'ЧУЖОЙ ОТВЕТ');
  assert.ok(!h.el('agent-history').innerHTML.includes('ЧУЖОЙ'));
  h.ask('Перед новым файлом');
  h.assistant.reset(h.graph, 'b'.repeat(64));
  assert.equal(h.calls[2].signal.aborted, true);
  h.assistant.open(gid);
  assert.equal(h.el('agent-history').innerHTML, '');
  await h.succeed(h.calls[2], 'СТАРАЯ ВЫГРУЗКА');
  assert.equal(h.el('agent-history').innerHTML, '');
});
test('errors never become notes; twelve-turn limit and fresh-note reset work', async () => {
  const h = harness(); h.assistant.open(gid); h.ask('Вопрос');
  h.calls[0].reject(new A.AgentError('rate_limited'));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.el('agent-error').hidden, false);
  assert.equal(h.el('agent-history').innerHTML, '');
  h.click('agent-retry'); await h.succeed(h.calls[1]);
  for (let i = 1; i < 12; i++) { h.ask('Уточнение ' + i); await h.succeed(h.calls.at(-1)); }
  const count = h.calls.length;
  h.ask('За пределами лимита');
  assert.equal(h.calls.length, count);
  assert.equal(h.el('agent-question').disabled, true);
  h.click('agent-new'); h.ask('Новая записка');
  assert.equal(h.calls.at(-1).request.conversation_id, null);
  assert.equal(h.el('agent-history').innerHTML, '');
  await h.succeed(h.calls.at(-1));
  h.click('agent-graph'); assert.equal(h.focused[0], gid);
  assert.equal(h.el('agent-dialog').open, true);
});

test('unavailable AI has an explicit error and pet state, never a fabricated answer', async () => {
  const h = harness(); h.assistant.open(gid); h.ask('Объясни роль');
  h.calls[0].reject(new A.AgentError('unavailable'));
  await new Promise(resolve => setImmediate(resolve));
  assert.match(h.el('agent-error-text').textContent, /ИИ пока не подключён/);
  assert.equal(h.el('agent-history').innerHTML, '');
  assert.equal(h.el('agent-companion').dataset.state, 'error');
  h.assistant.reset(h.graph, null); h.assistant.open(gid);
  assert.equal(h.el('agent-question').disabled, true);
  assert.equal(h.el('agent-send').disabled, true);
  h.ask('Без подтверждённого графа');
  assert.equal(h.calls.length, 1);
});

test('markup has unique IDs and keeps the approved miniature CSS lion', () => {
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(ids.length, new Set(ids).size);
  const css = readFileSync(join(__dirname, 'agent.css'), 'utf8');
  assert.match(css, /\.agent-pet \{[^}]*width: 72px; height: 76px;/);
  assert.match(css, /\.agent-pet-button \{[^}]*width: 88px; height: 106px;/);
  assert.ok(!css.includes('animation: agent-look 1.1s ease-in-out infinite'));
  assert.ok(css.includes('prefers-reduced-motion: reduce'));
});
