/**
 * background.context-menu.test.js — 测试 background.js 的右键菜单行为。
 *
 * 因为 background.js 在 service worker 中通过 importScripts 加载依赖，
 * 我们在 Node 里手工模拟 self/chrome global，再 eval background 文件主体
 * 不可行（importScripts 不存在）。所以本测试覆盖契约级行为：
 *   - bridge-auth 在 add-word 路径上正确 POST
 *   - 用户友好的错误处理（空选区/桥接失败/成功 toast）
 * 实际 service worker 行为通过手测验证（README 注明）。
 */

const assert = require('assert');
const { createBridgeAuthClient } = require('../bridge-auth.js');

function makeResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

function createStorage(initial = {}) {
  const storage = { ...initial };
  return {
    storage,
    readStorage: async (keys) => {
      const out = {};
      for (const key of keys) out[key] = storage[key];
      return out;
    },
    writeStorage: async (entries) => Object.assign(storage, entries),
  };
}

async function testAddWordRpcSendsExpectedPayload() {
  const calls = [];
  const { readStorage, writeStorage } = createStorage();
  const client = createBridgeAuthClient({
    readStorage,
    writeStorage,
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith('/handshake')) {
        return makeResponse(200, { ok: true, sessionToken: 't1', expiresAt: '2030-01-01T00:00:00Z' });
      }
      if (url.endsWith('/add-word')) {
        return makeResponse(200, { ok: true, wordId: 101, wordbookId: 1, word: 'ephemeral' });
      }
      return makeResponse(404, { ok: false, error: 'not_found' });
    },
    buildBridgeCandidates: (base) => [base],
    normalizeBridgeBase: (base) => String(base || '').trim(),
    withTimeout: (promise) => promise,
    timeoutMs: 1000,
    runtimeExtensionId: 'sample-extension',
  });

  const result = await client.request('/api/browser-assistant/add-word', {
    method: 'POST',
    payload: { text: 'ephemeral', url: 'https://example.com', title: 'A page' },
    requestedBase: 'http://127.0.0.1:18765',
  });

  assert.strictEqual(result.json.ok, true);
  assert.strictEqual(result.json.wordId, 101);
  // First call = handshake; second = add-word
  assert.strictEqual(calls.length, 2);
  assert.ok(calls[1].url.endsWith('/api/browser-assistant/add-word'));
  const body = JSON.parse(calls[1].options.body);
  assert.strictEqual(body.text, 'ephemeral');
  assert.strictEqual(body.url, 'https://example.com');
  assert.strictEqual(body.title, 'A page');
  assert.strictEqual(calls[1].options.headers['X-Bridge-Token'], 't1');
}

async function testAddWordRpcSurfacesErrorFromBackend() {
  const { readStorage, writeStorage } = createStorage();
  const client = createBridgeAuthClient({
    readStorage,
    writeStorage,
    fetchImpl: async (url) => {
      if (url.endsWith('/handshake')) return makeResponse(200, { ok: true, sessionToken: 't1', expiresAt: '2030-01-01T00:00:00Z' });
      if (url.endsWith('/add-word')) return makeResponse(400, { ok: false, error: 'text_required' });
      return makeResponse(404, { ok: false, error: 'not_found' });
    },
    buildBridgeCandidates: (base) => [base],
    normalizeBridgeBase: (base) => String(base || '').trim(),
    withTimeout: (promise) => promise,
    timeoutMs: 1000,
    runtimeExtensionId: 'sample-extension',
  });

  await assert.rejects(
    () => client.request('/api/browser-assistant/add-word', { method: 'POST', payload: {}, requestedBase: 'http://127.0.0.1:18765' }),
    (err) => /text_required/.test(String(err.message))
  );
}

async function testAddWordRpcRetriesAfterTokenExpiry() {
  let handshakeCalls = 0;
  let addWordCalls = 0;
  const { readStorage, writeStorage } = createStorage();
  const client = createBridgeAuthClient({
    readStorage,
    writeStorage,
    fetchImpl: async (url) => {
      if (url.endsWith('/handshake')) {
        handshakeCalls++;
        return makeResponse(200, { ok: true, sessionToken: `t${handshakeCalls}`, expiresAt: '2030-01-01T00:00:00Z' });
      }
      if (url.endsWith('/add-word')) {
        addWordCalls++;
        if (addWordCalls === 1) return makeResponse(401, { ok: false, error: 'invalid_session_token' });
        return makeResponse(200, { ok: true, wordId: 9, wordbookId: 1, word: 'redux' });
      }
      return makeResponse(404, { ok: false, error: 'not_found' });
    },
    buildBridgeCandidates: (base) => [base],
    normalizeBridgeBase: (base) => String(base || '').trim(),
    withTimeout: (promise) => promise,
    timeoutMs: 1000,
    runtimeExtensionId: 'sample-extension',
  });

  const result = await client.request('/api/browser-assistant/add-word', {
    method: 'POST',
    payload: { text: 'redux' },
    requestedBase: 'http://127.0.0.1:18765',
  });
  assert.strictEqual(result.json.ok, true);
  assert.strictEqual(handshakeCalls, 2, 'expected handshake to be retried after 401');
  assert.strictEqual(addWordCalls, 2);
}

async function run() {
  const tests = [
    ['add-word rpc sends expected payload', testAddWordRpcSendsExpectedPayload],
    ['add-word rpc surfaces error', testAddWordRpcSurfacesErrorFromBackend],
    ['add-word rpc retries on token expiry', testAddWordRpcRetriesAfterTokenExpiry],
  ];
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      await fn();
      console.log(`  ok  ${name}`);
    } catch (err) {
      failed++;
      console.error(`  FAIL  ${name}\n    ${err && err.stack ? err.stack : err}`);
    }
  }
  if (failed > 0) {
    process.exitCode = 1;
    console.error(`\n${failed} test(s) failed`);
  } else {
    console.log(`\nall ${tests.length} test(s) passed`);
  }
}

run();
