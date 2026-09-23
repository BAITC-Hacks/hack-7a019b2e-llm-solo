import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile, utimes } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { OpenAIClient } from '../src/openai-client.js';
import { JsonMemory } from '../src/memory.js';

test('bounded streaming cancels before consuming an unlimited body', async () => {
  let pulls = 0, cancelled = false;
  const body = new ReadableStream({ pull(controller) { pulls++; controller.enqueue(new Uint8Array(128)); },
    cancel() { cancelled = true; } });
  const client = new OpenAIClient({ apiKey: 'test', maxResponseBytes: 256,
    fetchImpl: async () => new Response(body) });
  await assert.rejects(client.generate('hello'), { code: 'response_too_large' });
  assert.equal(cancelled, true);
  assert.ok(pulls <= 5, `read ${pulls} chunks instead of stopping at byte limit`);
});

test('Content-Length beyond limit cancels without parsing JSON', async () => {
  let cancelled = false;
  const client = new OpenAIClient({ apiKey: 'test', maxResponseBytes: 1024, fetchImpl: async () => ({
    ok: true, headers: new Headers({ 'Content-Length': '999999' }),
    body: { getReader: () => assert.fail('body consumed'), cancel: async () => { cancelled = true; } },
  }) });
  await assert.rejects(client.generate('hello'), { code: 'response_too_large' });
  assert.equal(cancelled, true);
});

test('multiple CLI processes preserve every acknowledged append', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'moneygraph-lock-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const path = join(dir, 'memory.json');
  const outputs = await Promise.all(Array.from({ length: 8 }, (_, i) => new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ['src/cli.js', '--offline', `hello ${i}`], {
      env: { ...process.env, MEMORY_PATH: path, OPENAI_MODEL: '', OPENAI_TIMEOUT_MS: '' }, windowsHide: true,
    });
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => { stdout += chunk; });
    child.stderr.on('data', chunk => { stderr += chunk; });
    child.on('error', reject);
    child.on('exit', code => code === 0 ? resolve(JSON.parse(stdout)) : reject(new Error(stderr)));
  })));
  assert.equal(outputs.filter(x => x.memorySaved).length, 8);
  const memory = await new JsonMemory(path).load();
  assert.equal(memory.entries.length, 8);
  assert.equal(new Set(memory.entries.map(x => x.input)).size, 8);
});

test('recovers an old partial lock and retains valid unknown-source entries', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'moneygraph-recovery-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const path = join(dir, 'memory.json');
  await writeFile(`${path}.lock`, '{');
  await utimes(`${path}.lock`, new Date(0), new Date(0));
  const memory = new JsonMemory(path);
  await writeFile(path, JSON.stringify({ version: 1, entries: [
    { at: new Date().toISOString(), input: 'valid', result: { answer: { intent: 'help', reply: 'OK' }, source: 'future', fallbackReason: null } },
    { input: 'invalid' },
  ] }));
  const result = await memory.load();
  assert.equal(result.entries.length, 1);
  assert.equal(result.entries[0].result.source, 'future');
});
