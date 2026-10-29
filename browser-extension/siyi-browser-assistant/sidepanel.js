const {
  DEFAULT_BRIDGE_BASE,
  DEFAULT_TIMEOUT_MS,
  normalizeBridgeBase,
  migrateLegacyBridgeBase,
  buildBridgeCandidates,
  describeBridgeFailure,
  buildBridgeHistoryEntry,
  formatCheckedAt,
} = globalThis.BridgeUtils || {};
const { createBridgeAuthClient } = globalThis.BridgeAuth || {};

const BRIDGE_HISTORY_STORAGE_KEY = 'bridgeStatusHistory';
const BRIDGE_HISTORY_LIMIT = 8;
const LATEST_SUMMARY_STORAGE_KEY = 'latestBrowserSummary';
const doc = typeof document !== 'undefined' ? document : null;

const state = {
  bridgeBase: DEFAULT_BRIDGE_BASE,
  currentContext: null,
  lastSummary: null,
  bridgeHistory: [],
  lastResolvedBase: '',
  currentTabUrl: '',
  chatMessages: [],
  // result-view 状态机
  activeResultView: 'summary',        // 'summary' | 'subtitle'
  availableResultViews: ['summary'],   // 当前可用的 tab 集合
  resultViews: null,                   // 后端返回的 resultViews 字段
};

const bridgeStatusEl = doc ? doc.getElementById('bridge-status') : null;
const debugPanelEl = doc ? doc.getElementById('debug-panel') : null;
const debugSubtitleStatusEl = doc ? doc.getElementById('debug-subtitle-status') : null;
const debugContentConfidenceEl = doc ? doc.getElementById('debug-content-confidence') : null;
const debugNoiseScoreEl = doc ? doc.getElementById('debug-noise-score') : null;
const debugRootHintEl = doc ? doc.getElementById('debug-root-hint') : null;
const debugDescriptionLengthEl = doc ? doc.getElementById('debug-description-length') : null;
const debugSubtitleLengthEl = doc ? doc.getElementById('debug-subtitle-length') : null;
const debugPageLengthEl = doc ? doc.getElementById('debug-page-length') : null;
const debugSummaryModeEl = doc ? doc.getElementById('debug-summary-mode') : null;
const debugFetchMethodEl = doc ? doc.getElementById('debug-fetch-method') : null;
const debugFetchErrorEl = doc ? doc.getElementById('debug-fetch-error') : null;
const debugSubtitleLanEl = doc ? doc.getElementById('debug-subtitle-lan') : null;
const debugWarningEl = doc ? doc.getElementById('debug-warning') : null;
const pageMetaEl = doc ? doc.getElementById('page-meta') : null;
const summaryOutputEl = doc ? doc.getElementById('summary-output') : null;
const keyPointsEl = doc ? doc.getElementById('key-points') : null;
const actionHintsEl = doc ? doc.getElementById('action-hints') : null;
const contextSuggestionsEl = doc ? doc.getElementById('context-suggestions') : null;
const recommendedActionsEl = doc ? doc.getElementById('recommended-actions') : null;
const contextKindPillEl = doc ? doc.getElementById('context-kind-pill') : null;
const contextModePillEl = doc ? doc.getElementById('context-mode-pill') : null;
const contextTopActionEl = doc ? doc.getElementById('context-top-action') : null;
const contextChipsEl = doc ? doc.getElementById('context-chips') : null;
const contextSignalsEl = doc ? doc.getElementById('context-signals') : null;
const bridgeBaseInputEl = doc ? doc.getElementById('bridge-base-input') : null;
const bridgeHelpEl = doc ? doc.getElementById('bridge-help') : null;
const bridgeLastCheckEl = doc ? doc.getElementById('bridge-last-check') : null;
const workspaceOverviewEl = doc ? doc.getElementById('workspace-overview') : null;
const bridgeTipsEl = doc ? doc.getElementById('bridge-tips') : null;
const bridgeHistoryEl = doc ? doc.getElementById('bridge-history') : null;
const summarizeSelectionBtnEl = doc ? doc.getElementById('summarize-selection') : null;
const summarizePageBtnEl = doc ? doc.getElementById('summarize-page') : null;
const capturePageBtnEl = doc ? doc.getElementById('capture-page') : null;
const saveReadingBtnEl = doc ? doc.getElementById('save-reading') : null;
const saveBookmarkBtnEl = doc ? doc.getElementById('save-bookmark') : null;
const createNoteBtnEl = doc ? doc.getElementById('create-note') : null;
const createTaskBtnEl = doc ? doc.getElementById('create-task') : null;
const importKimiBtnEl = doc ? doc.getElementById('import-kimi') : null;
const importDeepSeekBtnEl = doc ? doc.getElementById('import-deepseek') : null;
const tabButtons = doc ? Array.from(doc.querySelectorAll('.tab-btn')) : [];
const tabPanels = doc ? Array.from(doc.querySelectorAll('.tab-panel')) : [];
const chatMessagesEl = doc ? doc.getElementById('chat-messages') : null;
const chatSuggestionsEl = doc ? doc.getElementById('chat-suggestions') : null;
const chatInputEl = doc ? doc.getElementById('chat-input') : null;
const chatSendBtnEl = doc ? doc.getElementById('chat-send') : null;
const panelCloseBtnEl = doc ? doc.getElementById('panel-close') : null;

// ===== 结果展示区 DOM =====
const resultViewEl = doc ? doc.getElementById('result-view') : null;
const resultViewSwitcherEl = doc ? doc.getElementById('result-view-switcher') : null;
const rvSummaryPanelEl = doc ? doc.getElementById('rv-summary-panel') : null;
const rvSubtitlePanelEl = doc ? doc.getElementById('rv-subtitle-panel') : null;
const rvSummaryConclusionEl = doc ? doc.getElementById('rv-summary-conclusion') : null;
const rvSummaryPointsEl = doc ? doc.getElementById('rv-summary-points') : null;
const rvSummaryNextEl = doc ? doc.getElementById('rv-summary-next') : null;
const rvSourceLabelEl = doc ? doc.getElementById('rv-source-label') : null;
const rvSummaryTypePillEl = doc ? doc.getElementById('rv-summary-type-pill') : null;
const rvSubtitleStatusPillEl = doc ? doc.getElementById('rv-subtitle-status-pill') : null;
const rvTextLengthPillEl = doc ? doc.getElementById('rv-text-length-pill') : null;
const subtitleMetaSourceEl = doc ? doc.getElementById('subtitle-meta-source') : null;
const subtitleMetaLangEl = doc ? doc.getElementById('subtitle-meta-lang') : null;
const subtitleMetaLengthEl = doc ? doc.getElementById('subtitle-meta-length') : null;
const subtitleCopyBtnEl = doc ? doc.getElementById('subtitle-copy-btn') : null;
const rvSubtitleTextEl = doc ? doc.getElementById('rv-subtitle-text') : null;

function humanizeContentKind(contentKind) {
  const mapping = {
    article: '网页文章',
    video_page: '视频页',
    document_page: '文档页',
    image_page: '图片页',
    mixed_page: '混合页',
    news_page: '资讯页'
  };
  return mapping[String(contentKind || '').trim()] || '网页文章';
}

function humanizeSummaryMode(summaryMode) {
  const mapping = {
    general_focus: '通用总结',
    video_focus: '视频重点',
    document_focus: '文档重点',
    news_focus: '资讯重点'
  };
  return mapping[String(summaryMode || '').trim()] || '未总结';
}

