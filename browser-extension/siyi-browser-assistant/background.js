// Service worker entry. We import the auth/utils helpers via classic
// importScripts so we can call the desktop bridge from the right-click menu.
try {
  importScripts('bridge-utils.js', 'bridge-auth.js');
} catch (importError) {
  console.error('[siyi] failed to import bridge helpers', importError);
}

const ADD_WORD_MENU_ID = 'siyi-add-word';
const SAVE_READING_MENU_ID = 'siyi-save-reading';
const SAVE_BOOKMARK_MENU_ID = 'siyi-save-bookmark';

function registerContextMenus() {
  // Register the right-click context menu (idempotent: clear any stale entry first).
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: ADD_WORD_MENU_ID,
      title: '加入思忆集生词本：%s',
      contexts: ['selection'],
    });
    chrome.contextMenus.create({
      id: SAVE_READING_MENU_ID,
      title: '保存到思忆集阅读列表',
      contexts: ['page'],
    });
    chrome.contextMenus.create({
      id: SAVE_BOOKMARK_MENU_ID,
      title: '加入思忆集书签',
      contexts: ['page', 'link'],
    });
  });
}

chrome.runtime.onInstalled.addListener(async () => {
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    try {
      await chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });
    } catch (error) {
      console.debug('Failed to set side panel behavior', error);
    }
  }
  registerContextMenus();
});

chrome.runtime.onStartup?.addListener?.(() => {
  // Re-register on cold start in case the menu was lost when the SW shut down.
  registerContextMenus();
});

chrome.action.onClicked.addListener(async (tab) => {
  if (!chrome.sidePanel || !chrome.sidePanel.open || !tab?.windowId) {
    return;
  }
  try {
    await chrome.sidePanel.open({ windowId: tab.windowId });
  } catch (error) {
    console.debug('Failed to open side panel', error);
  }
});

// ── Keyboard shortcut ───────────────────────────────────────────────
chrome.commands?.onCommand?.addListener?.(async (command, tab) => {
  if (command === 'save-to-reading') {
    await handleSaveReading(tab);
  }
});

// ── Right-click → 加入生词本 / 阅读列表 / 书签 ───────────────────────
chrome.contextMenus?.onClicked?.addListener?.(async (info, tab) => {
  if (info.menuItemId === ADD_WORD_MENU_ID) {
    const selection = String(info.selectionText || '').trim();
    if (!selection) {
      notifyUser('未选中文本', '请先选中要加入生词本的英文词或短语。');
      return;
    }
    try {
      const client = getBridgeClient();
      if (!client) {
        notifyUser('扩展未就绪', '桥接客户端未初始化，请重启浏览器后重试。');
        return;
      }
      const stored = await loadBridgeBase();
      const result = await client.request('/api/browser-assistant/add-word', {
        method: 'POST',
        payload: {
          text: selection.slice(0, 200),
          url: tab?.url || info.pageUrl || '',
          title: tab?.title || '',
        },
        requestedBase: stored,
      });
      const word = result?.json?.word || selection.slice(0, 30);
      notifyUser('已加入生词本', `「${word}」已存入思忆集，可在桌面端复习。`);
    } catch (error) {
      const message = String(error?.message || error || '未知错误');
      notifyUser('加入生词本失败', message.slice(0, 120));
    }
    return;
  }

  if (info.menuItemId === SAVE_READING_MENU_ID) {
    await handleSaveReading(tab);
    return;
  }

  if (info.menuItemId === SAVE_BOOKMARK_MENU_ID) {
    await handleSaveBookmark(info, tab);
    return;
  }
});

async function handleSaveReading(tab) {
  try {
    const client = getBridgeClient();
    if (!client) {
      notifyUser('扩展未就绪', '桥接客户端未初始化，请重启浏览器后重试。');
      return;
    }
    const stored = await loadBridgeBase();
    const result = await client.request('/api/browser-assistant/save-to-reading', {
      method: 'POST',
      payload: {
        title: tab?.title || '',
        url: tab?.url || '',
        source: 'browser_extension',
        contentKind: 'article',
      },
      requestedBase: stored,
    });
    const title = result?.json?.title || tab?.title || '网页';
    notifyUser('已保存到阅读列表', `「${title}」已存入思忆集阅读列表。`);
  } catch (error) {
    const message = String(error?.message || error || '未知错误');
    notifyUser('保存阅读列表失败', message.slice(0, 120));
  }
}

