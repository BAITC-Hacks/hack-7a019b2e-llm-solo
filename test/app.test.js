import test from 'node:test';
import assert from 'node:assert/strict';
import { run } from '../src/app.js';
import { applyRules } from '../src/rules.js';

for (const input of ['', ' \n\t ', null, undefined, 42, 'a'.repeat(10001)]) {
  test(`rejects invalid input ${String(input).slice(0, 20)} before side effects`, async () => {
    const unexpected = () => assert.fail('Unexpected side effect');
    await assert.rejects(run(input, { client: { generate: unexpected }, memory: { append: unexpected } }));
  });
}

for (const output of ['garbage', '```json\n{}\n```', '{}', 'null', '[]',
  { intent: 'wrong', reply: 'ok' }, { intent: 'help', reply: '  ' },
  { intent: 'help', reply: 42 }, { intent: 'help', reply: 'ok', extra: true }]) {
  test(`fallback on garbage output ${JSON.stringify(output)}`, async () => {
    const result = await run('Привет!', { client: { generate: async () => output } });
    assert.equal(result.source, 'rules');
    assert.equal(result.answer.intent, 'greeting');
    assert.ok(result.fallbackReason);
  });
}

test('valid LLM output is persisted with normalized input', async () => {
  const answer = { intent: 'help', reply: 'Ответ' };
  let saved;
  const result = await run(' помощь ', {
    client: { generate: async text => { assert.equal(text, 'помощь'); return answer; } },
    memory: { append: async (...args) => { saved = args; return true; } },
  });
  assert.equal(result.source, 'llm');
  assert.equal(result.fallbackReason, null);
  assert.equal(result.memorySaved, true);
  assert.deepEqual(saved, ['помощь', { answer, source: 'llm', fallbackReason: null }]);
});

test('rules use whole words and prioritize help', () => {
  assert.equal(applyRules('something').intent, 'unknown');
  assert.equal(applyRules('Привет! Помоги').intent, 'help');
});
