const {
  DEFAULT_BRIDGE_BASE,
  DEFAULT_TIMEOUT_MS,
  normalizeBridgeBase,
  migrateLegacyBridgeBase,
  buildBridgeCandidates,
  withTimeout,
  formatCheckedAt,
  describeBridgeFailure,
  buildBridgeHistoryEntry,
} = globalThis.BridgeUtils || {};
const { createBridgeAuthClient } = globalThis.BridgeAuth || {};

const BRIDGE_HISTORY_STORAGE_KEY = 'bridgeStatusHistory';
const BRIDGE_HISTORY_LIMIT = 8;
const LATEST_SUMMARY_STORAGE_KEY = 'latestBrowserSummary';

const statusEl = document.getElementById('options-status');
const lastCheckEl = document.getElementById('options-last-check');
const bridgeBaseEl = document.getElementById('options-bridge-base');
const helpEl = document.getElementById('options-help');
const historyListEl = document.getElementById('history-list');
const resultUpdatedAtEl = document.getElementById('result-updated-at');
const resultSummaryEl = document.getElementById('result-summary');
const resultKeyPointsEl = document.getElementById('result-key-points');
const resultActionHintsEl = document.getElementById('result-action-hints');

function getStorageArea() {
  return globalThis.chrome?.storage?.local || null;
}

async function readLocalStorage(keys) {
  const storage = getStorageArea();
  if (!storage) {
    return {};
  }
  return storage.get(keys);
}

async function writeLocalStorage(payload) {
  const storage = getStorageArea();
  if (!storage) {
    return;
  }
  await storage.set(payload);
}

const bridgeAuthClient = createBridgeAuthClient({
  readStorage: readLocalStorage,
  writeStorage: writeLocalStorage,
  fetchImpl: (...args) => fetch(...args),
  buildBridgeCandidates,
  normalizeBridgeBase,
  withTimeout,
  timeoutMs: DEFAULT_TIMEOUT_MS,
});

function setStatus(text, tone = 'ok') {
  statusEl.textContent = text;
  statusEl.dataset.tone = tone;
}

function renderHistory(records) {
  historyListEl.innerHTML = '';
  const values = Array.isArray(records) ? records.slice(0, BRIDGE_HISTORY_LIMIT) : [];
  if (!values.length) {
    const li = document.createElement('li');
    li.textContent = '还没有连接记录。';
    historyListEl.appendChild(li);
    return;
  }
  for (const item of values) {
    const li = document.createElement('li');
    li.textContent = `${item.statusText || '状态未知'} | ${item.base || DEFAULT_BRIDGE_BASE} | ${formatCheckedAt(item.checkedAt)}`;
    historyListEl.appendChild(li);
  }
}

function setList(element, items, emptyText) {
  if (!element) {
    return;
  }
  element.innerHTML = '';
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) {
    const li = document.createElement('li');
    li.textContent = emptyText;
    element.appendChild(li);
    return;
  }
  for (const item of values) {
    const li = document.createElement('li');
    li.textContent = String(item);
    element.appendChild(li);
  }
}

function renderLatestSummary(payload) {
  const data = payload && typeof payload === 'object' ? payload : {};
  const summary = String(data.summary || '').trim();
  const updatedAt = String(data.updatedAt || '').trim();
  if (resultUpdatedAtEl) {
    resultUpdatedAtEl.textContent = updatedAt ? `最近更新：${formatCheckedAt(updatedAt)}` : '尚未生成总结。';
  }
  if (resultSummaryEl) {
    resultSummaryEl.textContent = summary || '暂无摘要';
  }
  setList(resultKeyPointsEl, data.keyPoints, '暂无关键点');
  setList(resultActionHintsEl, data.actionHints, '暂无动作建议');
}