async function handleSaveBookmark(info, tab) {
  try {
    const client = getBridgeClient();
    if (!client) {
      notifyUser('扩展未就绪', '桥接客户端未初始化，请重启浏览器后重试。');
      return;
    }
    const stored = await loadBridgeBase();
    const url = info.linkUrl || tab?.url || '';
    const title = tab?.title || '';
    const result = await client.request('/api/browser-assistant/save-to-reading', {
      method: 'POST',
      payload: {
        title,
        url,
        source: 'browser_bookmark',
        contentKind: 'bookmark',
        externalId: url,
        tags: ['browser', 'bookmark'],
      },
      requestedBase: stored,
    });
    const savedTitle = result?.json?.title || title || '书签';
    notifyUser('已加入书签', `「${savedTitle}」已存入思忆集阅读列表。`);
  } catch (error) {
    const message = String(error?.message || error || '未知错误');
    notifyUser('加入书签失败', message.slice(0, 120));
  }
}

// ── Helpers ─────────────────────────────────────────────────────────

let _bridgeClientCache = null;

function getBridgeClient() {
  if (_bridgeClientCache) return _bridgeClientCache;
  if (!self.BridgeAuth || !self.BridgeUtils) return null;
  _bridgeClientCache = self.BridgeAuth.createBridgeAuthClient({
    readStorage: (keys) =>
      new Promise((resolve) => chrome.storage.local.get(keys, resolve)),
    writeStorage: (entries) =>
      new Promise((resolve) => chrome.storage.local.set(entries, () => resolve())),
    fetchImpl: (input, init) => fetch(input, init),
    buildBridgeCandidates: self.BridgeUtils.buildBridgeCandidates,
    normalizeBridgeBase: self.BridgeUtils.normalizeBridgeBase,
    withTimeout: self.BridgeUtils.withTimeout,
    timeoutMs: self.BridgeUtils.DEFAULT_TIMEOUT_MS,
    runtimeExtensionId: chrome.runtime.id,
  });
  return _bridgeClientCache;
}

async function loadBridgeBase() {
  const DEFAULTS = self.BridgeUtils?.DEFAULT_BRIDGE_BASE || 'http://127.0.0.1:18766';
  return new Promise((resolve) => {
    chrome.storage.local.get(['bridgeBase'], (data) => {
      const raw = String(data?.bridgeBase || DEFAULTS);
      const { migrated, base } = self.BridgeUtils
        ? self.BridgeUtils.migrateLegacyBridgeBase(raw)
        : { migrated: false, base: raw };
      if (migrated) {
        chrome.storage.local.set({ bridgeBase: base }, () => {});
      }
      resolve(base);
    });
  });
}

function notifyUser(title, message) {
  try {
    if (!chrome.notifications?.create) return;
    chrome.notifications.create({
      type: 'basic',
      iconUrl: chrome.runtime.getURL('icons/icon48.png'),
      title: String(title || '思忆集'),
      message: String(message || ''),
      priority: 1,
    });
  } catch (error) {
    console.debug('notifyUser failed', error);
  }
}

// ── Subtitle fetch relay ──────────────────────────────────────────────
// Content scripts cannot bypass CORS; the service worker can use
// host_permissions to call any origin.  Two message types:
//   FETCH_BILIBILI_SUBTITLE  – Player API flow  (bvid+cid → text)
//   FETCH_SUBTITLE_JSON      – generic subtitle URL fetch  (urls → text)

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message !== 'object') {
    return;
  }
  if (message.type === 'FETCH_BILIBILI_SUBTITLE') {
    handleBilibiliSubtitleFetch(message)
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error), text: '' }));
    return true;
  }
  if (message.type === 'FETCH_SUBTITLE_JSON') {
    handleSubtitleJsonFetch(message.urls)
      .then((result) => sendResponse(result))
      .catch((error) => sendResponse({ ok: false, error: String(error?.message || error), text: '' }));
    return true;
  }
});

