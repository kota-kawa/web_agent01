const state = {
  activeSession: null,
  pollTimer: null,
  renderedSteps: 0,
  previewMode: 'screenshot',
  liveViewLoaded: false,
};

const chatArea = document.getElementById('chat-area');
const userInput = document.getElementById('user-input');
const sendButton = document.getElementById('send-button');
const stopButton = document.getElementById('stop-button');
const resetButton = document.getElementById('reset-button');
const previewImage = document.getElementById('preview-image');
const previewPlaceholder = document.getElementById('preview-placeholder');
const previewStatus = document.getElementById('preview-status');
const screenshotContainer = document.getElementById('screenshot-container');
const liveBrowserContainer = document.getElementById('live-browser-container');
const liveBrowserFrame = document.getElementById('live-browser-frame');
const liveBrowserUnavailable = document.getElementById('live-browser-unavailable');
const previewModeButtons = document.querySelectorAll('[data-preview-mode]');

function escapeHtml(value) {
  if (typeof value !== 'string') return '';
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function normaliseScreenshot(data) {
  if (!data || typeof data !== 'string') return null;
  if (data.startsWith('data:image')) return data;
  return `data:image/png;base64,${data}`;
}

function appendMessage(kind, content) {
  const message = document.createElement('p');
  if (kind === 'user') {
    message.classList.add('user-message');
  } else if (kind === 'system') {
    message.classList.add('system-message');
  } else {
    message.classList.add('bot-message');
  }
  message.innerHTML = content;
  chatArea.appendChild(message);
  chatArea.scrollTop = chatArea.scrollHeight;
  return message;
}

function setExecuting(isExecuting) {
  if (isExecuting) {
    sendButton.disabled = true;
    sendButton.textContent = '実行中...';
    stopButton.disabled = false;
  } else {
    sendButton.disabled = false;
    sendButton.textContent = '送信';
    stopButton.disabled = true;
  }
}

function syncPreviewModeUI() {
  const isLive = state.previewMode === 'live';

  if (screenshotContainer) {
    screenshotContainer.hidden = isLive;
  }

  if (liveBrowserContainer) {
    liveBrowserContainer.hidden = !isLive;
  }

  previewModeButtons.forEach((button) => {
    const mode = button.dataset.previewMode === 'live' ? 'live' : 'screenshot';
    const active = mode === state.previewMode;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
}

function initialiseLiveView() {
  if (state.liveViewLoaded || !liveBrowserContainer || !liveBrowserFrame) {
    return;
  }

  const configuredUrl = typeof window.NOVNC_URL === 'string' ? window.NOVNC_URL.trim() : '';
  if (!configuredUrl) {
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.textContent = 'ライブビューの URL が設定されていません。';
    }
    return;
  }

  liveBrowserFrame.src = configuredUrl;
  liveBrowserContainer.classList.add('is-ready');
  state.liveViewLoaded = true;
}

function setPreviewMode(mode) {
  const resolvedMode = mode === 'live' ? 'live' : 'screenshot';
  state.previewMode = resolvedMode;
  syncPreviewModeUI();

  if (resolvedMode === 'live') {
    initialiseLiveView();
  }
}

function updatePreview(step) {
  const screenshot = normaliseScreenshot(step.screenshot);
  if (screenshot) {
    previewImage.src = screenshot;
    previewImage.style.display = 'block';
    previewPlaceholder.style.display = 'none';
  } else {
    previewImage.style.display = 'none';
    previewPlaceholder.style.display = 'block';
  }

  const url = step.url ? escapeHtml(step.url) : '不明';
  const actionSummary = (step.actions || []).map((action) => {
    const [name] = Object.keys(action);
    return name || 'action';
  });

  const actionsLabel = actionSummary.length
    ? escapeHtml(actionSummary.join(', '))
    : '操作情報なし';

  const timestamp = step.timestamp
    ? new Date(step.timestamp * 1000).toLocaleString()
    : '';

  previewStatus.innerHTML = `
    <div><strong>URL:</strong> ${url}</div>
    <div><strong>アクション:</strong> ${actionsLabel}</div>
    ${timestamp ? `<div><strong>時刻:</strong> ${escapeHtml(timestamp)}</div>` : ''}
  `;
}

function renderActions(actions) {
  const list = document.createElement('ul');
  list.classList.add('step-action-list');

  if (!actions || !actions.length) {
    const emptyItem = document.createElement('li');
    emptyItem.classList.add('step-action-empty');
    emptyItem.textContent = 'アクションなし';
    list.appendChild(emptyItem);
    return list;
  }

  actions.forEach((action) => {
    const [name, params] = Object.entries(action)[0] || ['操作', {}];
    const item = document.createElement('li');

    const nameSpan = document.createElement('span');
    nameSpan.classList.add('action-name');
    nameSpan.textContent = name || '操作';
    item.appendChild(nameSpan);

    if (params && typeof params === 'object' && Object.keys(params).length) {
      const paramsSpan = document.createElement('span');
      paramsSpan.classList.add('action-params');
      const paramText = Object.entries(params)
        .map(([key, value]) => {
          const printableValue =
            value === null || value === undefined
              ? ''
              : typeof value === 'object'
              ? JSON.stringify(value)
              : String(value);
          return `${key}=${printableValue}`;
        })
        .join(', ');
      paramsSpan.textContent = paramText;
      item.appendChild(paramsSpan);
    }

    list.appendChild(item);
  });

  return list;
}

function createStepSection(label, content) {
  if (content instanceof Node) {
    // use the provided node as-is
  } else {
    const normalised = content === null || content === undefined ? '' : String(content).trim();
    if (!normalised) {
      return null;
    }
    content = normalised;
  }

  const section = document.createElement('div');
  section.classList.add('step-section');

  const heading = document.createElement('div');
  heading.classList.add('step-section-label');
  heading.textContent = label;
  section.appendChild(heading);

  const body = document.createElement('div');
  body.classList.add('step-card', 'step-section-body');

  if (content instanceof Node) {
    body.appendChild(content);
  } else {
    body.textContent = content;
  }

  section.appendChild(body);
  return section;
}

function renderStep(step) {
  const container = document.createElement('div');
  container.classList.add('bot-message', 'step-message');

  const header = document.createElement('div');
  header.classList.add('step-header');

  const indexBadge = document.createElement('span');
  indexBadge.classList.add('step-index');
  indexBadge.textContent = `STEP ${step.index}`;
  header.appendChild(indexBadge);

  const title = document.createElement('span');
  title.classList.add('step-title');
  const titleText = step.title ? String(step.title).trim() : '無題のページ';
  title.textContent = titleText;
  title.title = titleText;
  header.appendChild(title);

  container.appendChild(header);

  const metaRow = document.createElement('div');
  metaRow.classList.add('step-meta-row');

  const metaLabel = document.createElement('span');
  metaLabel.classList.add('step-meta-label');
  metaLabel.textContent = 'URL';
  metaRow.appendChild(metaLabel);

  const hasUrl = step.url && typeof step.url === 'string' && step.url.trim().length;
  const urlElement = document.createElement(hasUrl ? 'a' : 'span');
  urlElement.classList.add('step-url');
  const urlText = hasUrl ? step.url.trim() : '不明';
  urlElement.textContent = urlText;
  if (hasUrl) {
    urlElement.href = urlText;
    urlElement.target = '_blank';
    urlElement.rel = 'noopener noreferrer';
  }
  metaRow.appendChild(urlElement);

  container.appendChild(metaRow);

  const sections = [
    createStepSection('思考', step.thinking),
    createStepSection('次の目標', step.next_goal),
    createStepSection('メモリ', step.memory),
  ];
  sections.forEach((section) => {
    if (section) {
      container.appendChild(section);
    }
  });

  const actionsSection = createStepSection('実行アクション', renderActions(step.actions));
  if (actionsSection) {
    container.appendChild(actionsSection);
  }

  chatArea.appendChild(container);
  chatArea.scrollTop = chatArea.scrollHeight;
  updatePreview(step);
}

function clearPolling() {
  if (state.pollTimer) {
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

async function pollSession() {
  if (!state.activeSession) return;

  try {
    const response = await fetch(`/status/${state.activeSession.id}`);
    if (!response.ok) {
      throw new Error(`status ${response.status}`);
    }
    const data = await response.json();
    const steps = Array.isArray(data.steps) ? data.steps : [];

    while (state.renderedSteps < steps.length) {
      renderStep(steps[state.renderedSteps]);
      state.renderedSteps += 1;
    }

    const statusText = escapeHtml(data.status || 'unknown');
    if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
      handleCompletion(data);
    } else {
      previewStatus.innerHTML += `<div><strong>ステータス:</strong> ${statusText}</div>`;
      state.pollTimer = setTimeout(pollSession, 1200);
    }
  } catch (err) {
    appendMessage('system', `❌ 状態取得に失敗しました: ${escapeHtml(err.message || String(err))}`);
    setExecuting(false);
    clearPolling();
    state.activeSession = null;
  }
}

function handleCompletion(payload) {
  const { status, error, result } = payload;
  clearPolling();
  setExecuting(false);
  const finalStatus = status || 'completed';

  if (finalStatus === 'failed') {
    appendMessage('system', `❌ 実行に失敗しました: ${escapeHtml(error || '原因不明のエラー')}`);
  } else if (finalStatus === 'cancelled') {
    appendMessage('system', '⏹ 実行をキャンセルしました。');
  } else if (result) {
    const success = result.success !== false;
    const summary = result.final_result || 'ブラウザ操作が完了しました。';
    const prefix = success ? '✅' : '⚠️';
    appendMessage('system', `${prefix} ${escapeHtml(summary)}`);
    if (Array.isArray(result.errors) && result.errors.length) {
      const list = result.errors.map((err) => `<li>${escapeHtml(String(err))}</li>`).join('');
      appendMessage('system', `⚠️ 実行中に警告が発生しました:<ul>${list}</ul>`);
    }
    if (Array.isArray(result.urls) && result.urls.length) {
      previewStatus.innerHTML += `<div><strong>訪問URL:</strong> ${escapeHtml(result.urls[result.urls.length - 1])}</div>`;
    }
  } else {
    appendMessage('system', '✅ ブラウザ操作が完了しました。');
  }

  state.activeSession = null;
}

async function startSession(command) {
  if (!command) return;
  if (state.activeSession) {
    appendMessage('system', '⚠️ 現在の実行が完了するまでお待ちください。');
    return;
  }

  appendMessage('user', escapeHtml(command));
  const placeholder = appendMessage('system', 'AI が応答中... <span class="spinner"></span>');
  setExecuting(true);
  state.renderedSteps = 0;

  try {
    const response = await fetch('/execute', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        command,
        model: window.DEFAULT_MODEL || 'gemini',
        max_steps: window.MAX_STEPS || undefined,
      }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `server returned ${response.status}`);
    }

    const data = await response.json();
    placeholder.remove();
    state.activeSession = { id: data.session_id };
    pollSession();
  } catch (err) {
    placeholder.remove();
    appendMessage('system', `❌ 実行開始に失敗しました: ${escapeHtml(err.message || String(err))}`);
    setExecuting(false);
  }
}

async function cancelSession() {
  if (!state.activeSession) return;
  try {
    await fetch(`/cancel/${state.activeSession.id}`, { method: 'POST' });
    appendMessage('system', '⏹ 停止リクエストを送信しました。');
  } catch (err) {
    appendMessage('system', `⚠️ 停止リクエストに失敗しました: ${escapeHtml(err.message || String(err))}`);
  }
}

async function resetHistory() {
  try {
    const response = await fetch('/reset', { method: 'POST' });
    const data = await response.json();
    chatArea.innerHTML = '<p class="bot-message">こんにちは！ご質問はありますか？</p>';
    appendMessage('system', escapeHtml(data.message || '会話履歴がリセットされました。'));
  } catch (err) {
    appendMessage('system', `⚠️ リセットに失敗しました: ${escapeHtml(err.message || String(err))}`);
  }
}

previewModeButtons.forEach((button) => {
  button.addEventListener('click', () => {
    setPreviewMode(button.dataset.previewMode);
  });
});

syncPreviewModeUI();

sendButton.addEventListener('click', () => {
  const command = userInput.value.trim();
  if (!command) return;
  userInput.value = '';
  startSession(command);
});

userInput.addEventListener('keydown', (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    event.preventDefault();
    sendButton.click();
  }
});

stopButton.addEventListener('click', () => {
  cancelSession();
});

resetButton.addEventListener('click', () => {
  if (state.activeSession) {
    appendMessage('system', '⚠️ 実行中はリセットできません。まず停止してください。');
    return;
  }
  resetHistory();
});

stopButton.disabled = true;
userInput.focus();