function renderDebugPanel(context, result = null) {
  const diagnostics = context?.extractionDiagnostics || {};
  const meta = result?.meta || {};
  const subtitleStatus = String(meta.subtitleStatus || context?.subtitleStatus || context?.mediaHints?.subtitleStatus || '未读取');
  const contentConfidence = meta.contentConfidence ?? diagnostics.contentConfidence ?? '-';
  const noiseScore = meta.noiseScore ?? diagnostics.noiseScore ?? '-';
  const rootHint = meta.rootHint || context?.rootHint || '-';
  const descriptionLength = meta.descriptionLength ?? diagnostics.descriptionLength ?? String(context?.descriptionText || '').trim().length;
  const subtitleLength = meta.subtitleLength ?? diagnostics.subtitleLength ?? String(context?.subtitleText || '').trim().length;
  const pageLength = meta.contentLength ?? diagnostics.pageTextLength ?? String(context?.pageText || '').trim().length;
  const summaryMode = humanizeSummaryMode(meta.summaryMode);
  const warnings = Array.isArray(meta.warnings) ? meta.warnings.filter(Boolean) : [];

  if (debugSubtitleStatusEl) {
    debugSubtitleStatusEl.textContent = subtitleStatus;
  }
  if (debugContentConfidenceEl) {
    debugContentConfidenceEl.textContent = String(contentConfidence);
  }
  if (debugNoiseScoreEl) {
    debugNoiseScoreEl.textContent = String(noiseScore);
  }
  if (debugRootHintEl) {
    debugRootHintEl.textContent = String(rootHint);
  }
  if (debugDescriptionLengthEl) {
    debugDescriptionLengthEl.textContent = String(descriptionLength);
  }
  if (debugSubtitleLengthEl) {
    debugSubtitleLengthEl.textContent = String(subtitleLength);
  }
  if (debugPageLengthEl) {
    debugPageLengthEl.textContent = String(pageLength);
  }
  if (debugSummaryModeEl) {
    debugSummaryModeEl.textContent = summaryMode;
  }
  const fetchMethod = diagnostics.subtitleFetchMethod || meta.subtitleFetchMethod || '-';
  const fetchError = diagnostics.subtitleFetchError || meta.subtitleFetchError || '';
  const subtitleLan = diagnostics.subtitleLan || meta.subtitleLan || '-';
  if (debugFetchMethodEl) {
    debugFetchMethodEl.textContent = fetchMethod;
  }
  if (debugFetchErrorEl) {
    debugFetchErrorEl.textContent = fetchError || '无';
  }
  if (debugSubtitleLanEl) {
    debugSubtitleLanEl.textContent = subtitleLan;
  }
  if (debugWarningEl) {
    if (!context) {
      debugWarningEl.textContent = '尚未读取页面上下文。';
    } else if (warnings.length) {
      debugWarningEl.textContent = warnings.slice(0, 2).join(' / ');
    } else {
      debugWarningEl.textContent = '当前未发现明显异常信号。';
    }
  }
  if (debugPanelEl && result?.meta?.degradedVideoTextSignal) {
    debugPanelEl.open = true;
  }
}

function renderContextProfile(context, result = null) {
  const contentKind = humanizeContentKind(context?.contentKind);
  const summaryMode = humanizeSummaryMode(result?.meta?.summaryMode);
  const topAction = result?.topRecommendedAction || selectTopRecommendedAction(result?.recommendedActions || []);
  const diagnostics = context?.extractionDiagnostics || {};
  const subtitleStatus = String(context?.subtitleStatus || context?.mediaHints?.subtitleStatus || 'absent');
  const chips = [
    `适配器 ${context?.siteAdapter || 'generic'}`,
    `提取 ${context?.extractionMode || 'unknown'}`,
    `主节点 ${context?.rootHint || 'body'}`,
    `置信 ${diagnostics.contentConfidence ?? '-'}`,
    `噪声 ${diagnostics.noiseScore ?? '-'}`,
  ];
  const mediaHints = context?.mediaHints || {};
  const signals = [
    ['视频信号', mediaHints.hasVideo ? '已命中' : '未命中'],
    ['字幕信号', mediaHints.hasSubtitle ? '已有文本' : '无文本'],
    ['字幕状态', subtitleStatus],
    ['文档容器', mediaHints.hasDocumentContainer ? '已命中' : '未命中'],
    ['图片密度', mediaHints.hasImageCluster ? '较高' : '常规'],
    ['站点标记', mediaHints.site || context?.siteAdapter || 'generic'],
    ['简介长度', `${diagnostics.descriptionLength ?? 0}`],
    ['字幕长度', `${diagnostics.subtitleLength ?? 0}`],
  ];

  if (contextKindPillEl) {
    contextKindPillEl.textContent = contentKind;
  }
  if (contextModePillEl) {
    contextModePillEl.textContent = summaryMode;
  }
  if (contextTopActionEl) {
    if (topAction && topAction.label) {
      const enabled = String(topAction.enabled || 'true').trim().toLowerCase() !== 'false';
      const reason = String(topAction.reason || '').trim();
      contextTopActionEl.textContent = enabled
        ? `当前最建议动作：${topAction.label}${reason ? `。${reason}` : ''}`
        : `当前首选动作已切换：${topAction.label}${reason ? `。${reason}` : ''}`;
    } else {
      contextTopActionEl.textContent = '当前还没有首选动作。';
    }
  }
  if (contextChipsEl) {
    contextChipsEl.innerHTML = '';
    for (const chip of chips) {
      const span = document.createElement('span');
      span.className = 'chip';
      span.textContent = chip;
      contextChipsEl.appendChild(span);
    }
  }
  if (contextSignalsEl) {
    contextSignalsEl.innerHTML = '';
    for (const [label, value] of signals) {
      const card = document.createElement('div');
      card.className = 'signal-card';
      const strong = document.createElement('strong');
      strong.textContent = label;
      const span = document.createElement('span');
      span.textContent = value;
      card.appendChild(strong);
      card.appendChild(span);
      contextSignalsEl.appendChild(card);
    }
  }
  renderDebugPanel(context, result);
}

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
  withTimeout: globalThis.BridgeUtils.withTimeout,
  timeoutMs: DEFAULT_TIMEOUT_MS,
});

function setBridgeStatus(text, tone = 'ok') {
  if (!bridgeStatusEl) {
    return;
  }
  bridgeStatusEl.textContent = text;
  bridgeStatusEl.dataset.tone = tone;
}

function hasReadableContext() {
  return Boolean(state.currentContext && state.currentContext.url && state.currentContext.pageText);
}

function hasSummaryResult() {
  return Boolean(state.lastSummary && (state.lastSummary.summary || '').trim());
}

function setButtonDisabled(button, disabled, title = '') {
  if (!button) {
    return;
  }
  button.disabled = Boolean(disabled);
  button.title = title || '';
}

function appendChatMessage(role, text) {
  if (!chatMessagesEl) {
    return;
  }
  const normalized = String(text || '').trim();
  if (!normalized) {
    return;
  }
  state.chatMessages.push({ role, text: normalized, createdAt: new Date().toISOString() });
  state.chatMessages = state.chatMessages.slice(-18);
  chatMessagesEl.innerHTML = '';
  for (const item of state.chatMessages) {
    const row = document.createElement('div');
    const isUser = item.role === 'user';
    row.className = `chat-row ${isUser ? 'user' : 'assistant'}`;

    const avatar = document.createElement('div');
    avatar.className = `chat-avatar ${isUser ? 'user' : 'assistant'}`;
    avatar.textContent = isUser ? '我' : '助';

    const bubble = document.createElement('div');
    bubble.className = `chat-bubble ${isUser ? 'user' : 'assistant'}`;
    bubble.textContent = item.text;

    row.appendChild(avatar);
    row.appendChild(bubble);
    chatMessagesEl.appendChild(row);
  }
  chatMessagesEl.scrollTop = chatMessagesEl.scrollHeight;
}

function getDefaultChatSuggestions() {
  return [
    { label: '总结当前页面', action: 'summarize_page' },
    { label: '总结选中内容', action: 'summarize_selection' },
    { label: '存到收件箱', action: 'capture' },
  ];
}

function renderChatSuggestions(items) {
  if (!chatSuggestionsEl) {
    return;
  }
  chatSuggestionsEl.innerHTML = '';
  const values = Array.isArray(items) ? items.filter(Boolean).slice(0, 4) : [];
  if (!values.length) {
    return;
  }
  for (const item of values) {
    const button = document.createElement('button');
    button.className = 'chat-suggestion-btn';
    button.textContent = item.label || '建议动作';
    button.addEventListener('click', async () => {
      await executeSuggestedAction(item.action);
    });
    chatSuggestionsEl.appendChild(button);
  }
}

function switchResultTab(tabKey) {
  for (const button of tabButtons) {
    const isActive = button.dataset.tabTarget === tabKey;
    button.classList.toggle('active', isActive);
    button.setAttribute('aria-selected', isActive ? 'true' : 'false');
  }
  for (const panel of tabPanels) {
    panel.classList.toggle('active', panel.dataset.tabPanel === tabKey);
  }
}

