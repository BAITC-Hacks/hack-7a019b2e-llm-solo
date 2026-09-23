/* Node-bound floating chat. All history stays in memory, never localStorage. */
(function (root) {
  'use strict';
  const A = root.TraceAgent;
  const esc = A.escape;
  const prompts = [
    ['Объяснить роль', 'Какие наблюдаемые связи подтверждают или опровергают назначенную роль этого узла?'],
    ['Проверить гипотезу', 'Какие альтернативные объяснения есть у поведения этого узла и каких данных не хватает?'],
    ['Следующий шаг', 'Каких контрагентов этого узла стоит проверить следующими и на каких фактах основан выбор?']
  ];
  function create({ onOpenNode, onShowGraph }) {
    const dialog = document.querySelector('#agent-dialog');
    const companion = document.querySelector('#agent-companion');
    const petButton = document.querySelector('#agent-pet-button');
    const petStatus = document.querySelector('#agent-pet-status');
    const find = selector => dialog.querySelector(selector);
    const transport = A.createTransport();
    let graph = null, datasetHash = null, current = null, pending = null, unread = false;
    const sessions = new Map();
    function sessionFor(gid) {
      if (!sessions.has(gid)) {
        if (sessions.size >= 8) sessions.delete(sessions.keys().next().value);
        sessions.set(gid, { gid, conversationId: null, turns: [], draft: '', lastRequest: null, error: null, notice: '' });
      }
      const session = sessions.get(gid);
      sessions.delete(gid); sessions.set(gid, session);
      return session;
    }
    function cancel() {
      if (!pending) return;
      const job = pending; pending = null;
      job.controller.abort();
      job.session.notice = 'Ожидание остановлено. Запрос мог уже дойти до сервера; повтор использует тот же ID.';
      if (current) render();
    }
    function reset(newGraph, newHash) {
      cancel(); current = null; sessions.clear(); graph = newGraph; datasetHash = newHash;
      unread = false;
      find('#agent-question').value = '';
      if (dialog.open) dialog.close();
      render();
    }
    function select(gid) {
      if (!graph?.byId.has(gid)) return;
      if (current?.gid === gid) return;
      cancel(); unread = false;
      current = sessionFor(gid);
      const n = graph.byId.get(gid);
      find('#agent-gid').textContent = gid;
      find('#agent-role').textContent = root.MoneyGraph.roles[n.role].label + ' · роль алгоритма';
      find('#agent-context-meta').textContent = 'Кластер #' + n.cluster_id + ' / уровень ' + n.depth;
      find('#agent-dataset').textContent = datasetHash ? 'Граф / ' + datasetHash.slice(0, 12) : 'Версия графа не подтверждена';
      find('#agent-boundary').textContent = n.truncated_by_depth ? 'Этот узел на границе выгрузки: отсутствие исходящих не доказывает, что он конечный получатель.' :
        n.is_seed ? 'Входящий поток seed-клиента неполный. Оборот не равен балансу счёта.' :
          'Выборка ограничена обходом до четырёх переходов. За её пределами могут быть другие связи.';
      find('#agent-question').value = current.draft;
      render();
      find('#agent-scroll').scrollTop = 0;
    }
    function syncCompanion() {
      companion.dataset.open = String(dialog.open);
      companion.dataset.state = pending ? 'thinking' : current?.error ? 'error' : unread ? 'ready' : 'idle';
      petButton.setAttribute('aria-expanded', String(dialog.open));
      petButton.setAttribute('aria-label', dialog.open ? 'След: свернуть чат' : unread ? 'След: открыть готовый ответ' : 'След: открыть чат');
      petStatus.textContent = pending ? 'Ожидаю ответ сервера…' : current?.error ? 'Не получил ответ. Проверим?' : unread ? 'Ответ готов. Посмотрим?' :
        dialog.open ? 'Я рядом. Разберём след?' : current ? 'Контекст сохранён. Я рядом.' : 'Я рядом. Разберём след?';
    }
    function open(gid) {
      if (gid != null && !graph?.byId.has(gid)) return;
      if (gid != null) select(gid);
      unread = false;
      render();
      if (!dialog.open) dialog.show();
      syncCompanion();
      (find('#agent-question').disabled ? find('#agent-close') : find('#agent-question')).focus({ preventScroll: true });
    }
    function minimize() {
      if (dialog.open) dialog.close();
      syncCompanion();
      petButton.focus({ preventScroll: true });
    }
    function updateControls() {
      const busy = !!pending;
      const limit = !!current && current.turns.length >= 12;
      const unavailable = !current || !datasetHash;
      const input = find('#agent-question');
      input.disabled = busy || limit || unavailable;
      find('#agent-send').disabled = busy || limit || unavailable || !input.value.trim();
      find('#agent-start').disabled = busy || limit || unavailable;
      find('#agent-cancel').hidden = !busy;
      find('#agent-new').disabled = busy || !current;
      find('#agent-count').textContent = input.value.length + ' / ' + A.MAX_QUESTION;
      find('#agent-input-help').textContent = limit ? '12 ответов в записке. Начните новую, чтобы продолжить.' : 'Ctrl + Enter — отправить. Не вставляйте API-ключи и персональные данные.';
      dialog.querySelectorAll('[data-ai-prompt]').forEach(button => button.disabled = busy || limit || unavailable);
    }
    function render() {
      const busy = !!pending;
      find('#agent-empty').hidden = !!current;
      find('#agent-empty-text').textContent = graph?.byId.size ? 'Выберите клиента на графе или в списке. Я закреплю его контекст здесь.' : 'Сначала загрузите graph.json через импорт данных, затем выберите клиента на графе или в списке.';
      find('#agent-context').hidden = !current;
      find('#agent-boundary').hidden = !current;
      find('#agent-intro').hidden = !current || current.turns.length > 0 || busy || !!current.error;
      find('#agent-history').innerHTML = (current?.turns || []).map((turn, i) => '<article class="agent-note"><h3 class="agent-user-message">' + esc(turn.question) + '</h3><header><span>СЛЕД / ' +
        String(i + 1).padStart(2, '0') + '</span><span>ИИ · ТРЕБУЕТ ПРОВЕРКИ</span></header>' + A.answerMarkup(turn.answer) + '</article>').join('');
      find('#agent-history').setAttribute('aria-busy', String(busy));
      find('#agent-progress').hidden = !busy;
      find('#agent-pending-question').textContent = busy ? pending.request.question : '';
      find('#agent-status').textContent = !current ? 'Выберите узел для разбора' : busy ? 'Запрос отправлен. Ожидаем ответ сервера…' : current.error ? 'Ответ не получен · нужна проверка подключения или запроса' :
        current.notice ? 'Ожидание остановлено' : current.turns.length ? 'Записка готова. Проверьте выводы по данным.' : 'Готов к запросу · сервер ещё не проверен';
      const error = current ? !datasetHash ? A.messages.insecure_context : current.error?.message : null;
      find('#agent-error').hidden = !error;
      find('#agent-error-text').textContent = error || '';
      find('#agent-retry').hidden = !current?.lastRequest || busy || current?.error?.code === 'conversation_expired' || !datasetHash;
      find('#agent-notice').textContent = current?.notice || '';
      find('#agent-notice').hidden = !current?.notice;
      find('#agent-cancel-retry').hidden = !current?.notice || !current?.lastRequest || busy;
      updateControls();
      syncCompanion();
    }
    async function send(question, retry = false) {
      if (pending || !current || current.turns.length >= 12 || !datasetHash) return;
      const session = current;
      let request;
      try {
        request = retry && session.lastRequest ? session.lastRequest : A.makeRequest({
          datasetHash, nodeGid: session.gid, question, conversationId: session.conversationId });
      } catch (error) { session.error = error; render(); return; }
      session.lastRequest = request; session.error = null; session.notice = '';
      session.draft = request.question;
      find('#agent-question').value = session.draft;
      const job = { controller: new AbortController(), session, request };
      pending = job; render();
      find('#agent-progress').scrollIntoView({ block: 'nearest' });
      find('#agent-cancel').focus({ preventScroll: true });
      try {
        const response = await transport(request, graph.byId, job.controller.signal);
        if (pending !== job || current !== session) return;
        session.conversationId = response.conversation_id;
        session.turns.push({ question: request.question, answer: response.answer });
        session.draft = ''; session.lastRequest = null;
        unread = !dialog.open;
        find('#agent-question').value = '';
      } catch (error) {
        if (pending !== job || current !== session) return;
        if (error.name !== 'AbortError') session.error = error;
      } finally {
        if (pending === job) {
          pending = null; render();
          const destination = session.error ? find('#agent-error') : find('#agent-history').lastElementChild;
          if (dialog.open) {
            destination?.scrollIntoView({ block: 'start' });
            if (dialog.contains(document.activeElement)) find('#agent-question').focus({ preventScroll: true });
          }
        }
      }
    }
    find('#agent-form').addEventListener('submit', event => { event.preventDefault(); send(find('#agent-question').value); });
    find('#agent-question').addEventListener('input', event => { if (current) current.draft = event.target.value; updateControls(); });
    find('#agent-question').addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') { event.preventDefault(); send(event.target.value); }
    });
    dialog.addEventListener('click', event => {
      const button = event.target.closest('button');
      if (!button || button.disabled) return;
      if (button.id === 'agent-close') { minimize(); return; }
      if (!current) return;
      if (button.dataset.aiNode) { dialog.close(); onOpenNode(button.dataset.aiNode); }
      else if (button.dataset.aiPrompt) { find('#agent-question').value = prompts[Number(button.dataset.aiPrompt) - 1][1]; current.draft = find('#agent-question').value; updateControls(); find('#agent-question').focus(); }
      else if (button.id === 'agent-graph') onShowGraph(current.gid);
      else if (button.id === 'agent-start') send('Разбери выбранный узел: отдели наблюдаемые факты от гипотез, укажи ограничения и следующие проверки.');
      else if (button.id === 'agent-retry' || button.id === 'agent-cancel-retry') send('', true);
      else if (button.id === 'agent-cancel') { cancel(); find('#agent-question').focus(); }
      else if (button.id === 'agent-new') {
        sessions.delete(current.gid); current = sessionFor(current.gid);
        find('#agent-question').value = ''; render(); find('#agent-question').focus();
      }
    });
    petButton.addEventListener('click', () => dialog.open ? minimize() : open());
    dialog.addEventListener('cancel', event => { event.preventDefault(); minimize(); });
    dialog.addEventListener('close', syncCompanion);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && dialog.open && !document.querySelector('#node-dialog')?.open) { event.preventDefault(); minimize(); }
    });
    find('#agent-prompts').innerHTML = prompts.map(([label], i) => '<button type="button" data-ai-prompt="' + (i + 1) + '"><span>0' + (i + 1) + '</span>' + label + ' ↗</button>').join('');
    render();
    return { open, select, reset };
  }
  root.TraceAssistant = { create };
})(window);
