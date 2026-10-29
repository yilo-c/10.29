(function (global) {
  const STORAGE_KEY = 'bridgeAuthSession';
  const EXPIRY_SKEW_MS = 30000;
  let inFlightHandshakePromise = null;

  function normalizeStoredSession(value) {
    if (!value || typeof value !== 'object') {
      return null;
    }
    const sessionToken = String(value.sessionToken || '').trim();
    const base = String(value.base || '').trim();
    const extensionId = String(value.extensionId || '').trim();
    if (!sessionToken || !base || !extensionId) {
      return null;
    }
    return {
      sessionToken,
      base,
      extensionId,
      expiresAt: String(value.expiresAt || '').trim(),
    };
  }

  function createBridgeAuthClient(options) {
    const readStorage = options.readStorage;
    const writeStorage = options.writeStorage;
    const fetchImpl = options.fetchImpl;
    const buildBridgeCandidates = options.buildBridgeCandidates;
    const normalizeBridgeBase = options.normalizeBridgeBase;
    const withTimeout = options.withTimeout;
    const timeoutMs = Number(options.timeoutMs || 6000);
    const runtimeExtensionId = options.runtimeExtensionId;

    function getExtensionId() {
      const explicitId = String(runtimeExtensionId || '').trim();
      if (explicitId) {
        return explicitId;
      }
      return String(global.chrome?.runtime?.id || '').trim();
    }

    async function loadStoredSession() {
      const stored = await readStorage([STORAGE_KEY]);
      return normalizeStoredSession(stored[STORAGE_KEY]);
    }

    async function persistSession(session) {
      const normalized = normalizeStoredSession(session);
      await writeStorage({ [STORAGE_KEY]: normalized });
      return normalized;
    }

    async function clearSession() {
      await writeStorage({ [STORAGE_KEY]: null });
    }

    function isSessionExpired(session) {
      const expiresAt = String(session?.expiresAt || '').trim();
      if (!expiresAt) {
        return false;
      }
      const expiresAtMs = new Date(expiresAt).getTime();
      if (!Number.isFinite(expiresAtMs)) {
        return false;
      }
      return Date.now() >= expiresAtMs - EXPIRY_SKEW_MS;
    }

    async function parseJsonResponse(response) {
      try {
        return await response.json();
      } catch (_error) {
        throw new Error('桌面端没有返回有效的 JSON 结果。');
      }
    }

    async function performHandshake(base) {
      const extensionId = getExtensionId();
      if (!extensionId) {
        throw new Error('extension_id_unavailable');
      }
      const response = await withTimeout(
        fetchImpl(`${base}/api/browser-assistant/handshake`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ extensionId }),
        }),
        timeoutMs,
        `桌面桥接请求超时：${base}`
      );
      const json = await parseJsonResponse(response);
      if (!response.ok || !json.ok || !json.sessionToken) {
        const error = new Error(json.error || `桌面端请求失败 (${response.status})`);
        error.responseStatus = response.status;
        error.responsePayload = json;
        throw error;
      }
      return persistSession({
        base,
        extensionId,
        sessionToken: json.sessionToken,
        expiresAt: json.expiresAt || '',
      });
    }

    async function refreshSession(candidates) {
      if (inFlightHandshakePromise) {
        return inFlightHandshakePromise;
      }
      const promise = (async () => {
        await clearSession();
        let lastError = null;
        for (const candidate of candidates) {
          try {
            return await performHandshake(candidate);
          } catch (error) {
            lastError = error;
          }
        }
        throw lastError || new Error('bridge_handshake_failed');
      })();
      inFlightHandshakePromise = promise;
      try {
        return await promise;
      } finally {
        if (inFlightHandshakePromise === promise) {
          inFlightHandshakePromise = null;
        }
      }
    }

    async function ensureSession(requestedBase, forceRefresh = false) {
      const normalizedBase = normalizeBridgeBase(requestedBase);
      const extensionId = getExtensionId();
      const candidates = buildBridgeCandidates(normalizedBase);
      if (inFlightHandshakePromise && !forceRefresh) {
        const pendingSession = await inFlightHandshakePromise;
        if (
          pendingSession
          && pendingSession.extensionId === extensionId
          && candidates.includes(normalizeBridgeBase(pendingSession.base))
          && !isSessionExpired(pendingSession)
        ) {
          return pendingSession;
        }
      }
      const storedSession = forceRefresh ? null : await loadStoredSession();
      if (
        storedSession
        && storedSession.extensionId === extensionId
        && candidates.includes(normalizeBridgeBase(storedSession.base))
        && !isSessionExpired(storedSession)
      ) {
        return storedSession;
      }
      return refreshSession(candidates);
    }

    function createRequestOptions(method, payload, sessionToken) {
      const headers = {};
      if (method === 'POST') {
        headers['Content-Type'] = 'application/json';
      }
      if (sessionToken) {
        headers['X-Bridge-Token'] = sessionToken;
      }
      return {
        method,
        headers,
        body: method === 'POST' ? JSON.stringify(payload || {}) : undefined,
      };
    }

    function isRetryableAuthError(error) {
      const message = String(error?.message || '').trim();
      const status = Number(error?.responseStatus || 0);
      // origin_rejected (403): 后端重启后 _allowed_extension_ids 被清空，
      // 需要重新握手让后端重新注册扩展 ID
      // invalid_session_token / token_expired (401): token 失效，需要重新握手
      return (
        message === 'invalid_session_token' ||
        message === 'token_expired' ||
        message === 'origin_rejected' ||
        status === 401 ||
        status === 403
      );
    }

    async function request(path, { method = 'GET', payload = null, requestedBase, allowRetry = true } = {}) {
      const session = await ensureSession(requestedBase, false);
      try {
        const response = await withTimeout(
          fetchImpl(`${session.base}${path}`, createRequestOptions(method, payload, session.sessionToken)),
          timeoutMs,
          `桌面桥接请求超时：${session.base}`
        );
        const json = await parseJsonResponse(response);
        if (!response.ok || !json.ok) {
          const error = new Error(json.error || `桌面端请求失败 (${response.status})`);
          error.responseStatus = response.status;
          error.responsePayload = json;
          throw error;
        }
        return {
          json,
          base: session.base,
          requestedBase: normalizeBridgeBase(requestedBase),
        };
      } catch (error) {
        if (!allowRetry || !isRetryableAuthError(error)) {
          throw error;
        }
        const refreshedSession = await ensureSession(requestedBase, true);
        const retryResponse = await withTimeout(
          fetchImpl(`${refreshedSession.base}${path}`, createRequestOptions(method, payload, refreshedSession.sessionToken)),
          timeoutMs,
          `桌面桥接请求超时：${refreshedSession.base}`
        );
        const retryJson = await parseJsonResponse(retryResponse);
        if (!retryResponse.ok || !retryJson.ok) {
          const retryError = new Error(retryJson.error || `桌面端请求失败 (${retryResponse.status})`);
          retryError.responseStatus = retryResponse.status;
          retryError.responsePayload = retryJson;
          throw retryError;
        }
        return {
          json: retryJson,
          base: refreshedSession.base,
          requestedBase: normalizeBridgeBase(requestedBase),
        };
      }
    }

    return {
      storageKey: STORAGE_KEY,
      clearSession,
      ensureSession,
      isSessionExpired,
      loadStoredSession,
      request,
    };
  }

  const BridgeAuth = {
    STORAGE_KEY,
    createBridgeAuthClient,
    normalizeStoredSession,
  };

  global.BridgeAuth = BridgeAuth;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = BridgeAuth;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);