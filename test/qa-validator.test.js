import test from 'node:test';
import assert from 'node:assert/strict';
import { validateInput, validateResponse, responseSchema } from '../src/validator.js';

test('validateInput принимает и обрезает текст', () => {
  assert.equal(validateInput('  помоги  '), 'помоги');
  assert.equal(validateInput('a'.repeat(10000)).length, 10000);
});

test('validateInput отбивает пустое и нестроковое', () => {
  for (const bad of ['', '   ', '\n\t', null, undefined, 42, {}, ['x'], Object('str')]) {
    assert.throws(() => validateInput(bad), /Введите непустой текст/, `принял ${JSON.stringify(bad)}`);
  }
});

test('validateInput отбивает слишком длинное', () => {
  assert.throws(() => validateInput('a'.repeat(10001)), /Максимум 10000/);
});

test('validateResponse принимает объект и JSON-строку одинаково', () => {
  const expected = { intent: 'help', reply: 'ок' };
  assert.deepEqual(validateResponse(expected), expected);
  assert.deepEqual(validateResponse(JSON.stringify(expected)), expected);
});

test('validateResponse отбивает всё, что не соответствует схеме', () => {
  const bad = [
    { intent: 'help' },                                   // нет reply
    { reply: 'x' },                                       // нет intent
    { intent: 'ГРУБОСТЬ', reply: 'x' },                   // intent вне enum
    { intent: 'help', reply: '' },                        // пустой reply
    { intent: 'help', reply: '   ' },                     // reply из пробелов
    { intent: 'help', reply: 'a'.repeat(4001) },          // слишком длинный reply
    { intent: 'help', reply: 'x', extra: 1 },             // лишний ключ
    { intent: 'help', reply: 123 },                       // reply не строка
    null, undefined, 'null', '[]', '"строка"', '5',
  ];
  for (const value of bad) {
    assert.throws(() => validateResponse(value), `принял ${JSON.stringify(value)}`);
  }
});

test('validateResponse не даёт загрязнить прототип', () => {
  assert.throws(() => validateResponse('{"intent":"help","reply":"x","__proto__":{"pwned":1}}'));
  assert.equal({}.pwned, undefined);
});

test('validateResponse обрезает reply, не ломая содержимое', () => {
  assert.deepEqual(validateResponse({ intent: 'unknown', reply: '  ответ  ' }), { intent: 'unknown', reply: 'ответ' });
});

test('responseSchema пригодна для strict Structured Outputs', () => {
  assert.equal(responseSchema.additionalProperties, false);
  assert.deepEqual([...responseSchema.required].sort(), Object.keys(responseSchema.properties).sort(),
    'в strict-режиме OpenAI все свойства обязаны быть в required');
});

test('ДЕФЕКТ-14: responseSchema экспортируется изменяемым объектом', () => {
  const before = responseSchema.properties.intent.enum.slice();
  responseSchema.properties.intent.enum.push('injected');
  const leaked = responseSchema.properties.intent.enum.includes('injected');
  responseSchema.properties.intent.enum.length = 0;
  responseSchema.properties.intent.enum.push(...before);
  assert.equal(leaked, false,
    'схема общая и мутабельная: любой код может расширить enum и снять ограничение с валидации ответов LLM');
});
