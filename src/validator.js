const intents = Object.freeze(['greeting', 'help', 'unknown']);
// Nested values are fresh snapshots: consumers cannot mutate the contract.
export const responseSchema = Object.freeze({
  type: 'object',
  get properties() {
    return { intent: { type: 'string', enum: [...intents] }, reply: { type: 'string' } };
  },
  get required() { return ['intent', 'reply']; },
  additionalProperties: false,
});

export function validateInput(value) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error('Введите непустой текст.');
  }
  if (value.length > 10000) throw new Error('Максимум 10000 символов.');
  return value.trim();
}

export function validateResponse(value) {
  const data = typeof value === 'string' ? JSON.parse(value) : value;
  if (!data || typeof data !== 'object' || Array.isArray(data)
      || Object.keys(data).length !== 2
      || !Object.hasOwn(data, 'intent') || !Object.hasOwn(data, 'reply')
      || !intents.includes(data.intent)
      || typeof data.reply !== 'string' || !data.reply.trim()
      || data.reply.length > 4000) {
    throw new Error('Некорректный ответ LLM.');
  }
  return { intent: data.intent, reply: data.reply.trim() };
}