async function handleBilibiliSubtitleFetch({ bvid, cid }) {
  if (!bvid) {
    return { ok: false, error: 'missing_bvid', text: '' };
  }
  let resolvedCid = String(cid || '').trim();

  // Auto-resolve cid via View API when not provided by content script
  if (!resolvedCid) {
    try {
      const viewUrl = `https://api.bilibili.com/x/web-interface/view?bvid=${encodeURIComponent(bvid)}`;
      const viewResp = await fetch(viewUrl, { credentials: 'include' });
      if (viewResp.ok) {
        const viewData = await viewResp.json();
        if (viewData?.code === 0 && viewData?.data) {
          resolvedCid = String(viewData.data.cid || '') ||
                        String((viewData.data.pages || [])[0]?.cid || '');
        }
      }
    } catch (_viewError) {
      // fall through — will return cid_not_resolved below
    }
    if (!resolvedCid) {
      return { ok: false, error: 'cid_not_resolved', text: '' };
    }
  }

  try {
    const playerUrl = `https://api.bilibili.com/x/player/wbi/v2?bvid=${encodeURIComponent(bvid)}&cid=${encodeURIComponent(resolvedCid)}`;
    const playerResp = await fetch(playerUrl, { credentials: 'include' });
    if (!playerResp.ok) {
      return { ok: false, error: `player_api_http_${playerResp.status}`, text: '' };
    }
    const playerData = await playerResp.json();

    // Check API-level error code (B站 returns code:0 on success)
    if (playerData?.code !== undefined && playerData.code !== 0) {
      return {
        ok: false,
        error: `player_api_code_${playerData.code}`,
        text: '',
        apiMessage: String(playerData?.message || ''),
      };
    }

    const subtitles = playerData?.data?.subtitle?.subtitles;
    if (!Array.isArray(subtitles) || subtitles.length === 0) {
      return { ok: true, text: '', subtitleCount: 0, status: 'no_subtitle_available' };
    }
    const preferred = subtitles.find((s) => /^zh/.test(s.lan || '')) || subtitles[0];
    let subtitleUrl = String(preferred.subtitle_url || '').trim();
    if (subtitleUrl.startsWith('//')) {
      subtitleUrl = `https:${subtitleUrl}`;
    }
    if (!subtitleUrl) {
      return { ok: true, text: '', subtitleCount: subtitles.length, status: 'no_usable_url' };
    }
    const subResp = await fetch(subtitleUrl);
    if (!subResp.ok) {
      return { ok: false, error: `subtitle_fetch_http_${subResp.status}`, text: '', subtitleUrl };
    }
    const subData = await subResp.json();
    const body = Array.isArray(subData?.body) ? subData.body : [];
    const lines = body
      .map((item) => String(item?.content || item?.text || '').trim())
      .filter(Boolean);
    const text = lines.join('\n');
    return {
      ok: true,
      text,
      subtitleUrl,
      subtitleCount: subtitles.length,
      lan: preferred.lan || '',
      lanDoc: preferred.lan_doc || '',
      status: text ? 'api_text_ready' : 'api_empty_body',
    };
  } catch (error) {
    return { ok: false, error: String(error?.message || error), text: '' };
  }
}

async function handleSubtitleJsonFetch(urls) {
  const values = Array.isArray(urls) ? urls.filter(Boolean) : [];
  for (const url of values) {
    try {
      const resp = await fetch(url);
      if (!resp.ok) {
        continue;
      }
      const data = await resp.json();
      const body = Array.isArray(data?.body) ? data.body : [];
      const lines = body
        .map((item) => String(item?.content || item?.text || '').trim())
        .filter(Boolean);
      const text = lines.join('\n');
      if (text) {
        return { ok: true, text, sourceUrl: url };
      }
    } catch (_error) {
      // try next url
    }
  }
  return { ok: true, text: '', sourceUrl: '' };
}