async function loadSettings() {
  const stored = await readLocalStorage(['bridgeBase', BRIDGE_HISTORY_STORAGE_KEY, 'bridgeLastStatus', LATEST_SUMMARY_STORAGE_KEY]);
  const { migrated, base } = migrateLegacyBridgeBase(stored.bridgeBase || DEFAULT_BRIDGE_BASE);
  if (migrated) {
    await writeLocalStorage({ bridgeBase: base });
  }
  bridgeBaseEl.value = base;
  renderHistory(stored[BRIDGE_HISTORY_STORAGE_KEY]);
  renderLatestSummary(stored[LATEST_SUMMARY_STORAGE_KEY]);
  if (stored.bridgeLastStatus) {
    setStatus(stored.bridgeLastStatus.statusText || '尚未检查', stored.bridgeLastStatus.ok ? 'ok' : 'warning');
    lastCheckEl.textContent = `最近检查：${formatCheckedAt(stored.bridgeLastStatus.checkedAt)}`;
    helpEl.textContent = stored.bridgeLastStatus.helpText || helpEl.textContent;
  }
  // 发布态后端要求扩展 ID 在受信白名单内，这里直接展示本扩展 ID 便于复制。
  const extensionId = String(globalThis.chrome?.runtime?.id || '').trim();
  if (extensionId) {
    helpEl.textContent = `${helpEl.textContent}（本扩展 ID：${extensionId}）`;
  }
}

async function persistHistory(entry) {
  const stored = await readLocalStorage([BRIDGE_HISTORY_STORAGE_KEY]);
  const nextHistory = [entry, ...(Array.isArray(stored[BRIDGE_HISTORY_STORAGE_KEY]) ? stored[BRIDGE_HISTORY_STORAGE_KEY] : [])].slice(0, BRIDGE_HISTORY_LIMIT);
  await writeLocalStorage({
    [BRIDGE_HISTORY_STORAGE_KEY]: nextHistory,
    bridgeLastStatus: entry,
  });
  renderHistory(nextHistory);
}

async function testConnection() {
  const requestedBase = normalizeBridgeBase(bridgeBaseEl.value || DEFAULT_BRIDGE_BASE);
  let lastError = null;
  try {
    const { json, base } = await bridgeAuthClient.request('/api/browser-assistant/diagnostics', { requestedBase });
    const checkedAt = new Date().toISOString();
    const statusText = json.bridge?.running ? `桌面端已连接 ${json.bridge.base_url || base}` : '桌面端未就绪';
    setStatus(statusText, json.bridge?.running ? 'ok' : 'warning');
    lastCheckEl.textContent = `最近检查：${formatCheckedAt(checkedAt)}`;
    helpEl.textContent = json.bridge?.running
      ? `桥接地址可用。当前实际连通地址：${base}${base !== requestedBase ? '，建议保存该地址。' : ''}`
      : '桌面端已响应，但桥接尚未进入可用状态。';
    await persistHistory(buildBridgeHistoryEntry({ ok: Boolean(json.bridge?.running), base, statusText, helpText: helpEl.textContent, checkedAt }));
    return;
  } catch (error) {
    lastError = error;
  }
  const checkedAt = new Date().toISOString();
  const details = describeBridgeFailure(lastError?.message, requestedBase, globalThis.chrome?.runtime?.id);
  setStatus(details.statusText, details.tone);
  lastCheckEl.textContent = `最近检查：${formatCheckedAt(checkedAt)}`;
  helpEl.textContent = details.helpText;
  await persistHistory(buildBridgeHistoryEntry({ ok: false, base: requestedBase, statusText: details.statusText, helpText: details.helpText, checkedAt }));
}

async function bindActions() {
  document.getElementById('test-connection').addEventListener('click', async () => {
    await testConnection();
  });
  document.getElementById('save-options-bridge').addEventListener('click', async () => {
    const normalized = normalizeBridgeBase(bridgeBaseEl.value || DEFAULT_BRIDGE_BASE);
    bridgeBaseEl.value = normalized;
    await writeLocalStorage({ bridgeBase: normalized });
    await bridgeAuthClient.clearSession();
    helpEl.textContent = '桥接地址已保存。建议立即执行一次连接测试。';
  });
  document.getElementById('reset-options-bridge').addEventListener('click', async () => {
    bridgeBaseEl.value = DEFAULT_BRIDGE_BASE;
    await writeLocalStorage({ bridgeBase: DEFAULT_BRIDGE_BASE });
    await bridgeAuthClient.clearSession();
    helpEl.textContent = '已恢复默认桥接地址。';
  });
}

(async function bootstrap() {
  await loadSettings();
  await bindActions();
})();