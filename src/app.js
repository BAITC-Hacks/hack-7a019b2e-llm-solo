import { validateInput, validateResponse } from './validator.js';
import { applyRules } from './rules.js';
import { LLMError } from './openai-client.js';

export async function run(input, { client, memory } = {}) {
  const text = validateInput(input);
  let result;
  try {
    if (typeof client?.generate !== 'function') throw new LLMError('configuration_error');
    const output = await client.generate(text);
    let answer;
    try { answer = validateResponse(output); }
    catch { throw new LLMError('invalid_output'); }
    result = { answer, source: 'llm', fallbackReason: null };
  } catch (error) {
    result = {
      answer: validateResponse(applyRules(text)),
      source: 'rules',
      fallbackReason: error instanceof LLMError ? error.code : 'client_error',
    };
  }
  let memorySaved = false;
  try { memorySaved = memory ? (await memory.append(text, result)) === true : false; }
  catch { /* Persistence failure must not discard an already validated answer. */ }
  return { ...result, memorySaved };
}