// ===== 结果展示区 视图切换 =====
function switchResultView(viewKey) {
  if (!state.availableResultViews.includes(viewKey)) {
    viewKey = state.availableResultViews[0] || 'summary';
  }
  state.activeResultView = viewKey;
  // 更新 switcher tab 高亮
  if (resultViewSwitcherEl) {
    for (const btn of resultViewSwitcherEl.querySelectorAll('.rv-tab')) {
      btn.classList.toggle('active', btn.dataset.rv === viewKey);
    }
  }
  // 切换面板可见性
  if (rvSummaryPanelEl) {
    rvSummaryPanelEl.hidden = viewKey !== 'summary';
  }
  if (rvSubtitlePanelEl) {
    rvSubtitlePanelEl.hidden = viewKey !== 'subtitle';
  }
}

function showResultView(show) {
  if (resultViewEl) {
    if (show) {
      resultViewEl.removeAttribute('hidden');
    } else {
      resultViewEl.setAttribute('hidden', '');
    }
  }
}

function updateResultViewSwitcher() {
  if (!resultViewSwitcherEl) {
    return;
  }
  const hasSubtitle = state.availableResultViews.includes('subtitle');
  // 视频页始终展示 switcher (即使无字幕)
  resultViewSwitcherEl.style.display = hasSubtitle ? 'flex' : 'none';
}

function renderResultSummaryCard(result) {
  const rv = result?.resultViews?.summary || {};
  const conclusion = String(rv.conclusion || result?.summary || '').trim();
  const points = Array.isArray(rv.keyPoints) && rv.keyPoints.length
    ? rv.keyPoints.filter(Boolean)
    : (Array.isArray(result?.keyPoints) ? result.keyPoints.filter(Boolean) : []);
  const next = String(rv.nextStep || '').trim();
  const sourceLabel = String(rv.sourceLabel || result?.meta?.summarySource || '本地提炼').trim();

  if (rvSummaryConclusionEl) {
    rvSummaryConclusionEl.textContent = conclusion || '暂无摘要内容，请稍候或重试。';
  }
  if (rvSummaryPointsEl) {
    rvSummaryPointsEl.innerHTML = '';
    if (points.length) {
      for (const pt of points.slice(0, 4)) {
        const li = document.createElement('li');
        li.textContent = pt;
        rvSummaryPointsEl.appendChild(li);
      }
    }
  }
  if (rvSummaryNextEl) {
    rvSummaryNextEl.textContent = next ? `💡 下一步：${next}` : '';
  }
  if (rvSourceLabelEl) {
    rvSourceLabelEl.textContent = sourceLabel;
  }
  const summaryTypeLabel = String(rv.summaryTypeLabel || result?.meta?.summaryTypeLabel || '').trim();
  if (rvSummaryTypePillEl) {
    if (summaryTypeLabel) {
      rvSummaryTypePillEl.textContent = summaryTypeLabel;
      rvSummaryTypePillEl.removeAttribute('hidden');
    } else {
      rvSummaryTypePillEl.setAttribute('hidden', '');
    }
  }

  // 证据层：字幕状态 + 文本长度
  const subtitleRv = result?.resultViews?.subtitle || {};
  const subtitleStatusLabel = subtitleRv.statusLabel || subtitleRv.status || '';
  if (rvSubtitleStatusPillEl) {
    if (subtitleStatusLabel) {
      rvSubtitleStatusPillEl.textContent = `字幕: ${subtitleStatusLabel}`;
      rvSubtitleStatusPillEl.removeAttribute('hidden');
    } else {
      rvSubtitleStatusPillEl.setAttribute('hidden', '');
    }
  }
  const nlpDebug = rv.nlpDebug || {};
  if (rvTextLengthPillEl) {
    const textLen = nlpDebug.textLength || result?.meta?.contentLength || 0;
    if (textLen > 0) {
      rvTextLengthPillEl.textContent = `${textLen} 字`;
      rvTextLengthPillEl.removeAttribute('hidden');
    } else {
      rvTextLengthPillEl.setAttribute('hidden', '');
    }
  }
}

function renderResultSubtitleView(result) {
  const rv = result?.resultViews?.subtitle || {};
  const subtitleText = String(rv.text || '').trim();
  const segments = Array.isArray(rv.segments) ? rv.segments : [];
  const isAvailable = rv.available !== false && Boolean(subtitleText);

  if (!isAvailable) {
    // 无字幕空态
    const hint = String(rv.hint || '此视频暂无CC字幕').trim();
    if (subtitleMetaSourceEl) { subtitleMetaSourceEl.textContent = ''; }
    if (subtitleMetaLangEl) { subtitleMetaLangEl.textContent = ''; }
    if (subtitleMetaLengthEl) { subtitleMetaLengthEl.textContent = ''; }
    if (subtitleCopyBtnEl) { subtitleCopyBtnEl.style.display = 'none'; }
    if (rvSubtitleTextEl) {
      rvSubtitleTextEl.innerHTML = '';
      const emptyDiv = document.createElement('div');
      emptyDiv.className = 'rv-subtitle-empty';
      emptyDiv.textContent = hint;
      const pasteBtn = document.createElement('button');
      pasteBtn.className = 'empty-action';
      pasteBtn.textContent = '粘贴字幕文本';
      pasteBtn.addEventListener('click', () => {
        executeSuggestedAction('paste_transcript');
      });
      emptyDiv.appendChild(document.createElement('br'));
      emptyDiv.appendChild(pasteBtn);
      rvSubtitleTextEl.appendChild(emptyDiv);
    }
    return;
  }

  // 有字幕
  const source = String(rv.source || '自动提取').trim();
  const lang = String(rv.language || '').trim();
  const length = rv.length != null ? rv.length : subtitleText.length;

  if (subtitleMetaSourceEl) {
    subtitleMetaSourceEl.textContent = `来源: ${source}`;
  }
  if (subtitleMetaLangEl) {
    subtitleMetaLangEl.textContent = lang ? `语言: ${lang}` : '';
  }
  if (subtitleMetaLengthEl) {
    subtitleMetaLengthEl.textContent = `${length} 字`;
  }
  if (subtitleCopyBtnEl) {
    subtitleCopyBtnEl.style.display = '';
  }

  if (rvSubtitleTextEl) {
    rvSubtitleTextEl.innerHTML = '';
    if (segments.length > 0) {
      // 时间戳分段列表渲染
      const list = document.createElement('div');
      list.className = 'subtitle-segment-list';
      for (const seg of segments) {
        const row = document.createElement('div');
        row.className = 'subtitle-segment-row';
        if (seg.time) {
          const timeSpan = document.createElement('span');
          timeSpan.className = 'subtitle-segment-time';
          timeSpan.textContent = seg.time;
          row.appendChild(timeSpan);
        }
        const textSpan = document.createElement('span');
        textSpan.className = 'subtitle-segment-text';
        textSpan.textContent = String(seg.text || '').trim();
        row.appendChild(textSpan);
        list.appendChild(row);
      }
      rvSubtitleTextEl.appendChild(list);
    } else {
      // 回退：纯文本显示
      rvSubtitleTextEl.textContent = subtitleText;
    }
  }
}

function updateActionAvailability(reason = '') {
  const readableContext = hasReadableContext();
  const summaryReady = hasSummaryResult();
  const restrictedReason = reason || '当前页面不允许注入扩展脚本，请切换到普通网页后重试。';
  const actionReason = readableContext ? '' : restrictedReason;
  const summaryReason = readableContext ? '' : '请先切换到普通网页，再执行总结。';

  setButtonDisabled(summarizeSelectionBtnEl, false, summaryReason);
  setButtonDisabled(summarizePageBtnEl, false, summaryReason);
  setButtonDisabled(capturePageBtnEl, !readableContext, actionReason);
  setButtonDisabled(saveReadingBtnEl, !readableContext, actionReason);
  setButtonDisabled(saveBookmarkBtnEl, !readableContext, actionReason);
  setButtonDisabled(createNoteBtnEl, !readableContext, actionReason);
  setButtonDisabled(createTaskBtnEl, !readableContext, actionReason);

  const isKimi = state.currentTabUrl.includes('kimi.com');
  if (importKimiBtnEl) {
    importKimiBtnEl.hidden = !isKimi;
    importKimiBtnEl.title = isKimi ? '导入当前 Kimi 对话到思忆集' : '导入 Kimi 对话';
  }

  const isDeepSeek = state.currentTabUrl.includes('chat.deepseek.com');
  if (importDeepSeekBtnEl) {
    importDeepSeekBtnEl.hidden = !isDeepSeek;
    importDeepSeekBtnEl.title = isDeepSeek ? '导入当前 DeepSeek 对话到思忆集' : '导入 DeepSeek 对话';
  }

  if (readableContext && !summaryReady) {
    setButtonDisabled(createNoteBtnEl, false, '可直接基于页面内容创建笔记。');
    setButtonDisabled(createTaskBtnEl, false, '可直接基于页面内容创建任务。');
    setButtonDisabled(capturePageBtnEl, false, '可直接把当前页面存到收件箱。');
    setButtonDisabled(saveReadingBtnEl, false, '可直接把当前页面保存到阅读列表。');
    setButtonDisabled(saveBookmarkBtnEl, false, '可直接把当前页面加入书签。');
  }
}

