/**
 * backend-route-contract.test.js — 契约门禁：插件调用的桥接路由必须真实存在。
 *
 * 背景：background.js 曾调用后端从未实现的 `/api/browser-assistant/add-word`
 * （幽灵调用 → 后端 404 → 划词入库功能全程不可用），而 mock fetch 的测试全绿，
 * 掩盖了漂移。本测试以「后端 dispatch 源码里的路由字面量」为真源做子集断言，
 * 新增调用点若后端没有对应路由会立刻失败。
 *
 * 零依赖、离线可跑：node tests/backend-route-contract.test.js
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const EXTENSION_DIR = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(EXTENSION_DIR, '..', '..');
const BRIDGE_SOURCE = path.join(REPO_ROOT, '思忆集app', 'browser', 'assistant_bridge.py');

// 插件侧发起桥接请求的文件（tests 目录自身不参与）
const PLUGIN_SOURCES = [
  'background.js',
  'bridge-auth.js',
  'bridge-utils.js',
  'content-script.js',
  'options.js',
  'sidepanel.js',
];

const ROUTE_PATTERN = /\/api\/browser-assistant\/[a-z0-9-]+/g;

function collectRoutes(text) {
  const found = new Set();
  for (const match of String(text).matchAll(ROUTE_PATTERN)) {
    found.add(match[0]);
  }
  return found;
}

function testPluginRoutesExistInBackendDispatch() {
  assert.ok(fs.existsSync(BRIDGE_SOURCE), `后端桥源码不存在：${BRIDGE_SOURCE}`);
  const bridgeSource = fs.readFileSync(BRIDGE_SOURCE, 'utf8');
  const backendRoutes = collectRoutes(bridgeSource);

  const pluginRoutes = new Set();
  for (const file of PLUGIN_SOURCES) {
    const fullPath = path.join(EXTENSION_DIR, file);
    assert.ok(fs.existsSync(fullPath), `插件源码不存在：${fullPath}`);
    for (const route of collectRoutes(fs.readFileSync(fullPath, 'utf8'))) {
      pluginRoutes.add(route);
    }
  }

  assert.ok(backendRoutes.size > 0, '未从后端源码解析出任何路由');
  assert.ok(pluginRoutes.size > 0, '未从插件源码解析出任何路由');

  const missing = [...pluginRoutes].filter((route) => !backendRoutes.has(route)).sort();
  assert.deepStrictEqual(
    missing,
    [],
    `插件调用了后端不存在的路由（幽灵调用）：${missing.join(', ')}\n后端现有路由：${[...backendRoutes].sort().join(', ')}`
  );

  console.log(`      插件路由 ${pluginRoutes.size} 个，后端路由 ${backendRoutes.size} 个，全部命中`);
}

function testAddWordRouteIsDispatchedByBackend() {
  const bridgeSource = fs.readFileSync(BRIDGE_SOURCE, 'utf8');
  // 划词入库：插件 background.js 依赖该端点到真实能力 word.add
  assert.ok(
    bridgeSource.includes('"/api/browser-assistant/add-word"'),
    '后端缺少 add-word 路由字面量'
  );
  assert.ok(
    /route == "\/api\/browser-assistant\/add-word"/.test(bridgeSource),
    'add-word 路由未进入 _dispatch_request 分发表（仅字面量存在不算接通）'
  );
}

function run() {
  const tests = [
    ['插件调用的路由 ⊆ 后端 dispatch 路由', testPluginRoutesExistInBackendDispatch],
    ['add-word 已进入后端分发表', testAddWordRouteIsDispatchedByBackend],
  ];
  let failed = 0;
  for (const [name, fn] of tests) {
    try {
      fn();
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
