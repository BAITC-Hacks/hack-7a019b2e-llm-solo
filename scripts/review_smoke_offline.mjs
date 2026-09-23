// Audit the QA smoke wrapper in isolated children with fetch replaced.
// No real key or network request is used; the QA source is not modified.
import { spawnSync } from 'node:child_process';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const smokeUrl = new URL('./qa-smoke-openai.mjs', import.meta.url).href;
const dotenvFixture = new URL('../artifacts/research/smoke-dotenv-fixture/', import.meta.url);
mkdirSync(dotenvFixture, { recursive: true });
writeFileSync(new URL('.env', dotenvFixture), 'OPENAI_API_KEY=offline-dummy-from-fixture\n');
const results = [];
for (const mode of ['no_key', 'valid', 'large_response', 'dotenv_only']) {
  const childSource = `
    const mode = ${JSON.stringify(mode)};
    globalThis.fetch = async (_url, options) => {
      const request = JSON.parse(options.body);
      console.log('OFFLINE_REQUEST', JSON.stringify({
        input: request.input, store: request.store,
        strict: request.text.format.strict, max_output_tokens: request.max_output_tokens
      }));
      const payload = {
        status: 'completed', store: false,
        output: [{ type: 'message', content: [
          { type: 'output_text', text: JSON.stringify({ intent: 'greeting', reply: 'Привет.' }) }
        ] }]
      };
      if (mode === 'large_response') payload.padding = 'x'.repeat(4 * 1024 * 1024);
      const bytes = new TextEncoder().encode(JSON.stringify(payload));
      let offset = 0;
      return new Response(new ReadableStream({
        pull(controller) {
          if (offset === bytes.byteLength) {
            console.log('OFFLINE_TRANSPORT_FULLY_READ', offset);
            controller.close(); return;
          }
          const end = Math.min(offset + 16384, bytes.byteLength);
          controller.enqueue(bytes.slice(offset, end)); offset = end;
        },
        cancel() { console.log('OFFLINE_TRANSPORT_CANCELLED', offset); }
      }), { status: 200, headers: { 'content-type': 'application/json' } });
    };
    await import(${JSON.stringify(smokeUrl)});
  `;
  const childEnv = { ...process.env, OPENAI_API_KEY: 'offline-dummy',
                     OPENAI_MODEL: 'offline-model', NODE_OPTIONS: '' };
  if (['no_key', 'dotenv_only'].includes(mode)) delete childEnv.OPENAI_API_KEY;
  const run = spawnSync(process.execPath, ['--input-type=module', '-e', childSource], {
    cwd: mode === 'dotenv_only' ? fileURLToPath(dotenvFixture) : root,
    encoding: 'utf8', timeout: 10000,
    env: childEnv,
  });
  const result = {
    mode, exit_code: run.status, output: run.stdout, error_output: run.stderr,
    fetch_invoked: run.stdout.includes('OFFLINE_REQUEST'),
    original_response_read_in_full: run.stdout.includes('OFFLINE_TRANSPORT_FULLY_READ'),
  };
  results.push(result);
  console.log(JSON.stringify(result));
}
if (results[0].exit_code !== 1 || results[0].fetch_invoked ||
    results[1].exit_code !== 0 || results[2].exit_code !== 1 ||
    !results[2].output.includes('response_too_large') ||
    !results[2].original_response_read_in_full ||
    results[3].exit_code !== 1 || results[3].fetch_invoked) {
  throw new Error('Offline smoke audit did not reproduce expected behavior');
}
const out = new URL('../artifacts/research/', import.meta.url);
mkdirSync(out, { recursive: true });
writeFileSync(new URL('smoke_review.json', out), JSON.stringify({
  warning: 'Offline wrapper audit only; no evidence of live API acceptance.',
  results,
}, null, 2));