async function getBridgeBase() {
  const stored = await readLocalStorage(['bridgeBase']);
  const { migrated, base } = migrateLegacyBridgeBase(stored.bridgeBase || DEFAULT_BRIDGE_BASE);
  if (migrated) {
    await writeLocalStorage({ bridgeBase: base });
  }
  state.bridgeBase = base;
  return state.bridgeBase;
}

async function saveBridgeBase(rawValue) {
  const normalized = normalizeBridgeBase(rawValue);
  state.bridgeBase = normalized;
  await writeLocalStorage({ bridgeBase: normalized });
  if (bridgeBaseInputEl) {
    bridgeBaseInputEl.value = normalized;
  }
  return normalized;
}

function setBridgeLastCheckText(text) {
  if (!bridgeLastCheckEl) {
    return;
  }
  bridgeLastCheckEl.textContent = text;
}

function renderBridgeHistory(records) {
  if (!bridgeHistoryEl) {
    return;
  }
  bridgeHistoryEl.innerHTML = '';
  const values = Array.isArray(records) ? records.slice(0, 3) : [];
  if (!values.length) {
    const li = document.createElement('li');
    li.textContent = '还没有连接记录。';
    bridgeHistoryEl.appendChild(li);
    return;
  }
  for (const item of values) {
    const li = document.createElement('li');
    li.textContent = `${item.statusText || '状态未知'} | ${item.base || state.bridgeBase} | ${formatCheckedAt(item.checkedAt)}`;
    bridgeHistoryEl.appendChild(li);
  }
}

async function loadBridgeState() {
  const stored = await readLocalStorage(['bridgeBase', BRIDGE_HISTORY_STORAGE_KEY]);
  const { migrated, base } = migrateLegacyBridgeBase(stored.bridgeBase || DEFAULT_BRIDGE_BASE);
  if (migrated) {
    await writeLocalStorage({ bridgeBase: base });
  }
  state.bridgeBase = base;
  state.bridgeHistory = Array.isArray(stored[BRIDGE_HISTORY_STORAGE_KEY]) ? stored[BRIDGE_HISTORY_STORAGE_KEY] : [];
  renderBridgeHistory(state.bridgeHistory);
  if (state.bridgeHistory[0]) {
    setBridgeLastCheckText(`最近检查：${formatCheckedAt(state.bridgeHistory[0].checkedAt)}`);
  }
  return state.bridgeBase;
}

async function persistBridgeHistory(entry) {
  state.bridgeHistory = [entry, ...state.bridgeHistory].slice(0, BRIDGE_HISTORY_LIMIT);
  renderBridgeHistory(state.bridgeHistory);
  await bridgeAuthClient.clearSession();
  await writeLocalStorage({
    [BRIDGE_HISTORY_STORAGE_KEY]: state.bridgeHistory,
    bridgeLastStatus: entry,
  });
}

async function requestBridge(path, { method = 'GET', payload = null } = {}) {
  const requestedBase = await getBridgeBase();
  let lastError = null;
  try {
    const result = await bridgeAuthClient.request(path, { method, payload, requestedBase });
    state.lastResolvedBase = result.base;
    return result;
  } catch (error) {
    lastError = error;
  }
  const details = describeBridgeFailure(lastError?.message || 'unknown error', requestedBase);
  const enhanced = new Error(details.helpText);
  enhanced.tone = details.tone;
  enhanced.statusText = details.statusText;
  enhanced.base = requestedBase;
  throw enhanced;
}

async function getActivePageContext() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error('未找到当前标签页');
  }
  state.currentTabUrl = String(tab.url || tab.pendingUrl || '');
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content-script.js'],
    });
  } catch (error) {
    throw new Error('当前页面不允许注入扩展脚本，可能是浏览器内置页或受限页面。');
  }
  let response = await chrome.tabs.sendMessage(tab.id, { type: 'COLLECT_PAGE_CONTEXT' });
  if (!response || !response.ok) {
    throw new Error('当前页面无法读取上下文，可能是浏览器内置页。');
  }
  const shouldRetryVideoContext = Boolean(
    response.context?.contentKind === 'video_page'
    && !String(response.context?.subtitleText || '').trim()
  );
  if (shouldRetryVideoContext) {
    const retryDelays = [1500, 3000];
    for (const delay of retryDelays) {
      if (String(response.context?.subtitleText || '').trim()) {
        break;
      }
      await new Promise((resolve) => globalThis.setTimeout(resolve, delay));
      const retryResponse = await chrome.tabs.sendMessage(tab.id, { type: 'COLLECT_PAGE_CONTEXT' }).catch(() => null);
      if (retryResponse?.ok && retryResponse.context) {
        const currentSubtitleLength = String(response.context?.subtitleText || '').trim().length;
        const retriedSubtitleLength = String(retryResponse.context?.subtitleText || '').trim().length;
        const currentDescriptionLength = String(response.context?.descriptionText || '').trim().length;
        const retriedDescriptionLength = String(retryResponse.context?.descriptionText || '').trim().length;
        if (retriedSubtitleLength > currentSubtitleLength || retriedDescriptionLength > currentDescriptionLength) {
          response = retryResponse;
        }
      }
    }
  }
  state.currentContext = response.context;
  renderPageMeta(response.context);
  return response.context;
}

function renderPageMeta(context) {
  if (!context) {
    pageMetaEl.textContent = '尚未读取页面上下文。';
    updateActionAvailability('当前页面尚未读取到可用上下文。');
    return;
  }
  const selectionCount = String(context.selectionText || '').trim().length;
  const contentKind = String(context.contentKind || 'article');
  const rootHint = String(context.rootHint || 'body');
  const mediaHints = context?.mediaHints || {};
  const diagnostics = context?.extractionDiagnostics || {};
  const subtitleStatus = String(context?.subtitleStatus || mediaHints.subtitleStatus || 'absent');
  const subtitleTip = contentKind === 'video_page'
    ? ` | 字幕 ${subtitleStatus}`
    : '';
  const confidenceTip = diagnostics.contentConfidence != null
    ? ` | 置信 ${diagnostics.contentConfidence}`
    : '';
  pageMetaEl.textContent = `${context.title || '未命名页面'} | ${context.url || ''} | 类型 ${contentKind} | 已选中文本 ${selectionCount} 字 | 主提取 ${rootHint}${subtitleTip}${confidenceTip}`;
  renderContextProfile(context, state.lastSummary);
  updateActionAvailability();
}

async function callBridge(path, payload) {
  const result = await requestBridge(path, { method: 'POST', payload });
  return result.json;
}

async function fetchBridgeJson(path) {
  const result = await requestBridge(path, { method: 'GET' });
  return result;
}

function renderDiagnostics(payload, resolvedBase) {
  const workspace = payload?.workspace || {};
  const bridge = payload?.bridge || {};
  const actionItems = [
    `待办 ${workspace.pendingTasks || 0}`,
    `收件 ${workspace.inboxEntries || 0}`,
    `笔记 ${workspace.notes || 0}`,
    `翻译 ${workspace.translations || 0}`,
    `收藏新闻 ${workspace.favoriteNews || 0}`,
  ];
  setList(workspaceOverviewEl, actionItems, '暂无桌面工作台概览');
  setList(bridgeTipsEl, payload?.tips, '暂无诊断建议');
  const actionNames = [];
  if (payload?.availableActions?.capture) actionNames.push('收件箱');
  if (payload?.availableActions?.createNote) actionNames.push('笔记');
  if (payload?.availableActions?.createTask) actionNames.push('任务');
  const actionText = actionNames.length ? `可直接沉淀到：${actionNames.join('、')}` : '当前桌面端可用动作有限。';
  const bridgeLabel = bridge.running ? `桌面端桥接运行中，地址 ${bridge.base_url || state.bridgeBase}` : '桌面端桥接未运行';
  const fallbackNotice = resolvedBase && resolvedBase !== state.bridgeBase
    ? `；当前实际连通地址是 ${resolvedBase}，如需固定可在设置页保存`
    : '';
  if (bridgeHelpEl) {
    bridgeHelpEl.textContent = `${bridgeLabel}；${actionText}${fallbackNotice}`;
  }
}

