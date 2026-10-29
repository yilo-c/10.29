const assert = require('assert');
const { createBridgeAuthClient, STORAGE_KEY } = require('../bridge-auth.js');

function makeResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

function createStorage() {
  const storage = {};
  return {
    storage,
    readStorage: async (keys) => {
      const response = {};
      for (const key of keys) {
        response[key] = storage[key];
      }
      return response;
    },
    writeStorage: async (payload) => {
      Object.assign(storage, payload);
    },
  };
}

async function testRequestPerformsHandshakeAndStoresToken() {
  const calls = [];
  const { storage, readStorage, writeStorage } = createStorage();
  const client = createBridgeAuthClient({
    readStorage,
    writeStorage,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith('/api/browser-assistant/handshake')) {
        return makeResponse(200, { ok: true, sessionToken: 'token-1', expiresAt: '2026-04-07T12:00:00Z' });
      }
      return makeResponse(200, { ok: true, bridge: { running: true } });
    },
    buildBridgeCandidates: (base) => [base],
    normalizeBridgeBase: (base) => String(base || '').trim(),
    withTimeout: (promise) => promise,
    timeoutMs: 1000,
    runtimeExtensionId: 'sample-extension',
  });

  const result = await client.request('/api/browser-assistant/diagnostics', { requestedBase: 'http://127.0.0.1:18765' });

  assert.strictEqual(result.json.bridge.running, true);
  assert.strictEqual(calls.length, 2);
  assert.ok(calls[0].url.endsWith('/api/browser-assistant/handshake'));
  assert.strictEqual(JSON.parse(calls[0].options.body).extensionId, 'sample-extension');
  assert.strictEqual(calls[1].options.headers['X-Bridge-Token'], 'token-1');
  assert.strictEqual(storage[STORAGE_KEY].sessionToken, 'token-1');
}

async function testRequestRetriesAfterTokenFailure() {
  const calls = [];
  const { readStorage, writeStorage } = createStorage();
  let diagnosticsCalls = 0;
  const client = createBridgeAuthClient({
    readStorage,
    writeStorage,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith('/api/browser-assistant/handshake')) {
        const token = calls.filter((item) => item.url.endsWith('/api/browser-assistant/handshake')).length === 1 ? 'token-old' : 'token-new';
        return makeResponse(200, { ok: true, sessionToken: token, expiresAt: '2026-04-07T12:00:00Z' });
      }
      diagnosticsCalls += 1;
      if (diagnosticsCalls === 1) {
        return makeResponse(401, { ok: false, error: 'invalid_session_token' });
      }
      assert.strictEqual(options.headers['X-Bridge-Token'], 'token-new');
      return makeResponse(200, { ok: true, bridge: { running: true } });
    },
    buildBridgeCandidates: (base) => [base],
    normalizeBridgeBase: (base) => String(base || '').trim(),
    withTimeout: (promise) => promise,
    timeoutMs: 1000,
    runtimeExtensionId: 'sample-extension',
  });

  const result = await client.request('/api/browser-assistant/diagnostics', { requestedBase: 'http://127.0.0.1:18765' });

  assert.strictEqual(result.json.bridge.running, true);
  assert.strictEqual(diagnosticsCalls, 2);
  assert.strictEqual(calls.filter((item) => item.url.endsWith('/api/browser-assistant/handshake')).length, 2);
}

async function testExpiredStoredSessionTriggersFreshHandshake() {
  const calls = [];
  const { storage, readStorage, writeStorage } = createStorage();
  storage[STORAGE_KEY] = {
    base: 'http://127.0.0.1:18765',
    extensionId: 'sample-extension',
    sessionToken: 'expired-token',
    expiresAt: '2000-01-01T00:00:00Z',
  };
  const client = createBridgeAuthClient({
    readStorage,
    writeStorage,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith('/api/browser-assistant/handshake')) {
        return makeResponse(200, { ok: true, sessionToken: 'token-fresh', expiresAt: '2999-01-01T00:00:00Z' });
      }
      assert.strictEqual(options.headers['X-Bridge-Token'], 'token-fresh');
      return makeResponse(200, { ok: true, bridge: { running: true } });
    },
    buildBridgeCandidates: (base) => [base],
    normalizeBridgeBase: (base) => String(base || '').trim(),
    withTimeout: (promise) => promise,
    timeoutMs: 1000,
    runtimeExtensionId: 'sample-extension',
  });

  await client.request('/api/browser-assistant/diagnostics', { requestedBase: 'http://127.0.0.1:18765' });

  assert.strictEqual(calls.filter((item) => item.url.endsWith('/api/browser-assistant/handshake')).length, 1);
}

async function testConcurrentRequestsShareSingleHandshake() {
  const { readStorage, writeStorage } = createStorage();
  let handshakeCalls = 0;
  const client = createBridgeAuthClient({
    readStorage,
    writeStorage,
    fetchImpl: async (url, options) => {
      if (url.endsWith('/api/browser-assistant/handshake')) {
        handshakeCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 20));
        return makeResponse(200, { ok: true, sessionToken: 'shared-token', expiresAt: '2999-01-01T00:00:00Z' });
      }
      assert.strictEqual(options.headers['X-Bridge-Token'], 'shared-token');
      return makeResponse(200, { ok: true, bridge: { running: true } });
    },
    buildBridgeCandidates: (base) => [base],
    normalizeBridgeBase: (base) => String(base || '').trim(),
    withTimeout: (promise) => promise,
    timeoutMs: 1000,
    runtimeExtensionId: 'sample-extension',
  });

  await Promise.all([
    client.request('/api/browser-assistant/diagnostics', { requestedBase: 'http://127.0.0.1:18765' }),
    client.request('/api/browser-assistant/diagnostics', { requestedBase: 'http://127.0.0.1:18765' }),
  ]);

  assert.strictEqual(handshakeCalls, 1);
}

async function run() {
  await testRequestPerformsHandshakeAndStoresToken();
  await testRequestRetriesAfterTokenFailure();
  await testExpiredStoredSessionTriggersFreshHandshake();
  await testConcurrentRequestsShareSingleHandshake();
  console.log('bridge-auth sample tests passed');
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});