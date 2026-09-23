import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

test('CLI produces JSON offline and exits cleanly on empty input', async t => {
  const dir = await mkdtemp(join(tmpdir(), 'llm-solo-cli-'));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const options = { encoding: 'utf8', env: { ...process.env, MEMORY_PATH: join(dir, 'memory.json'), OPENAI_TIMEOUT_MS: '1000', OPENAI_MODEL: 'test' } };
  const ok = spawnSync(process.execPath, ['src/cli.js', '--offline', 'Привет'], options);
  assert.equal(ok.status, 0, ok.stderr);
  assert.equal(JSON.parse(ok.stdout).answer.intent, 'greeting');
  assert.equal(JSON.parse(ok.stdout).memorySaved, true);
  const empty = spawnSync(process.execPath, ['src/cli.js', '--offline'], options);
  assert.equal(empty.status, 1);
  assert.equal(empty.stdout, '');
  assert.match(JSON.parse(empty.stderr).error, /непустой/);
});