async function refreshBridgeStatus() {
  try {
    const { json, base } = await fetchBridgeJson('/api/browser-assistant/diagnostics');
    renderDiagnostics(json, base);
    const checkedAt = new Date().toISOString();
    setBridgeLastCheckText(`最近检查：${formatCheckedAt(checkedAt)}`);
    if (json.bridge?.running) {
      const statusText = `桌面端已连接 ${json.bridge.base_url || base || state.bridgeBase}`;
      setBridgeStatus(statusText, 'ok');
      await persistBridgeHistory(
        buildBridgeHistoryEntry({
          ok: true,
          base: base || state.bridgeBase,
          statusText,
          helpText: bridgeHelpEl?.textContent || '',
          checkedAt,
        })
      );
      return;
    }
    if (json.bridge?.enabled === false) {
      const statusText = '桌面端已禁用浏览器桥';
      setBridgeStatus(statusText, 'warning');
      await persistBridgeHistory(
        buildBridgeHistoryEntry({ ok: false, base: base || state.bridgeBase, statusText, helpText: bridgeHelpEl?.textContent || '', checkedAt })
      );
      return;
    }
    const statusText = '桌面端未就绪';
    setBridgeStatus(statusText, 'warning');
    await persistBridgeHistory(
      buildBridgeHistoryEntry({ ok: false, base: base || state.bridgeBase, statusText, helpText: bridgeHelpEl?.textContent || '', checkedAt })
    );
  } catch (error) {
    const checkedAt = new Date().toISOString();
    setBridgeStatus(error.statusText || '桌面端未启动', error.tone || 'error');
    if (bridgeHelpEl) {
      bridgeHelpEl.textContent = error.message || `请确认桌面端已启动，且桥接地址可访问：${state.bridgeBase}`;
    }
    setBridgeLastCheckText(`最近检查：${formatCheckedAt(checkedAt)}`);
    setList(workspaceOverviewEl, [], '尚未获取桌面工作台概览');
    setList(bridgeTipsEl, [String(error.message || error)], '暂无诊断建议');
    await persistBridgeHistory(
      buildBridgeHistoryEntry({
        ok: false,
        base: state.bridgeBase,
        statusText: error.statusText || '桌面端未启动',
        helpText: String(error.message || error),
        checkedAt,
      })
    );
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
    li.textContent = typeof item === 'string' ? item : String(item);
    element.appendChild(li);
  }
}

function resolveRecommendedActionConfig(actionId) {
  const mapping = {
    capture: {
      path: '/api/browser-assistant/capture',
      successText: '已按建议发送到思忆集收件箱。',
      buttonTone: 'secondary',
    },
    createNote: {
      path: '/api/browser-assistant/create-note',
      successText: '已按建议转成思忆集笔记。',
      buttonTone: 'secondary',
    },
    createTask: {
      path: '/api/browser-assistant/create-task',
      successText: '已按建议转成思忆集任务。',
      buttonTone: 'primary',
    },
    transcribe_subtitle: {
      path: '',
      successText: '',
      buttonTone: 'primary',
      virtual: true,
      actionAlias: 'transcribe_subtitle',
    },
    paste_transcript: {
      path: '',
      successText: '',
      buttonTone: 'secondary',
      virtual: true,
      actionAlias: 'paste_transcript',
    },
  };
  return mapping[String(actionId || '').trim()] || null;
}

function markRecommendedActionCompleted(actionId, successText) {
  if (!state.lastSummary || !Array.isArray(state.lastSummary.recommendedActions)) {
    return;
  }
  const completedAt = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  state.lastSummary.recommendedActions = state.lastSummary.recommendedActions.map((item) => {
    if (!item || item.actionId !== actionId) {
      return item;
    }
    return {
      ...item,
      enabled: 'false',
      disabledReason: successText || '该推荐动作已执行。',
      priority: String(Math.max(0, Number(item.priority || 0) - 100)),
      completedAt,
    };
  });
  state.lastSummary.topRecommendedAction = selectTopRecommendedAction(state.lastSummary.recommendedActions);
}

function selectTopRecommendedAction(items) {
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  const sorted = [...values].sort((left, right) => {
    const leftEnabled = String(left?.enabled || 'true').trim().toLowerCase() !== 'false';
    const rightEnabled = String(right?.enabled || 'true').trim().toLowerCase() !== 'false';
    if (leftEnabled !== rightEnabled) {
      return leftEnabled ? -1 : 1;
    }
    return Number(right?.priority || 0) - Number(left?.priority || 0);
  });
  return sorted[0] || null;
}

function renderActionResult(result, successText) {
  if (summaryOutputEl) {
    summaryOutputEl.textContent = successText;
  }
  if (result.entry) {
    if (pageMetaEl) {
      pageMetaEl.textContent = `已写入收件箱：${result.entry.title || result.entry.id}`;
    }
  } else if (result.noteId) {
    if (pageMetaEl) {
      pageMetaEl.textContent = `已创建笔记：${result.title || result.noteId}`;
    }
  } else if (result.taskId) {
    if (pageMetaEl) {
      pageMetaEl.textContent = `已创建任务：${result.title || result.taskId}`;
    }
  }
}

function buildSummaryChatText(result) {
  const lines = [];
  const summary = String(result?.summary || '').trim();
  const keyPoints = Array.isArray(result?.keyPoints) ? result.keyPoints.filter(Boolean) : [];
  const actionHints = Array.isArray(result?.actionHints) ? result.actionHints.filter(Boolean) : [];
  const warnings = Array.isArray(result?.meta?.warnings) ? result.meta.warnings.filter(Boolean) : [];
  if (warnings.length) {
    lines.push(`注意：\n${warnings.slice(0, 2).map((item, index) => `${index + 1}. ${item}`).join('\n')}`);
  }
  if (summary) {
    lines.push(`总结：\n${summary}`);
  }
  if (keyPoints.length) {
    lines.push(`重点：\n${keyPoints.map((item, index) => `${index + 1}. ${item}`).join('\n')}`);
  }
  if (actionHints.length) {
    lines.push(`下一步建议：\n${actionHints.map((item, index) => `${index + 1}. ${item}`).join('\n')}`);
  }
  return lines.join('\n\n') || '已完成总结，但当前没有提取到可展示内容。';
}

function renderRecommendedActions(items) {
  if (!recommendedActionsEl) {
    return;
  }
  recommendedActionsEl.innerHTML = '';
  const values = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!values.length) {
    const empty = document.createElement('p');
    empty.className = 'muted small';
    empty.textContent = '当前没有结构化推荐动作。';
    recommendedActionsEl.appendChild(empty);
    return;
  }
  values.sort((left, right) => {
    const leftEnabled = String(left?.enabled || 'true').trim().toLowerCase() !== 'false';
    const rightEnabled = String(right?.enabled || 'true').trim().toLowerCase() !== 'false';
    if (leftEnabled !== rightEnabled) {
      return leftEnabled ? -1 : 1;
    }
    return Number(right?.priority || 0) - Number(left?.priority || 0);
  });
  for (const item of values) {
    const config = resolveRecommendedActionConfig(item.actionId);
    if (!config) {
      continue;
    }
    const enabled = String(item.enabled || 'true').trim().toLowerCase() !== 'false';
    const card = document.createElement('div');
    card.className = 'recommended-action-card';
    const title = document.createElement('strong');
    title.textContent = `${item.label || item.actionId} · P${item.priority || 0}`;
    const reason = document.createElement('span');
    reason.textContent = item.reason || '无附加说明';
    const meta = document.createElement('div');
    meta.className = 'recommended-action-meta';
    const completedAt = String(item.completedAt || '').trim();
    if (completedAt) {
      meta.textContent = `已于 ${completedAt} 执行；${item.disabledReason || '该推荐动作已完成。'}`;
    } else {
      meta.textContent = enabled ? '当前可直接执行' : (item.disabledReason || '当前条件不足，暂不可执行');
    }
    const button = document.createElement('button');
    button.className = config.buttonTone;
    button.textContent = item.label || '执行';
    button.disabled = !enabled;
    button.addEventListener('click', async () => {
      try {
        if (config.virtual) {
          await executeSuggestedAction(config.actionAlias || item.actionId);
        } else {
          await runAction(config.path, config.successText, item.actionId);
        }
      } catch (error) {
        summaryOutputEl.textContent = error.message;
      }
    });
    card.appendChild(title);
    card.appendChild(reason);
    card.appendChild(meta);
    card.appendChild(button);
    recommendedActionsEl.appendChild(card);
  }
}

