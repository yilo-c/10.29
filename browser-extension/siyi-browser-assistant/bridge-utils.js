(function (global) {
  const DEFAULT_BRIDGE_BASE = 'http://127.0.0.1:18766';
  const DEFAULT_TIMEOUT_MS = 6000;
  // 旧的 Bridge 端口（曾与 WebSocket 端口冲突），需要迁移到 18766
  const LEGACY_BRIDGE_PORT = '18765';
  const CURRENT_BRIDGE_PORT = '18766';

  function normalizeBridgeBase(value) {
    const raw = String(value || '').trim();
    if (!raw) {
      return DEFAULT_BRIDGE_BASE;
    }
    const withProtocol = /^[a-z]+:\/\//i.test(raw) ? raw : `http://${raw}`;
    return withProtocol.replace(/\/+$/, '');
  }

  /**
   * 将旧的 18765 端口地址迁移到 18766。
   * 返回 { migrated: boolean, base: string }。
   * 调用方应在 storage.get 后立即调用此函数，并在 migrated=true 时写回 storage。
   */
  function migrateLegacyBridgeBase(rawBase) {
    const original = String(rawBase || '').trim();
    if (!original || !original.includes(`:${LEGACY_BRIDGE_PORT}`)) {
      return { migrated: false, base: normalizeBridgeBase(original) };
    }
    // 仅替换 :18765（避免误改 18765xx 这种情况，加上边界）
    const migrated = original.replace(
      new RegExp(`:${LEGACY_BRIDGE_PORT}(\\b|/|$)`),
      `:${CURRENT_BRIDGE_PORT}$1`
    );
    return { migrated: true, base: normalizeBridgeBase(migrated) };
  }

  function buildBridgeCandidates(base) {
    const normalized = normalizeBridgeBase(base);
    const candidates = [normalized];
    try {
      const url = new URL(normalized);
      if (url.hostname === '127.0.0.1' || url.hostname === 'localhost') {
        const fallbackHost = url.hostname === '127.0.0.1' ? 'localhost' : '127.0.0.1';
        const fallback = `${url.protocol}//${fallbackHost}${url.port ? `:${url.port}` : ''}`;
        if (!candidates.includes(fallback)) {
          candidates.push(fallback);
        }
      }
    } catch (_error) {
      return candidates;
    }
    return candidates;
  }

  function withTimeout(promise, timeoutMs, timeoutMessage) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        reject(new Error(timeoutMessage || 'request_timed_out'));
      }, timeoutMs);
      promise
        .then((value) => {
          clearTimeout(timer);
          resolve(value);
        })
        .catch((error) => {
          clearTimeout(timer);
          reject(error);
        });
    });
  }

  function formatCheckedAt(value) {
    if (!value) {
      return '未知时间';
    }
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) {
      return '未知时间';
    }
    return date.toLocaleString('zh-CN', {
      hour12: false,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  /**
   * 把桥接失败信息翻译成用户可读的状态 + 处置建议。
   * @param {string} message 后端 error code 或异常文本
   * @param {string} base 桥接地址
   * @param {string} [extensionId] 本扩展 ID（白名单类错误时用于给出可直接复制的 ID）
   */
  function describeBridgeFailure(message, base, extensionId) {
    const normalized = String(message || '').trim();
    const selfId = String(extensionId || '').trim();
    if (!normalized) {
      return {
        tone: 'error',
        statusText: '桌面端连接失败',
        helpText: `请确认桌面端已启动，且桥接地址可访问：${normalizeBridgeBase(base)}`,
      };
    }
    const lower = normalized.toLowerCase();
    if (
      normalized === 'extension_whitelist_required_in_stable' ||
      normalized === 'extension_whitelist_not_configured' ||
      normalized === 'extension_not_whitelisted'
    ) {
      // 发布态白名单闸门：后端 fail-closed，必须由用户在桌面端显式放行本扩展 ID
      return {
        tone: 'warning',
        statusText: '桌面端扩展白名单未放行',
        helpText: `桌面端（发布态）要求扩展 ID 在受信白名单内，当前被拒绝：${normalized}。请在桌面端「设置 → 浏览器助手」把本扩展 ID${selfId ? `（${selfId}）` : ''}加入受信扩展列表后重试。`,
      };
    }
    if (lower.includes('timed out') || normalized.includes('超时')) {
      return {
        tone: 'warning',
        statusText: '桌面端响应超时',
        helpText: `桥接地址 ${normalizeBridgeBase(base)} 响应超时，请确认桌面端未卡死，或在设置页检查端口。`,
      };
    }
    if (lower.includes('failed to fetch') || lower.includes('networkerror') || lower.includes('load failed')) {
      return {
        tone: 'error',
        statusText: '桌面端未启动',
        helpText: `当前无法连接 ${normalizeBridgeBase(base)}。请先启动思忆集桌面端，再重新诊断。`,
      };
    }
    if (lower.includes('json') || normalized.includes('invalid_json')) {
      return {
        tone: 'warning',
        statusText: '桌面端返回异常',
        helpText: `桥接地址 ${normalizeBridgeBase(base)} 已响应，但返回内容不可识别。请确认桌面端版本与扩展版本匹配。`,
      };
    }
    if (lower.includes('404') || normalized.includes('not_found')) {
      return {
        tone: 'warning',
        statusText: '桥接接口不存在',
        helpText: `桥接地址 ${normalizeBridgeBase(base)} 可访问，但缺少浏览器助手接口。请升级桌面端。`,
      };
    }
    if (normalized === 'origin_rejected' || normalized === 'invalid_session_token' || normalized === 'token_expired' || lower.includes('鉴权')) {
      return {
        tone: 'warning',
        statusText: '桌面端鉴权失败',
        helpText: `桌面端拒绝了扩展访问：${normalized}。请确认桌面端与浏览器扩展版本匹配，并重新打开侧栏。`,
      };
    }
    return {
      tone: 'error',
      statusText: '桌面端请求失败',
      helpText: `桌面连接失败：${normalized}`,
    };
  }

  function buildBridgeHistoryEntry({ ok, base, statusText, helpText, checkedAt }) {
    return {
      ok: Boolean(ok),
      base: normalizeBridgeBase(base),
      statusText: String(statusText || '').trim() || '状态未知',
      helpText: String(helpText || '').trim(),
      checkedAt: checkedAt || new Date().toISOString(),
    };
  }

  const BridgeUtils = {
    DEFAULT_BRIDGE_BASE,
    DEFAULT_TIMEOUT_MS,
    LEGACY_BRIDGE_PORT,
    CURRENT_BRIDGE_PORT,
    normalizeBridgeBase,
    migrateLegacyBridgeBase,
    buildBridgeCandidates,
    withTimeout,
    formatCheckedAt,
    describeBridgeFailure,
    buildBridgeHistoryEntry,
  };

  global.BridgeUtils = BridgeUtils;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BridgeUtils;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);