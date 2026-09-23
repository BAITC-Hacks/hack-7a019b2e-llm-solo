import { parseArgs } from 'node:util';
import { run } from './app.js';
import { OpenAIClient } from './openai-client.js';
import { JsonMemory } from './memory.js';

const envOr = (name, fallback) => process.env[name]?.trim() || fallback;

try {
  const { values, positionals } = parseArgs({
    options: { offline: { type: 'boolean', default: false } }, allowPositionals: true,
  });
  const client = new OpenAIClient({
    apiKey: values.offline ? undefined : process.env.OPENAI_API_KEY,
    model: envOr('OPENAI_MODEL', 'gpt-4.1-mini'),
    timeoutMs: Number(envOr('OPENAI_TIMEOUT_MS', '10000')),
    baseUrl: envOr('OPENAI_BASE_URL', 'https://api.openai.com/v1/responses'),
    maxResponseBytes: Number(envOr('OPENAI_MAX_RESPONSE_BYTES', '1048576')),
  });
  const result = await run(positionals.join(' '), {
    client, memory: new JsonMemory(envOr('MEMORY_PATH', './.local/memory.json')),
  });
  console.log(JSON.stringify(result, null, 2));
} catch (error) {
  console.error(JSON.stringify({ error: error.message }));
  process.exitCode = 1;
}
