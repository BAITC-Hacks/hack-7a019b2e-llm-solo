// Replace these demo rules when the product domain is defined.
export function applyRules(text) {
  const words = text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
  if (words.some(word => ['помощь', 'помоги', 'help'].includes(word))) {
    return { intent: 'help', reply: 'Опишите задачу подробнее.' };
  }
  if (words.some(word => ['привет', 'здравствуйте', 'hello', 'hi'].includes(word))) {
    return { intent: 'greeting', reply: 'Здравствуйте! Чем помочь?' };
  }
  return { intent: 'unknown', reply: 'Уточните запрос, пожалуйста.' };
}