function renderSummary(result) {
  state.lastSummary = result;
  state.lastSummary.topRecommendedAction = result?.topRecommendedAction || selectTopRecommendedAction(result?.recommendedActions || []);

  // ===== 结果展示区 =====
  const contentKind = String(result?.meta?.contentKind || state.currentContext?.contentKind || 'article');
  const subtitleView = result?.resultViews?.subtitle;
  const hasSubtitleTab = Boolean(subtitleView); // 视频页后端始终返回 subtitle 视图

  // 决定可用视图
  state.availableResultViews = ['summary'];
  if (hasSubtitleTab) {
    state.availableResultViews.push('subtitle');
  }
  state.resultViews = result?.resultViews || null;

  // 渲染总结卡片
  renderResultSummaryCard(result);

  // 渲染字幕视图（视频页始终渲染，包括空态）
  if (hasSubtitleTab) {
    renderResultSubtitleView(result);
  }

  // 更新 switcher 可见性 & 选中 tab
  updateResultViewSwitcher();
  switchResultView('summary');
  showResultView(true);

  // ===== 兼容：写入 legacy 节点 =====
  if (summaryOutputEl) {
    const warnings = Array.isArray(result?.meta?.warnings) ? result.meta.warnings.filter(Boolean) : [];
    const warningLine = warnings.find((item) => String(item).includes('未检测到字幕') || String(item).includes('文本较少'));
    summaryOutputEl.textContent = warningLine
      ? `${result.summary || '未返回摘要'}\n\n提示：${warningLine}`
      : (result.summary || '未返回摘要');
  }
  setList(keyPointsEl, result.keyPoints, '暂无关键点');
  setList(actionHintsEl, result.actionHints, '暂无建议动作');
  setList(contextSuggestionsEl, result.contextSuggestions, '暂无上下文建议');
  renderRecommendedActions(result.recommendedActions);
  renderContextProfile(state.currentContext, result);
  updateActionAvailability();
  writeLocalStorage({
    [LATEST_SUMMARY_STORAGE_KEY]: {
      summary: result?.summary || '',
      keyPoints: Array.isArray(result?.keyPoints) ? result.keyPoints : [],
      actionHints: Array.isArray(result?.actionHints) ? result.actionHints : [],
      updatedAt: new Date().toISOString(),
      url: state.currentContext?.url || '',
      title: state.currentContext?.title || '',
    },
  }).catch(() => {});

  // ===== Chat：只发一条简短通知，不再 dump 全部内容 =====
  const briefLines = [];
  const summary = String(result?.summary || '').trim();
  if (summary) {
    briefLines.push(summary.length > 120 ? summary.slice(0, 120) + '…' : summary);
  }
  const pointCount = Array.isArray(result?.keyPoints) ? result.keyPoints.filter(Boolean).length : 0;
  if (pointCount) {
    briefLines.push(`（共 ${pointCount} 条重点，已展示在上方卡片）`);
  }
  appendChatMessage('assistant', briefLines.join('\n') || '已完成总结，详见上方卡片。');
}

function buildSummaryPayload(context, mode) {
  return {
    url: context.url,
    title: context.title,
    contentKind: context.contentKind,
    rootHint: context.rootHint,
    extractionMode: context.extractionMode,
    mediaHints: context.mediaHints || {},
    selectionText: mode === 'selection' ? context.selectionText : '',
    pageText: context.pageText,
    descriptionText: context.descriptionText || '',
    subtitleText: context.subtitleText || '',
    subtitleStatus: context.subtitleStatus || '',
    extractionDiagnostics: context.extractionDiagnostics || {},
  };
}

async function summarize(mode) {
  const context = await getActivePageContext();
  if (mode === 'selection' && !String(context.selectionText || '').trim()) {
    throw new Error('请先在网页中选中一段内容。');
  }
  if (summaryOutputEl) {
    summaryOutputEl.textContent = '正在请求思忆集桌面端总结...';
  }
  const result = await callBridge('/api/browser-assistant/summarize', buildSummaryPayload(context, mode));
  renderSummary(result);
}

function askTranscriptText(actionKey) {
  const helper = actionKey === 'transcribe_subtitle'
    ? '当前未拿到可用字幕文本，请粘贴字幕/转录文本：'
    : '请粘贴字幕或转录文本：';
  const value = globalThis.prompt ? globalThis.prompt(helper, '') : '';
  return String(value || '').trim();
}

async function summarizeWithTranscript(transcriptText, existingContext = null) {
  const context = existingContext || await getActivePageContext();
  const descriptionText = String(context?.descriptionText || context?.pageText || '').slice(0, 3000);
  const payload = {
    ...buildSummaryPayload(context, 'page'),
    transcriptText,
    descriptionText,
  };
  if (summaryOutputEl) {
    summaryOutputEl.textContent = '正在合并标题、简介和转录文本进行总结...';
  }
  const result = await callBridge('/api/browser-assistant/summarize-with-transcript', payload);
  renderSummary(result);
  switchResultTab('summary');
}

async function transcribeSubtitleFromBridge(context, transcriptText = '') {
  const descriptionText = String(context?.descriptionText || context?.pageText || '').slice(0, 3000);
  // Use auto-extracted subtitle from context if available and no manual override
  const autoExtractedSubtitle = String(context?.subtitleText || '').trim();
  const finalTranscriptText = String(transcriptText || autoExtractedSubtitle || '').trim();
  
  const payload = {
    ...buildSummaryPayload(context, 'page'),
    transcriptText: finalTranscriptText,
    descriptionText,
  };
  return callBridge('/api/browser-assistant/transcribe-subtitle', payload);
}

function buildActionPayload() {
  const context = state.currentContext;
  const summary = state.lastSummary;
  if (!context) {
    throw new Error('请先读取当前页面或先做一次总结。');
  }
  return {
    url: context.url,
    title: context.title,
    contentKind: context.contentKind,
    rootHint: context.rootHint,
    extractionMode: context.extractionMode,
    mediaHints: context.mediaHints || {},
    selectionText: context.selectionText,
    pageText: context.pageText,
    descriptionText: context.descriptionText || '',
    subtitleText: context.subtitleText || '',
    subtitleStatus: context.subtitleStatus || '',
    extractionDiagnostics: context.extractionDiagnostics || {},
    summary: summary?.summary || '',
    keyPoints: summary?.keyPoints || [],
    studySuggestions: summary?.studySuggestions || [],
    actionHints: summary?.actionHints || [],
    recommendedActions: summary?.recommendedActions || [],
  };
}

function buildExecutedRecommendedAction(actionId) {
  const items = Array.isArray(state.lastSummary?.recommendedActions) ? state.lastSummary.recommendedActions : [];
  const matched = items.find((item) => item && item.actionId === actionId) || {};
  return {
    actionId,
    label: matched.label || actionId,
    executedAt: new Date().toISOString(),
  };
}

async function runAction(path, successText, actionId = '') {
  const payload = buildActionPayload();
  if (actionId) {
    payload.executedRecommendedAction = buildExecutedRecommendedAction(actionId);
  }
  const result = await callBridge(path, payload);
  if (actionId) {
    markRecommendedActionCompleted(actionId, successText);
    renderRecommendedActions(state.lastSummary?.recommendedActions || []);
  }
  renderActionResult(result, successText);
  appendChatMessage('assistant', successText);
  await refreshBridgeStatus();
  return result;
}

