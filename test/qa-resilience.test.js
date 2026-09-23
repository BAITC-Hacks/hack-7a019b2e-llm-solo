import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { run } from '../src/app.js';

const answer = { intent: 'help', reply: 'ответ модели' };
const workingClient = { generate: async () => answer };

async function cliSandbox(t, env = {}) {
  const dir = await mkdtemp(join(tmpdir(), 'llm-solo-qa-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  return (...args) => spawnSync(process.execPath, ['src/cli.js', ...args], {
    encoding: 'utf8',
    env: { ...process.env, MEMORY_PATH: join(dir, 'memory.json'), ...env },
  });
}

test('успешный ответ LLM не подменяется правилами', async () => {
  const result = await run('помоги', { client: workingClient });
  assert.equal(result.source, 'llm');
  assert.equal(result.fallbackReason, null);
  assert.deepEqual(result.answer, answer);
});

test('синхронный client.generate тоже поддерживается', async () => {
  const result = await run('помоги', { client: { generate: () => answer } });
  assert.equal(result.source, 'llm');
});

test('без memory сценарий отрабатывает и честно сообщает об этом', async () => {
  assert.equal((await run('помоги', { client: workingClient })).memorySaved, false);
});

test('ДЕФЕКТ-9: ошибка проводки неотличима от штатного fallback', async () => {
  // A real LLM failure and a dependency-injection mistake must not look alike:
  // the second one means the app will answer with dumb rules forever.
  const garbage = await run('привет', { client: { generate: async () => 'не json' } });
  const noClient = await run('привет', {});
  const clientWithoutMethod = await run('привет', { client: {} });
  const brokenClient = await run('привет', { client: { generate: async () => { null.oops; } } });

  assert.equal(garbage.source, 'rules');
  for (const [label, broken] of [['нет client', noClient], ['client без generate', clientWithoutMethod], ['TypeError внутри generate', brokenClient]]) {
    assert.notEqual(broken.fallbackReason, garbage.fallbackReason,
      `${label}: причина совпадает с обычным мусором от LLM (${garbage.fallbackReason}). ` +
      'Приложение с неправильной проводкой выглядит полностью исправным и всегда отвечает правилами');
  }
});

test('ДЕФЕКТ-10: падение памяти уносит уже полученный ответ', async () => {
  const memory = { append: async () => { throw new Error('на диске нет места'); } };
  const result = await run('помоги', { client: workingClient, memory })
    .catch(error => error);
  assert.ok(!(result instanceof Error),
    `run() пробросил «${result.message}» наружу и потерял готовый ответ LLM. ` +
    'Граница защиты живёт внутри JsonMemory, а не в сценарии: любая другая реализация памяти ' +
    'превращает сбой записи в полный отказ (exit 1), хотя поле memorySaved существует именно для этого случая');
});

test('ДЕФЕКТ-11: memorySaved не приводится к boolean', async () => {
  const memory = { append: async () => 'да' };
  const result = await run('помоги', { client: workingClient, memory });
  assert.equal(typeof result.memorySaved, 'boolean',
    `memorySaved вернулся как ${JSON.stringify(result.memorySaved)}: значение реализации памяти ` +
    'попадает в выходной JSON без нормализации и ломает контракт вывода');
});

test('ДЕФЕКТ-12: пустая переменная окружения роняет приложение вместо значения по умолчанию', async t => {
  // `??` only falls back on undefined, so `OPENAI_MODEL=` in .env is fatal
  // even though README documents a default.
  const cli = await cliSandbox(t, { OPENAI_MODEL: '' });
  const result = cli('--offline', 'привет');
  assert.equal(result.status, 0,
    `пустая OPENAI_MODEL даёт выход ${result.status} и ${result.stderr.trim()}. ` +
    'Использован оператор ?? вместо ||, поэтому значение по умолчанию из README не применяется — ' +
    'достаточно оставить строку пустой в .env, и офлайн-режим тоже перестаёт работать');
});

test('ДЕФЕКТ-13: пустой MEMORY_PATH молча отключает память', async t => {
  const cli = await cliSandbox(t, { MEMORY_PATH: '' });
  const result = cli('--offline', 'привет');
  assert.equal(result.status, 0, result.stderr);
  assert.equal(JSON.parse(result.stdout).memorySaved, true,
    'пустой MEMORY_PATH не подменяется значением по умолчанию: память тихо перестаёт писаться, ' +
    'код выхода остаётся 0, и потеря истории заметна только по строчке в stderr');
});

test('CLI пишет данные в stdout, а диагностику в stderr', async t => {
  const cli = await cliSandbox(t);
  const result = cli('--offline', 'привет');
  assert.equal(result.status, 0, result.stderr);
  assert.doesNotThrow(() => JSON.parse(result.stdout), 'stdout обязан оставаться разбираемым JSON');
  assert.equal(JSON.parse(result.stdout).source, 'rules');
});

test('CLI не пишет ключ и текст пользователя в диагностику', async t => {
  const cli = await cliSandbox(t, { OPENAI_API_KEY: 'sk-СЕКРЕТНЫЙ-КЛЮЧ' });
  const result = cli('--offline', 'мой секретный запрос');
  assert.doesNotMatch(result.stderr, /sk-СЕКРЕТНЫЙ-КЛЮЧ/, 'ключ утёк в stderr');
  assert.doesNotMatch(result.stdout, /sk-СЕКРЕТНЫЙ-КЛЮЧ/, 'ключ утёк в stdout');
});
