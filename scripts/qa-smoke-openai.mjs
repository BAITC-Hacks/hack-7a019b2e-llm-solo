// Единственная проверка, которую нельзя закрыть моком: принимает ли настоящий
// OpenAI нашу strict-схему и совпадает ли форма ответа с той, что разбирает клиент.
//
//   node scripts/qa-smoke-openai.mjs
//
// Запускается только вручную и только при заданном OPENAI_API_KEY. В npm test не
// входит намеренно: AGENTS.md запрещает платные вызовы в тестах.
//
// В запрос уходит нейтральная фраза. Данные хакатона, gid и суммы переводов
// сюда попадать не должны ни при каких обстоятельствах.
import { OpenAIClient, LLMError } from '../src/openai-client.js';
import { validateResponse, responseSchema } from '../src/validator.js';

const KEY = process.env.OPENAI_API_KEY?.trim();
const MODEL = process.env.OPENAI_MODEL?.trim() || 'gpt-4.1-mini';
const PROMPT = 'Привет! Помоги разобраться.';

if (!KEY) {
  console.error('Нет OPENAI_API_KEY. Задайте его в окружении или .env и повторите.');
  process.exit(1);
}

const line = (label, value) => console.log(`${label.padEnd(34)} ${value}`);
let raw;

// Same request the client builds, sent through a wrapper that keeps the payload
// so we can check the real shape against what the client assumes.
const client = new OpenAIClient({
  apiKey: KEY, model: MODEL, timeoutMs: 30000,
  fetchImpl: async (url, options) => {
    const response = await fetch(url, options);
    const body = await response.text();
    try { raw = JSON.parse(body); } catch { raw = { unparsed: body.slice(0, 400) }; }
    return new Response(body, { status: response.status, headers: response.headers });
  },
});

console.log(`Модель: ${MODEL}\nЗапрос: ${JSON.stringify(PROMPT)}\n`);
const started = Date.now();
let answer = null;
let failure = null;
try {
  answer = await client.generate(PROMPT);
} catch (error) {
  failure = error;
}
const elapsed = Date.now() - started;

if (failure) {
  line('РЕЗУЛЬТАТ', `ОТКАЗ — ${failure instanceof LLMError ? failure.code : failure.message}`);
  if (raw?.error) {
    line('  сообщение API', raw.error.message ?? '—');
    line('  тип', raw.error.type ?? '—');
    if (/model/i.test(raw.error.message ?? '')) {
      console.log('\nПохоже, модель недоступна вашему проекту. Возьмите доступную с поддержкой\nResponses API и Structured Outputs и задайте её в OPENAI_MODEL.');
    }
  } else if (raw) {
    line('  сырой ответ', JSON.stringify(raw).slice(0, 300));
  }
  process.exit(1);
}

line('РЕЗУЛЬТАТ', 'клиент разобрал ответ');
line('  время', `${elapsed} мс`);
line('  intent', answer.intent);
line('  reply', JSON.stringify(answer.reply).slice(0, 120));
console.log();

// Everything below checks assumptions hard-coded in openai-client.js.
const messages = (raw?.output ?? []).filter(item => item.type === 'message');
const blocks = messages.flatMap(item => item.content ?? []);
const checks = [
  ['status === "completed"', raw?.status === 'completed', raw?.status],
  ['в output есть блок type="message"', messages.length > 0, `${messages.length} шт`],
  ['внутри есть type="output_text"', blocks.some(b => b.type === 'output_text'), blocks.map(b => b.type).join(',') || '—'],
  ['текст — валидный JSON по схеме', (() => {
    try { validateResponse(blocks.filter(b => b.type === 'output_text').map(b => b.text).join('')); return true; }
    catch { return false; }
  })(), ''],
  ['ответ уложился в max_output_tokens', raw?.status === 'completed' && !raw?.incomplete_details, raw?.incomplete_details?.reason ?? 'да'],
  ['store=false принят без ошибки', raw?.store === false || raw?.store === undefined, String(raw?.store)],
];

let failed = 0;
for (const [label, ok, detail] of checks) {
  if (!ok) failed += 1;
  line(`${ok ? 'OK    ' : 'ПРОВАЛ'} ${label}`, detail);
}

if (raw?.usage) {
  console.log();
  line('токенов вход/выход', `${raw.usage.input_tokens ?? '?'} / ${raw.usage.output_tokens ?? '?'}`);
}

console.log();
if (failed) {
  console.log(`Не сошлось проверок: ${failed}. Клиент разбирает ответ по предположениям,\nкоторые реальный API не подтверждает — это и есть та дыра, ради которой тест написан.`);
  process.exit(1);
}
console.log('Все предположения клиента подтверждены живым API.');
console.log(`Схема отправлена как strict json_schema (${Object.keys(responseSchema.properties).join(', ')}).`);
