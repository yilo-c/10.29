const assert = require('assert');
const {
  DEFAULT_BRIDGE_BASE,
  normalizeBridgeBase,
  migrateLegacyBridgeBase,
  buildBridgeCandidates,
  describeBridgeFailure,
  buildBridgeHistoryEntry,
} = require('../bridge-utils.js');

function testNormalizeBridgeBaseAddsProtocol() {
  // normalize 只做规范化（补协议 / 去尾部斜杠），不做端口迁移——那是 migrate 的职责
  assert.strictEqual(normalizeBridgeBase('127.0.0.1:18765/'), 'http://127.0.0.1:18765');
  assert.strictEqual(normalizeBridgeBase('localhost:18888'), 'http://localhost:18888');
}

function testMigrateLegacyBridgeBaseMigratesPort() {
  // 旧 18765 端口 → 18766（迁移收尾：调用方 storage.get 后立即迁移并写回）
  const migrated = migrateLegacyBridgeBase('http://127.0.0.1:18765');
  assert.strictEqual(migrated.migrated, true);
  assert.strictEqual(migrated.base, DEFAULT_BRIDGE_BASE);

  // 带路径的 URL 只替换端口、保留路径
  const withPath = migrateLegacyBridgeBase('http://localhost:18765/ws');
  assert.strictEqual(withPath.migrated, true);
  assert.strictEqual(withPath.base, 'http://localhost:18766/ws');

  // 已经是 18766 / 其他端口 → 不迁移
  assert.deepStrictEqual(migrateLegacyBridgeBase(DEFAULT_BRIDGE_BASE), {
    migrated: false,
    base: DEFAULT_BRIDGE_BASE,
  });
  assert.deepStrictEqual(migrateLegacyBridgeBase('http://127.0.0.1:18888'), {
    migrated: false,
    base: 'http://127.0.0.1:18888',
  });

  // 空值 → 不迁移，normalize 回退默认
  assert.deepStrictEqual(migrateLegacyBridgeBase(''), {
    migrated: false,
    base: DEFAULT_BRIDGE_BASE,
  });
}

function testBuildBridgeCandidatesProvidesLoopbackFallback() {
  assert.deepStrictEqual(buildBridgeCandidates('http://127.0.0.1:18765'), [
    'http://127.0.0.1:18765',
    'http://localhost:18765',
  ]);
  assert.deepStrictEqual(buildBridgeCandidates('http://localhost:19999'), [
    'http://localhost:19999',
    'http://127.0.0.1:19999',
  ]);
}

function testDescribeBridgeFailureProducesGuidance() {
  const timeout = describeBridgeFailure('request timed out', DEFAULT_BRIDGE_BASE);
  assert.strictEqual(timeout.statusText, '桌面端响应超时');

  const network = describeBridgeFailure('Failed to fetch', DEFAULT_BRIDGE_BASE);
  assert.strictEqual(network.statusText, '桌面端未启动');

  const invalidJson = describeBridgeFailure('invalid_json', DEFAULT_BRIDGE_BASE);
  assert.strictEqual(invalidJson.statusText, '桌面端返回异常');
}

function testBuildBridgeHistoryEntryNormalizesShape() {
  const entry = buildBridgeHistoryEntry({
    ok: true,
    base: 'localhost:18888',
    statusText: '桌面端已连接',
    helpText: '连接正常',
    checkedAt: '2026-04-03T10:00:00Z',
  });

  assert.strictEqual(entry.ok, true);
  assert.strictEqual(entry.base, 'http://localhost:18888');
  assert.strictEqual(entry.statusText, '桌面端已连接');
}

function run() {
  testNormalizeBridgeBaseAddsProtocol();
  testMigrateLegacyBridgeBaseMigratesPort();
  testBuildBridgeCandidatesProvidesLoopbackFallback();
  testDescribeBridgeFailureProducesGuidance();
  testBuildBridgeHistoryEntryNormalizesShape();
  console.log('bridge-utils sample tests passed');
}

run();