async function importKimiConversations() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error('未找到当前标签页');
  }
  if (!String(tab.url || '').includes('kimi.com')) {
    throw new Error('当前页面不是 Kimi');
  }
  appendChatMessage('assistant', '正在读取当前 Kimi 对话...');
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content-script.js'],
    });
  } catch (error) {
    throw new Error('无法注入脚本到 Kimi 页面');
  }
  const response = await chrome.tabs.sendMessage(tab.id, { type: 'COLLECT_KIMI_CONVERSATIONS' });
  if (!response || !response.ok) {
    throw new Error(response?.error || '读取 Kimi 对话失败');
  }
  const conversations = response.conversations || [];
  if (!conversations.length) {
    throw new Error('未找到可导入的对话');
  }
  appendChatMessage('assistant', `已读取 ${conversations.length} 个对话，正在导入思忆集...`);
  const result = await callBridge('/api/browser-assistant/import-conversations', { conversations });
  const imported = result?.imported || 0;
  const sessionsSaved = result?.sessions_saved || 0;
  const memoriesStored = result?.memories_stored || 0;
  const summaryText = `已导入 ${imported} 个对话，保存 ${sessionsSaved} 条会话，提取 ${memoriesStored} 条记忆。`;
  renderActionResult(result, summaryText);
  appendChatMessage('assistant', summaryText);
  await refreshBridgeStatus();
}

async function importDeepSeekConversations() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error('未找到当前标签页');
  }
  if (!String(tab.url || '').includes('chat.deepseek.com')) {
    throw new Error('当前页面不是 DeepSeek 对话页');
  }
  appendChatMessage('assistant', '正在读取当前 DeepSeek 对话...');
  try {
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ['content-script.js'],
    });
  } catch (error) {
    throw new Error('无法注入脚本到 DeepSeek 页面');
  }
  const response = await chrome.tabs.sendMessage(tab.id, { type: 'COLLECT_DEEPSEEK_CONVERSATIONS' });
  if (!response || !response.ok) {
    throw new Error(response?.error || '读取 DeepSeek 对话失败');
  }
  const conversations = response.conversations || [];
  if (!conversations.length) {
    throw new Error('未找到可导入的对话');
  }
  appendChatMessage('assistant', `已读取 ${conversations.length} 个对话，正在导入思忆集...`);
  const result = await callBridge('/api/browser-assistant/import-conversations', { conversations });
  const imported = result?.imported || 0;
  const sessionsSaved = result?.sessions_saved || 0;
  const memoriesStored = result?.memories_stored || 0;
  const summaryText = `已导入 ${imported} 个对话，保存 ${sessionsSaved} 条会话，提取 ${memoriesStored} 条记忆。`;
  renderActionResult(result, summaryText);
  appendChatMessage('assistant', summaryText);
  await refreshBridgeStatus();
}

async function executeSuggestedAction(action) {
  const key = String(action || '').trim();
  if (key === 'transcribe_subtitle' || key === 'paste_transcript') {
    try {
      const context = await getActivePageContext();
      
      // Step 1: Determine transcript source - auto-extracted or manual
      let transcriptText = '';
      let source = '';
      
      if (key === 'paste_transcript') {
        // Force manual input for paste_transcript action
        transcriptText = askTranscriptText(key);
        source = 'manual';
      } else {
        // For transcribe_subtitle: auto-extract if available, fallback to manual
        const autoSubtitle = String(context?.subtitleText || '').trim();
        if (autoSubtitle) {
          transcriptText = autoSubtitle;
          source = 'auto-extracted';
        } else {
          // No auto-extracted subtitles; try backend check
          const transcribeResult = await transcribeSubtitleFromBridge(context);
          if (transcribeResult?.transcriptText) {
            transcriptText = String(transcribeResult.transcriptText).trim();
            source = transcribeResult?.source || 'backend';
          }
          // If still no text and backend requires manual input, ask user
          if (!transcriptText && transcribeResult?.requiresManualInput) {
            transcriptText = askTranscriptText(key);
            source = 'manual';
          }
        }
      }
      
      // Step 2: Validate we have transcript text
      if (!transcriptText) {
        appendChatMessage('assistant', '未提供转录文本，已取消本次补充总结。');
        return;
      }
      
      // Step 3: Perform final summary with the transcript
      await summarizeWithTranscript(transcriptText, context);
      
      // Step 4: Provide feedback about source
      const sourceLabel = {
        'auto-extracted': '自动提取的字幕',
        'manual': '手动输入的转录',
        'backend': '后端检测到的文本',
      }[source] || '转录文本';
      
      appendChatMessage('assistant', `已基于${sourceLabel}完成二次总结。`);
    } catch (error) {
      appendChatMessage('assistant', `转录补充总结失败：${error.message}`);
    }
    return;
  }
  if (key === 'summarize_page') {
    try {
      await summarize('page');
      switchResultTab('summary');
      appendChatMessage('assistant', '已为你刷新当前页面总结，可切到“重点”或“动作”查看。');
    } catch (error) {
      appendChatMessage('assistant', `总结失败：${error.message}`);
    }
    return;
  }
  if (key === 'summarize_selection') {
    try {
      await summarize('selection');
      switchResultTab('summary');
      appendChatMessage('assistant', '已基于选中文本完成总结。');
    } catch (error) {
      appendChatMessage('assistant', `选中总结失败：${error.message}`);
    }
    return;
  }
  if (key === 'capture') {
    try {
      await runAction('/api/browser-assistant/capture', '已发送到思忆集收件箱。');
      appendChatMessage('assistant', '已把当前内容发送到收件箱。');
    } catch (error) {
      appendChatMessage('assistant', `收件失败：${error.message}`);
    }
    return;
  }
  if (key === 'create_note') {
    try {
      await runAction('/api/browser-assistant/create-note', '已转成思忆集笔记。');
      appendChatMessage('assistant', '已创建笔记。');
    } catch (error) {
      appendChatMessage('assistant', `笔记创建失败：${error.message}`);
    }
    return;
  }
  if (key === 'create_task') {
    try {
      await runAction('/api/browser-assistant/create-task', '已转成思忆集任务。');
      appendChatMessage('assistant', '已创建任务。');
    } catch (error) {
      appendChatMessage('assistant', `任务创建失败：${error.message}`);
    }
  }
}

function buildChatAnswer(question) {
  const q = String(question || '').toLowerCase();
  const hasContext = hasReadableContext();
  if (!hasContext) {
    return {
      text: '当前还没有可用页面上下文。请先切换到普通网页，再点“总结当前页面”。',
      suggestions: getDefaultChatSuggestions(),
    };
  }

  const summary = String(state.lastSummary?.summary || '').trim();
  const keyPoints = Array.isArray(state.lastSummary?.keyPoints) ? state.lastSummary.keyPoints.filter(Boolean) : [];
  const actionHints = Array.isArray(state.lastSummary?.actionHints) ? state.lastSummary.actionHints.filter(Boolean) : [];
  const contentKind = String(state.currentContext?.contentKind || 'article');

  if (!summary && (q.includes('总结') || q.includes('重点') || q.includes('建议'))) {
    return {
      text: '你在问总结相关问题，建议先做一次“总结当前页面”，我再给你更精准答复。',
      suggestions: [
        { label: '现在总结', action: 'summarize_page' },
        { label: '存到收件箱', action: 'capture' },
      ],
    };
  }

  const lines = [];
  lines.push(`已基于当前${contentKind}分析：`);
  if (summary) {
    lines.push(`摘要：${summary}`);
  }
  if (keyPoints.length) {
    lines.push(`重点：\n${keyPoints.slice(0, 3).map((item, index) => `${index + 1}. ${item}`).join('\n')}`);
  }
  if (actionHints.length) {
    lines.push(`建议：\n${actionHints.slice(0, 2).map((item, index) => `${index + 1}. ${item}`).join('\n')}`);
  }

  let suggestions = [
    { label: '转成笔记', action: 'create_note' },
    { label: '生成任务', action: 'create_task' },
    { label: '存到收件箱', action: 'capture' },
  ];
  if (q.includes('任务') || q.includes('执行') || q.includes('下一步')) {
    suggestions = [
      { label: '生成任务', action: 'create_task' },
      { label: '存到收件箱', action: 'capture' },
      { label: '总结当前页面', action: 'summarize_page' },
    ];
  } else if (q.includes('笔记')) {
    suggestions = [
      { label: '转成笔记', action: 'create_note' },
      { label: '总结当前页面', action: 'summarize_page' },
      { label: '存到收件箱', action: 'capture' },
    ];
  }
  return {
    text: lines.join('\n\n') || '我已经拿到页面上下文，可以继续问我重点、建议或下一步动作。',
    suggestions,
  };
}

