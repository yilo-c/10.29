/**
 * AG-C2①：主进程中继（backend-relay.cjs）的 client_version 声明源。
 *
 * 渲染侧真源是 `packages/shared/src/ws-client/client-version.ts`（M2.2 第0步），
 * 但 electron 主进程为 CommonJS 且运行在 node/undici 环境，无法 import TS 模块，
 * 故在此镜像同一套解析优先级，声明串仍取自同一个 desktop/package.json version
 * （shared 侧有回归测试锁 `PACKAGE_CLIENT_VERSION === desktop/package.json version`，
 * 两侧同源即不会漂移）。
 *
 * 后端 M2.1 起：未声明 client_version 的连接 = legacy_client，其 R4 方法
 * （notes.delete / tasks.delete / inbox 写…）不再返回 {pending:true}，而是显式
 * JSON-RPC -32010 CLIENT_UPGRADE_REQUIRED。中继此前既不带握手 query 也不带帧字段，
 * 一旦后端重启，所有走中继的弹窗（翻译/悬浮问答/OCR）全线报错。
 *
 * 纯函数、无 electron 依赖，便于 vitest 直接单测（同 relay-backoff.cjs 模式）。
 */

/** 兜底版本串：desktop/package.json 读不到时使用（与 shared PACKAGE_CLIENT_VERSION 同值）。 */
const FALLBACK_CLIENT_VERSION = '1.2.0'

function _readPackageVersion() {
  try {
    const pkg = require('../package.json')
    const v = pkg && typeof pkg.version === 'string' ? pkg.version.trim() : ''
    return v || ''
  } catch {
    return ''
  }
}

/**
 * 解析中继应声明的 client_version。
 * 优先级：显式 override > 运行时全局（Electron 注入点）> package.json > 兜底常量。
 * 后端只做「非空即新客户端」的最小识别，故返回值永不为空串。
 */
function resolveClientVersion(override) {
  const candidate = String(override || '').trim()
    || String(globalThis.__SIYI_CLIENT_VERSION__ || '').trim()
    || _readPackageVersion()
    || FALLBACK_CLIENT_VERSION
  return candidate
}

/** 握手 URL 追加 client_version query（后端建连即记连接标记，首帧前也不被判 legacy）。 */
function withClientVersionQuery(url, version) {
  const v = version || resolveClientVersion()
  try {
    const u = new URL(url)
    u.searchParams.set('client_version', v)
    return u.toString()
  } catch {
    // 非法 URL：退回手工拼接，保证声明不丢（ws:// 与 wss:// 均可用 ? / & 携带 query）
    const sep = String(url).includes('?') ? '&' : '?'
    return `${url}${sep}client_version=${encodeURIComponent(v)}`
  }
}

/** 帧级声明：后端按帧升级连接标记（覆盖中继复用同一条连接的场景）。 */
function withClientVersionFrame(frame, version) {
  const v = version || resolveClientVersion()
  return { ...frame, client_version: v }
}

module.exports = {
  FALLBACK_CLIENT_VERSION,
  resolveClientVersion,
  withClientVersionQuery,
  withClientVersionFrame,
}
