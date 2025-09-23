const state = {
  activeSession: null,
  pollTimer: null,
  renderedSteps: 0,
  previewMode: 'live',
  liveViewInitialised: false,
  liveViewLoaded: false,
  liveViewTimeoutId: null,
  liveViewListeners: null,
  liveViewInstance: null,
  liveViewRetryId: null,
  liveViewRetryCount: 0,
  liveViewAwaitingLibrary: !window.__NOVNC_READY__,
  latestStep: null,
  lastPreviewImage: null,
  displayedWarnings: new Set(),
  sharedBrowserMode: 'unknown',
  liveViewDisabled: false,
  liveViewDisabledMessage: '',
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
const liveBrowserSurface = document.getElementById('live-browser-canvas');
const liveBrowserUnavailable = document.getElementById('live-browser-unavailable');
const previewModeButtons = document.querySelectorAll('[data-preview-mode]');

if (!window.__NOVNC_READY__) {
  document.addEventListener(
    'novnc:ready',
    () => {
      state.liveViewAwaitingLibrary = false;
      if (state.previewMode === 'live') {
        initialiseLiveView(true);
      }
    },
    { once: true },
  );
} else {
  state.liveViewAwaitingLibrary = false;
}

document.addEventListener('novnc:error', (event) => {
  state.liveViewAwaitingLibrary = false;
  if (state.previewMode !== 'live') {
    return;
  }
  clearLiveViewRetry();
  clearLiveViewWatchdog();
  disconnectLiveView(true);
  if (liveBrowserContainer) {
    liveBrowserContainer.classList.remove('is-loading', 'is-ready');
    liveBrowserContainer.classList.add('has-error');
  }
  if (liveBrowserUnavailable) {
    const detail = event && event.detail ? String(event.detail) : '';
    liveBrowserUnavailable.textContent = detail
      ? `ライブビューのライブラリ読み込みに失敗しました（${detail}）。`
      : 'ライブビューのライブラリ読み込みに失敗しました。';
    liveBrowserUnavailable.removeAttribute('aria-busy');
  }
});

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

function showSharedBrowserError(detailMessage) {
  clearLiveViewRetry();
  clearLiveViewWatchdog();
  disconnectLiveView(true);

  const fallbackText =
    typeof detailMessage === 'string' && detailMessage.trim().length
      ? detailMessage.trim()
      : 'ライブビューのブラウザに接続できないため実行できません。';
  state.liveViewDisabled = true;
  state.liveViewDisabledMessage = fallbackText;

  if (liveBrowserContainer) {
    liveBrowserContainer.classList.remove('is-loading', 'is-ready');
    liveBrowserContainer.classList.add('has-error');
  }

  if (liveBrowserUnavailable) {
    liveBrowserUnavailable.textContent = fallbackText;
    liveBrowserUnavailable.removeAttribute('aria-busy');
  }
}

function captureLiveViewFrame() {
  if (!state.liveViewLoaded || !liveBrowserSurface) {
    return null;
  }

  const canvas = liveBrowserSurface.querySelector('canvas');
  if (!canvas) {
    return null;
  }

  try {
    const dataUrl = canvas.toDataURL('image/png');
    if (dataUrl && dataUrl !== 'data:,') {
      state.lastPreviewImage = dataUrl;
      return dataUrl;
    }
  } catch (err) {
    // Ignore capture errors; fall back to existing screenshots when needed.
  }

  return null;
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

function clearLiveViewWatchdog() {
  if (state.liveViewTimeoutId) {
    clearTimeout(state.liveViewTimeoutId);
    state.liveViewTimeoutId = null;
  }
}

function clearLiveViewRetry() {
  if (state.liveViewRetryId) {
    clearTimeout(state.liveViewRetryId);
    state.liveViewRetryId = null;
  }
}

function scheduleLiveViewRetry(reason) {
  if (state.previewMode !== 'live') {
    return;
  }

  const attempt = state.liveViewRetryCount + 1;
  state.liveViewRetryCount = attempt;

  const baseDelay = reason === 'timeout' ? 6000 : 4000;
  const delay = Math.min(15000, baseDelay + attempt * 1000);

  if (liveBrowserUnavailable) {
    const prefix =
      reason === 'timeout'
        ? 'ライブビューの応答がありません。'
        : 'ライブビューの接続に失敗しました。';
    const attemptLabel = attempt > 1 ? `（${attempt}回目）` : '';
    const seconds = Math.max(4, Math.round(delay / 1000));
    liveBrowserUnavailable.textContent =
      `${prefix}再接続を試みています...${attemptLabel}（約${seconds}秒後に再試行）`;
    liveBrowserUnavailable.setAttribute('aria-busy', 'true');
  }

  clearLiveViewRetry();
  state.liveViewRetryId = window.setTimeout(() => {
    state.liveViewRetryId = null;
    initialiseLiveView(true);
  }, delay);
}

function removeLiveViewListeners() {
  if (!state.liveViewListeners || !state.liveViewInstance) {
    return;
  }

  const { connectHandler, disconnectHandler, credentialsHandler } = state.liveViewListeners;
  const instance = state.liveViewInstance;

  if (connectHandler) {
    instance.removeEventListener('connect', connectHandler);
  }
  if (disconnectHandler) {
    instance.removeEventListener('disconnect', disconnectHandler);
  }
  if (credentialsHandler) {
    instance.removeEventListener('credentialsrequired', credentialsHandler);
  }

  state.liveViewListeners = null;
}

function disconnectLiveView(manual = false) {
  clearLiveViewWatchdog();
  removeLiveViewListeners();

  const instance = state.liveViewInstance;
  if (instance && typeof instance.disconnect === 'function') {
    try {
      instance.disconnect();
    } catch (err) {
      // Ignore disconnect errors
    }
  }

  state.liveViewInstance = null;
  state.liveViewInitialised = manual ? false : state.liveViewInitialised;
  state.liveViewLoaded = false;
}

function initialiseLiveView(forceReload = false) {
  if (!liveBrowserContainer || !liveBrowserSurface) {
    return;
  }

  clearLiveViewRetry();
  clearLiveViewWatchdog();

  if (state.liveViewDisabled) {
    disconnectLiveView();
    state.liveViewInitialised = false;
    state.liveViewLoaded = false;
    if (liveBrowserContainer) {
      liveBrowserContainer.classList.remove('is-loading', 'is-ready');
      liveBrowserContainer.classList.add('has-error');
    }
    if (liveBrowserUnavailable) {
      const text =
        typeof state.liveViewDisabledMessage === 'string' &&
        state.liveViewDisabledMessage.trim().length
          ? state.liveViewDisabledMessage
          : 'ライブビューは現在利用できません。';
      liveBrowserUnavailable.textContent = text;
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
    return;
  }

  if (window.__NOVNC_LOAD_FAILED__) {
    disconnectLiveView();
    state.liveViewInitialised = false;
    state.liveViewLoaded = false;
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.textContent =
        'ライブビューのライブラリ読み込みに失敗しました。ページを再読み込みしてください。';
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
    liveBrowserContainer.classList.remove('is-loading', 'is-ready');
    liveBrowserContainer.classList.add('has-error');
    return;
  }

  if (state.liveViewAwaitingLibrary || typeof window.__NOVNC_RFB__ !== 'function') {
    disconnectLiveView();
    state.liveViewAwaitingLibrary = true;
    state.liveViewInitialised = false;
    state.liveViewLoaded = false;
    if (liveBrowserContainer) {
      liveBrowserContainer.classList.remove('has-error', 'is-ready');
      liveBrowserContainer.classList.add('is-loading');
    }
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.textContent = 'ライブビューのライブラリを読み込んでいます...';
      liveBrowserUnavailable.setAttribute('aria-busy', 'true');
    }
    return;
  }

  const configuredUrl = typeof window.NOVNC_URL === 'string' ? window.NOVNC_URL.trim() : '';
  if (!configuredUrl) {
    disconnectLiveView();
    state.liveViewInitialised = false;
    state.liveViewLoaded = false;
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.textContent = 'ライブビューの URL が設定されていません。';
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
    liveBrowserContainer.classList.remove('is-loading', 'is-ready');
    liveBrowserContainer.classList.add('has-error');
    return;
  }

  const resolveUrl = (url) => {
    if (url.startsWith('ws://') || url.startsWith('wss://')) {
      return url;
    }
    if (url.startsWith('//')) {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${protocol}${url}`;
    }
    if (url.startsWith('/')) {
      const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
      return `${protocol}${window.location.host}${url}`;
    }
    try {
      const parsed = new URL(url, window.location.href);
      if (parsed.protocol === 'ws:' || parsed.protocol === 'wss:') {
        return parsed.toString();
      }
      if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
        const protocol = parsed.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${parsed.host}${parsed.pathname}${parsed.search}`;
      }
      return parsed.toString();
    } catch (err) {
      return url;
    }
  };

  const resolvedUrl = resolveUrl(configuredUrl);

  if (state.liveViewInitialised && state.liveViewLoaded && !forceReload) {
    return;
  }

  const isRetry = forceReload || (state.liveViewInitialised && !state.liveViewLoaded);

  state.liveViewInitialised = true;
  state.liveViewLoaded = false;
  state.liveViewAwaitingLibrary = false;

  disconnectLiveView();
  state.liveViewRetryCount = isRetry ? state.liveViewRetryCount : 0;

  liveBrowserContainer.classList.remove('has-error', 'is-ready');
  liveBrowserContainer.classList.add('is-loading');
  if (liveBrowserUnavailable) {
    liveBrowserUnavailable.textContent = isRetry
      ? 'ライブビューを再接続しています...'
      : 'ライブビューを初期化しています...';
    liveBrowserUnavailable.setAttribute('aria-busy', 'true');
  }

  let urlToConnect = resolvedUrl;
  if (isRetry) {
    try {
      const parsed = new URL(resolvedUrl);
      parsed.searchParams.set('_ts', Date.now().toString());
      urlToConnect = parsed.toString();
    } catch (err) {
      const separator = resolvedUrl.includes('?') ? '&' : '?';
      urlToConnect = `${resolvedUrl}${separator}_ts=${Date.now()}`;
    }
  }

  let rfbInstance;
  try {
    const RFBConstructor = window.__NOVNC_RFB__;
    rfbInstance = new RFBConstructor(liveBrowserSurface, urlToConnect, { shared: true });
    rfbInstance.viewOnly = false;
    rfbInstance.scaleViewport = true;
    rfbInstance.focusOnClick = true;
    rfbInstance.background = '#0b1120';
    if ('resizeSession' in rfbInstance) {
      rfbInstance.resizeSession = true;
    }
    if ('clipViewport' in rfbInstance) {
      rfbInstance.clipViewport = true;
    }
  } catch (err) {
    state.liveViewInitialised = false;
    if (liveBrowserContainer) {
      liveBrowserContainer.classList.remove('is-loading', 'is-ready');
      liveBrowserContainer.classList.add('has-error');
    }
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.textContent = `ライブビューの初期化に失敗しました: ${err.message || err}`;
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
    scheduleLiveViewRetry('error');
    return;
  }

  state.liveViewInstance = rfbInstance;

  if (liveBrowserSurface && !liveBrowserSurface.hasAttribute('tabindex')) {
    liveBrowserSurface.setAttribute('tabindex', '0');
  }

  const handleLiveConnect = () => {
    clearLiveViewWatchdog();
    clearLiveViewRetry();
    state.liveViewRetryCount = 0;
    state.liveViewLoaded = true;
    liveBrowserContainer.classList.remove('is-loading', 'has-error');
    liveBrowserContainer.classList.add('is-ready');
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
    if (liveBrowserSurface) {
      liveBrowserSurface.focus({ preventScroll: true });
    }
  };

  const handleLiveDisconnect = (event) => {
    clearLiveViewWatchdog();
    state.liveViewLoaded = false;
    removeLiveViewListeners();
    state.liveViewInstance = null;
    liveBrowserContainer.classList.remove('is-loading', 'is-ready');
    liveBrowserContainer.classList.add('has-error');
    if (liveBrowserUnavailable) {
      const reason =
        event && event.detail && event.detail.reason
          ? String(event.detail.reason)
          : 'ライブビューの接続が終了しました。';
      liveBrowserUnavailable.textContent = `${reason} 再接続を試みています...`;
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
    scheduleLiveViewRetry('error');
  };

  const handleCredentialsRequired = () => {
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.textContent = 'ライブビューの認証情報が必要です。';
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
    try {
      rfbInstance.sendCredentials({ password: '' });
    } catch (err) {
      // Ignore credential submission errors
    }
  };

  rfbInstance.addEventListener('connect', handleLiveConnect);
  rfbInstance.addEventListener('disconnect', handleLiveDisconnect);
  rfbInstance.addEventListener('credentialsrequired', handleCredentialsRequired);
  state.liveViewListeners = {
    connectHandler: handleLiveConnect,
    disconnectHandler: handleLiveDisconnect,
    credentialsHandler: handleCredentialsRequired,
  };

  state.liveViewTimeoutId = window.setTimeout(() => {
    if (state.liveViewLoaded) {
      state.liveViewTimeoutId = null;
      return;
    }
    removeLiveViewListeners();
    try {
      rfbInstance.disconnect();
    } catch (err) {
      // Ignore disconnect errors during timeout handling
    }
    state.liveViewInstance = null;
    state.liveViewInitialised = false;
    liveBrowserContainer.classList.remove('is-loading', 'is-ready');
    liveBrowserContainer.classList.add('has-error');
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.textContent =
        'ライブビューの応答がありません。数秒後にもう一度お試しください。';
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
    state.liveViewTimeoutId = null;
    scheduleLiveViewRetry('timeout');
  }, 10000);
}

function setPreviewMode(mode, options = {}) {
  const resolvedMode = mode === 'live' ? 'live' : 'screenshot';
  const { forceReload = false } = options;
  state.previewMode = resolvedMode;
  syncPreviewModeUI();

  if (resolvedMode === 'live') {
    initialiseLiveView(forceReload);
  } else {
    clearLiveViewWatchdog();
    clearLiveViewRetry();
    state.liveViewRetryCount = 0;
    disconnectLiveView(true);
    if (liveBrowserContainer) {
      liveBrowserContainer.classList.remove('is-loading', 'is-ready', 'has-error');
    }
    if (liveBrowserUnavailable) {
      liveBrowserUnavailable.textContent =
        'ライブビューは停止中です。ライブビューに切り替えると再接続されます。';
      liveBrowserUnavailable.removeAttribute('aria-busy');
    }
  }
}

function updatePreview(step) {
  let screenshot = null;
  const liveFrame = captureLiveViewFrame();

  if (liveFrame) {
    screenshot = liveFrame;
    step.screenshot = liveFrame;
  } else {
    const fromStep = normaliseScreenshot(step.screenshot);
    if (fromStep) {
      screenshot = fromStep;
    } else if (state.lastPreviewImage) {
      screenshot = state.lastPreviewImage;
    }
  }

  if (screenshot) {
    state.lastPreviewImage = screenshot;
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

  state.latestStep = step;
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
    const warnings = Array.isArray(data.warnings) ? data.warnings : [];
    for (const warning of warnings) {
      const text = typeof warning === 'string' ? warning.trim() : String(warning || '').trim();
      if (!text || state.displayedWarnings.has(text)) {
        continue;
      }
      state.displayedWarnings.add(text);
      appendMessage('system', `⚠️ ${escapeHtml(text)}`);
    }

    const sharedMode =
      typeof data.shared_browser_mode === 'string' ? data.shared_browser_mode.trim().toLowerCase() : 'unknown';
    if (sharedMode === 'local' && state.sharedBrowserMode !== 'local') {
      state.sharedBrowserMode = 'local';
      state.liveViewDisabled = true;
      const message =
        warnings.length && typeof warnings[warnings.length - 1] === 'string'
          ? warnings[warnings.length - 1]
          : 'ライブビューのブラウザに接続できなかったため、スクリーンショットのみ表示されます。';
      state.liveViewDisabledMessage = message;
      if (state.previewMode === 'live') {
        showSharedBrowserError(message);
      }
    } else if (sharedMode === 'remote' && state.sharedBrowserMode !== 'remote') {
      state.sharedBrowserMode = 'remote';
      state.liveViewDisabled = false;
      state.liveViewDisabledMessage = '';
      if (state.previewMode === 'live') {
        initialiseLiveView(true);
      }
    }

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
    const failureMessage =
      typeof error === 'string' && error.trim().length
        ? error
        : '原因不明のエラー';
    appendMessage('system', `❌ 実行に失敗しました: ${escapeHtml(failureMessage)}`);
    if (failureMessage.includes('ライブビューのブラウザに接続できないため実行できません')) {
      showSharedBrowserError(failureMessage);
    }
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
    if (Array.isArray(result.warnings) && result.warnings.length) {
      for (const warning of result.warnings) {
        const text = typeof warning === 'string' ? warning.trim() : String(warning || '').trim();
        if (!text || state.displayedWarnings.has(text)) {
          continue;
        }
        state.displayedWarnings.add(text);
        appendMessage('system', `⚠️ ${escapeHtml(text)}`);
      }
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
  state.displayedWarnings = new Set();
  state.sharedBrowserMode = 'unknown';
  state.liveViewDisabled = false;
  state.liveViewDisabledMessage = '';
  if (state.previewMode === 'live') {
    initialiseLiveView(true);
  }

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
      const error = new Error(data.error || `server returned ${response.status}`);
      if (data && typeof data.code === 'string') {
        error.code = data.code;
      }
      error.status = response.status;
      error.details = data;
      throw error;
    }

    const data = await response.json();
    placeholder.remove();
    state.activeSession = { id: data.session_id };
    pollSession();
  } catch (err) {
    placeholder.remove();
    const message = err && typeof err.message === 'string' ? err.message : String(err);
    if (err && err.code === 'shared_browser_unavailable') {
      appendMessage('system', `❌ ${escapeHtml(message)}`);
      showSharedBrowserError(message);
    } else {
      appendMessage('system', `❌ 実行開始に失敗しました: ${escapeHtml(message)}`);
    }
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
    state.latestStep = null;
    state.lastPreviewImage = null;
    if (previewImage) {
      previewImage.style.display = 'none';
      previewImage.removeAttribute('src');
    }
    if (previewPlaceholder) {
      previewPlaceholder.style.display = 'block';
    }
  } catch (err) {
    appendMessage('system', `⚠️ リセットに失敗しました: ${escapeHtml(err.message || String(err))}`);
  }
}

previewModeButtons.forEach((button) => {
  button.addEventListener('click', () => {
    const desiredMode = button.dataset.previewMode;
    const alreadyLive = state.previewMode === 'live';
    const wantsLive = desiredMode === 'live';
    setPreviewMode(desiredMode, {
      forceReload: alreadyLive && wantsLive,
    });
  });
});
setPreviewMode('live');

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
