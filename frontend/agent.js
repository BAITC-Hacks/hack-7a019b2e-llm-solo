/* TRACE AI: same-origin transport, strict response validation, no provider secrets. */
(function (root) {
  'use strict';
  const VERSION = 'trace-ai.v1';
  const ENDPOINT = '/api/ai/analyze';
  const MAX_QUESTION = 2000;
  const sections = [
    ['facts', '01', 'Наблюдаемые факты'],
    ['hypotheses', '02', 'Рабочие гипотезы'],
    ['limitations', '03', 'Чего мы не знаем'],
    ['next_steps', '04', 'Что проверить дальше']
  ];
  const messages = {
    unavailable: 'ИИ пока не подключён. Muha нужно запустить POST /api/ai/analyze на том же адресе, что и TRACE.',
    network: 'Не удалось связаться с сервером. Проверьте соединение и повторите запрос.',
    timeout: 'Сервер не ответил за 60 секунд. Можно повторить запрос.',
    rate_limited: 'Лимит запросов исчерпан. Подождите немного перед повтором.',
    dataset_mismatch: 'На сервере другая версия графа. Загрузите один и тот же graph.json во фронтенд и бэкенд.',
    conversation_expired: 'Сессия на сервере завершена. Начните новую записку.',
    unauthorized: 'Сервер не разрешил запрос. Проверьте доступ к приложению; API-ключ в браузер вводить не нужно.',
    refused: 'Модель не смогла ответить на этот запрос. Переформулируйте вопрос о выбранном узле.',
    invalid_response: 'Ответ сервера не прошёл проверку формата, версии графа или ссылок. Он не добавлен в досье.',
    invalid_question: 'Введите вопрос длиной от 1 до 2000 символов.',
    insecure_context: 'Для проверки версии графа откройте TRACE через localhost или HTTPS.',
    server: 'Ошибка сервера анализа. Повторите запрос позже или сообщите Muha.'
  };
  class AgentError extends Error {
    constructor(code) { super(messages[code] || messages.server); this.name = 'AgentError'; this.code = code; }
  }
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function object(value, keys) {
    return value && typeof value === 'object' && !Array.isArray(value) &&
      Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
  }
  const text = (value, max) => typeof value === 'string' && value.trim().length > 0 && value.length <= max;
  function validateResponse(data, request, knownIds) {
    const invalid = () => { throw new AgentError('invalid_response'); };
    if (!object(data, ['schema_version', 'request_id', 'dataset_sha256', 'node_gid', 'conversation_id', 'answer']) ||
        data.schema_version !== VERSION || data.request_id !== request.request_id ||
        data.dataset_sha256 !== request.dataset_sha256 || data.node_gid !== request.node_gid ||
        typeof data.conversation_id !== 'string' || !/^[a-zA-Z0-9_-]{16,128}$/.test(data.conversation_id) ||
        (request.conversation_id && request.conversation_id !== data.conversation_id)) invalid();
    const answer = data.answer;
    if (!object(answer, ['summary', ...sections.map(([key]) => key)]) || !text(answer.summary, 1500)) invalid();
    for (const [key] of sections) {
      if (!Array.isArray(answer[key]) || answer[key].length > 8) invalid();
      for (const item of answer[key]) {
        if (!object(item, ['text', 'node_ids']) || !text(item.text, 1200) ||
            !Array.isArray(item.node_ids) || item.node_ids.length > 8 || (key === 'facts' && !item.node_ids.length)) invalid();
        if (item.node_ids.some(id => typeof id !== 'string' || !/^\d+$/.test(id) || !knownIds.has(id)) ||
            new Set(item.node_ids).size !== item.node_ids.length) invalid();
      }
    }
    return data;
  }
  async function fingerprint(bytes, cryptoApi = root.crypto) {
    if (!cryptoApi?.subtle) throw new AgentError('insecure_context');
    const digest = await cryptoApi.subtle.digest('SHA-256', bytes);
    return [...new Uint8Array(digest)].map(n => n.toString(16).padStart(2, '0')).join('');
  }
  function makeRequest({ datasetHash, nodeGid, question, conversationId = null }, cryptoApi = root.crypto) {
    if (typeof question !== 'string' || !text(question.trim(), MAX_QUESTION)) throw new AgentError('invalid_question');
    if (!/^[a-f0-9]{64}$/.test(datasetHash || '') || !cryptoApi?.randomUUID) throw new AgentError('insecure_context');
    if (typeof nodeGid !== 'string' || !/^\d+$/.test(nodeGid)) throw new AgentError('invalid_response');
    return { schema_version: VERSION, request_id: cryptoApi.randomUUID(), dataset_sha256: datasetHash,
      node_gid: nodeGid, conversation_id: conversationId, question: question.trim() };
  }
  function createTransport(fetcher = (...args) => root.fetch(...args), timeoutMs = 60000) {
    return async function analyze(request, knownIds, signal) {
      const controller = new AbortController();
      let timedOut = false;
      const abort = () => controller.abort();
      if (signal?.aborted) controller.abort();
      signal?.addEventListener('abort', abort, { once: true });
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
      try {
        const response = await fetcher(ENDPOINT, { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
          headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(request), signal: controller.signal });
        if (!response.ok) {
          if ([404, 405, 501, 503].includes(response.status)) throw new AgentError('unavailable');
          const code = ({ 401: 'unauthorized', 403: 'unauthorized', 409: 'dataset_mismatch', 410: 'conversation_expired',
            422: 'refused', 429: 'rate_limited', 504: 'timeout' })[response.status] || 'server';
          throw new AgentError(code);
        }
        if (!response.headers.get('content-type')?.includes('application/json')) throw new AgentError('invalid_response');
        const body = await response.text();
        if (body.length > 100000) throw new AgentError('invalid_response');
        let data;
        try { data = JSON.parse(body); } catch { throw new AgentError('invalid_response'); }
        if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        return validateResponse(data, request, knownIds);
      } catch (error) {
        if (timedOut) throw new AgentError('timeout');
        if (controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
        if (error instanceof AgentError) throw error;
        throw new AgentError('network');
      } finally {
        clearTimeout(timer);
        signal?.removeEventListener('abort', abort);
      }
    };
  }
  function answerMarkup(answer) {
    return '<p class="agent-summary">' + escape(answer.summary) + '</p><div class="agent-findings">' + sections.map(([key, index, title]) =>
      '<section class="agent-finding agent-' + key + '"><h4><span>' + index + '</span>' + title + '</h4>' +
      (answer[key].length ? '<ul>' + answer[key].map(item => '<li><p>' + escape(item.text) + '</p>' +
        (item.node_ids.length ? '<div class="agent-citations" aria-label="Упомянутые узлы">' + item.node_ids.map(id =>
          '<button type="button" data-ai-node="' + escape(id) + '" title="Открыть карточку клиента ' + escape(id) + '">↗ ' + escape(id) + '</button>').join('') + '</div>' : '') +
        '</li>').join('') + '</ul>' : '<p class="agent-no-data">Не указано в ответе.</p>') + '</section>').join('') + '</div>';
  }
  const api = { VERSION, ENDPOINT, MAX_QUESTION, sections, messages, AgentError, escape, fingerprint, makeRequest, validateResponse, createTransport, answerMarkup };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TraceAgent = api;
})(typeof window !== 'undefined' ? window : globalThis);