async function requestBridgeExplain(question, context) {
  const payload = {
    question,
    context: `当前页面：${context?.title || '未命名'}`,
    pageText: context?.pageText || '',
  };
  const result = await callBridge('/api/browser-assistant/explain', payload);
  return result.explanation || '暂无解释结果';
}

async function handleChatSend() {
  const question = String(chatInputEl?.value || '').trim();
  if (!question) {
    return;
  }
  appendChatMessage('user', question);
  if (chatInputEl) {
    chatInputEl.value = '';
  }

  // 检测问题是否是"命令"类型（执行特定动作）vs"提问"类型（寻求 LLM 解释）
  const isCommand = /^(总结|整理|转笔记|生成任务|提取|捕获)/.test(question);

  if (isCommand) {
    // 命令路径：执行现有逻辑（自动总结 + 构建本地回答）
    const needsSummary = !hasSummaryResult() && /总结|重点|建议|下一步|任务|笔记|怎么做/.test(question);
    if (needsSummary && hasReadableContext()) {
      try {
        await summarize('page');
      } catch (_error) {
        // 自动总结失败时继续给出基础回答。
      }
    }

    const answer = buildChatAnswer(question);
    appendChatMessage('assistant', answer.text);
    renderChatSuggestions(answer.suggestions);
  } else {
    // 提问路径：调用 LLM 深度解释
    try {
      const explanation = await requestBridgeExplain(question, state.currentContext);
      appendChatMessage('assistant', explanation);
      renderChatSuggestions([
        { label: '继续深入了解', action: 'summarize_page' },
        { label: '存到收件箱', action: 'capture' },
        { label: '转成笔记', action: 'create_note' },
      ]);
    } catch (error) {
      // 如果 LLM 解释失败，回退到本地回答
      const answer = buildChatAnswer(question);
      appendChatMessage('assistant', answer.text);
      renderChatSuggestions(answer.suggestions);
    }
  }
}

async function bindActions() {
  for (const button of tabButtons) {
    button.addEventListener('click', () => {
      switchResultTab(button.dataset.tabTarget || 'summary');
    });
  }
  // 结果展示区 tab 切换
  if (resultViewSwitcherEl) {
    for (const btn of resultViewSwitcherEl.querySelectorAll('.rv-tab')) {
      btn.addEventListener('click', () => {
        switchResultView(btn.dataset.rv || 'summary');
      });
    }
  }
  // 字幕复制按钮
  if (subtitleCopyBtnEl) {
    subtitleCopyBtnEl.addEventListener('click', () => {
      // 只复制纯字幕文本，不复制空态提示
      const subtitleData = state.resultViews?.subtitle;
      const text = subtitleData?.available !== false ? String(subtitleData?.text || '').trim() : '';
      if (text && navigator.clipboard) {
        navigator.clipboard.writeText(text).then(() => {
          subtitleCopyBtnEl.textContent = '已复制';
          setTimeout(() => { subtitleCopyBtnEl.textContent = '复制'; }, 1500);
        }).catch(() => {});
      }
    });
  }
  doc.getElementById('refresh-status')?.addEventListener('click', refreshBridgeStatus);
  doc.getElementById('run-diagnostics')?.addEventListener('click', refreshBridgeStatus);
  doc.getElementById('open-settings')?.addEventListener('click', async () => {
    await chrome.runtime.openOptionsPage();
  });
  doc.getElementById('save-bridge-base')?.addEventListener('click', async () => {
    try {
      await saveBridgeBase(bridgeBaseInputEl?.value);
      if (summaryOutputEl) {
        summaryOutputEl.textContent = '桥接地址已保存，正在重新检查桌面连接。';
      }
      await refreshBridgeStatus();
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
    }
  });
  doc.getElementById('reset-bridge-base')?.addEventListener('click', async () => {
    await saveBridgeBase(DEFAULT_BRIDGE_BASE);
    if (summaryOutputEl) {
      summaryOutputEl.textContent = '已恢复默认桥接地址。';
    }
    await refreshBridgeStatus();
  });
  doc.getElementById('summarize-selection')?.addEventListener('click', async () => {
    try {
      await summarize('selection');
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      if (pageMetaEl) {
        pageMetaEl.textContent = `页面读取失败：${error.message}`;
      }
      appendChatMessage('assistant', `总结失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });
  doc.getElementById('summarize-page')?.addEventListener('click', async () => {
    try {
      await summarize('page');
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      if (pageMetaEl) {
        pageMetaEl.textContent = `页面读取失败：${error.message}`;
      }
      appendChatMessage('assistant', `总结失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });
  doc.getElementById('capture-page')?.addEventListener('click', async () => {
    try {
      await runAction('/api/browser-assistant/capture', '已发送到思忆集收件箱。');
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      appendChatMessage('assistant', `动作失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });
  doc.getElementById('save-reading')?.addEventListener('click', async () => {
    try {
      const payload = buildActionPayload();
      const result = await callBridge('/api/browser-assistant/save-to-reading', payload);
      const title = result?.title || state.currentContext?.title || '网页';
      renderActionResult(result, `「${title}」已保存到阅读列表。`);
      appendChatMessage('assistant', `已把当前页面保存到阅读列表：${title}`);
      await refreshBridgeStatus();
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      appendChatMessage('assistant', `保存阅读列表失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });
  doc.getElementById('save-bookmark')?.addEventListener('click', async () => {
    try {
      const payload = buildActionPayload();
      payload.source = 'browser_bookmark';
      payload.contentKind = 'bookmark';
      payload.externalId = payload.url;
      payload.tags = ['browser', 'bookmark'];
      const result = await callBridge('/api/browser-assistant/save-to-reading', payload);
      const title = result?.title || state.currentContext?.title || '书签';
      renderActionResult(result, `「${title}」已加入书签。`);
      appendChatMessage('assistant', `已把当前页面加入书签：${title}`);
      await refreshBridgeStatus();
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      appendChatMessage('assistant', `加入书签失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });
  doc.getElementById('create-note')?.addEventListener('click', async () => {
    try {
      await runAction('/api/browser-assistant/create-note', '已转成思忆集笔记。');
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      appendChatMessage('assistant', `动作失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });
  doc.getElementById('create-task')?.addEventListener('click', async () => {
    try {
      await runAction('/api/browser-assistant/create-task', '已转成思忆集任务。');
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      appendChatMessage('assistant', `动作失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });
  doc.getElementById('import-kimi')?.addEventListener('click', async () => {
    try {
      await importKimiConversations();
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      appendChatMessage('assistant', `导入 Kimi 失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });
  doc.getElementById('import-deepseek')?.addEventListener('click', async () => {
    try {
      await importDeepSeekConversations();
    } catch (error) {
      if (summaryOutputEl) {
        summaryOutputEl.textContent = error.message;
      }
      appendChatMessage('assistant', `导入 DeepSeek 失败：${error.message}`);
      updateActionAvailability(error.message);
    }
  });

  if (panelCloseBtnEl) {
    panelCloseBtnEl.addEventListener('click', () => {
      document.body.style.display = 'none';
    });
  }

  if (chatSendBtnEl) {
    chatSendBtnEl.addEventListener('click', async () => {
      await handleChatSend();
    });
  }
  if (chatInputEl) {
    chatInputEl.addEventListener('keydown', async (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        await handleChatSend();
      }
    });
  }
}

(async function bootstrap() {
  if (bridgeBaseInputEl) {
    bridgeBaseInputEl.value = await loadBridgeState();
  }
  await bindActions();
  switchResultTab('summary');
  appendChatMessage('assistant', '你好，我是页面对话助手。你可以问我这页重点、下一步动作，或直接点下方建议。');
  renderChatSuggestions(getDefaultChatSuggestions());
  await refreshBridgeStatus();
  updateActionAvailability();
  try {
    await getActivePageContext();
  } catch (error) {
    if (summaryOutputEl) {
      summaryOutputEl.textContent = error.message;
    }
    updateActionAvailability(error.message);
  }
})();
