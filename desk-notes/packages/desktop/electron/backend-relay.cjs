/**
 * Main-process backend WebSocket relay for popup windows.
 *
 * Each popup BrowserWindow is a separate renderer that can't share the
 * renderer-side WsClient singleton. Instead of each popup creating its own
 * WebSocket connection (which can fail in sandboxed contexts), we maintain
 * ONE connection in the main process and relay calls via IPC.
 */
const { ipcMain } = require('electron')
// AG-C2①：中继也必须声明 client_version——弹窗走的是这条连接，
// 未声明 = 后端 M2.1 R4 判 legacy_client → notes.delete 等直接 -32010。
// 纯函数取自 client-version.cjs（与渲染侧 ws-client 同一版本串来源）。
const { resolveClientVersion, withClientVersionQuery, withClientVersionFrame } = require('./client-version.cjs')

const BACKEND_WS_URL = 'ws://127.0.0.1:18765/ws'

let _backendWs = null
let _backendCallId = 0
const _backendPendingCalls = new Map()
// V2.1 批次4：重连退避状态（失败后指数退避，避免连接风暴）
// 纯函数（computeBackoffDelayMs / shouldAttemptConnect）在 relay-backoff.cjs，
// 便于 vitest 单测覆盖。
const { computeBackoffDelayMs, shouldAttemptConnect } = require('./relay-backoff.cjs')
let _lastConnectAttempt = 0
let _consecutiveFailures = 0
// AG-C2①：本次连接握手期声明的版本串，帧级沿用同值（握手/帧一致）
let _backendClientVersion = ''

function _shouldAttemptConnect(now) {
  return shouldAttemptConnect(now, _lastConnectAttempt, _consecutiveFailures)
}

function _ensureBackendWs() {
  if (_backendWs && (_backendWs.readyState === WebSocket.OPEN || _backendWs.readyState === WebSocket.CONNECTING)) return
  if (_backendWs) { try { _backendWs.close() } catch {} _backendWs = null }

  const now = Date.now()
  if (!_shouldAttemptConnect(now)) return  // 退避期内不发起连接

  _lastConnectAttempt = now
  _backendClientVersion = resolveClientVersion()
  _backendWs = new WebSocket(withClientVersionQuery(BACKEND_WS_URL, _backendClientVersion))
  _backendWs.on('open', () => {
    console.log('[backend-relay] connected')
    _consecutiveFailures = 0  // 连接成功，重置退避
  })
  _backendWs.on('message', (data) => {
    try {
      const msg = JSON.parse(data.toString())
      if (msg.id != null && _backendPendingCalls.has(msg.id)) {
        const { resolve } = _backendPendingCalls.get(msg.id)
        _backendPendingCalls.delete(msg.id)
        resolve(msg)
      }
    } catch { /* ignore malformed */ }
  })
  _backendWs.on('close', () => {
    console.log('[backend-relay] disconnected')
    _consecutiveFailures++
    const pending = Array.from(_backendPendingCalls.entries())
    _backendPendingCalls.clear()
    for (const [, { reject }] of pending) {
      reject(new Error('Backend disconnected'))
    }
    _backendWs = null
    _backendClientVersion = ''
  })
  _backendWs.on('error', () => { /* onclose will fire */ })
}

function doSend(resolve, reject, method, params) {
  const id = ++_backendCallId
  _backendPendingCalls.set(id, { resolve, reject })
  try {
    _backendWs.send(JSON.stringify(withClientVersionFrame(
      { jsonrpc: '2.0', id, method, params: params || {} },
      _backendClientVersion || resolveClientVersion(),
    )))
  } catch (err) {
    _backendPendingCalls.delete(id)
    reject(new Error('Send failed: ' + err.message))
    return
  }
  const timeoutMs = method === 'translate.full' ? 60000 : 15000
  setTimeout(() => {
    if (_backendPendingCalls.has(id)) {
      _backendPendingCalls.delete(id)
      // 旧逻辑超时时关闭整个共享连接，连带杀死其他在途请求。
      // 改为只拒绝当前超时请求，连接保持，由后端心跳/服务端超时管理。
      reject(new Error('Backend RPC timeout: ' + method))
    }
  }, timeoutMs).unref()
}

function _sleep(ms) { return new Promise((r) => setTimeout(r, ms)) }

// P3 修复：RPC 调用时若连接未就绪（瞬时断连 / flood 后进入退避期），
// 不再立即 reject('Backend not connected')——那会把"瞬时断连"误报为后端不可用。
// 改为等待重连成功再发送，仅当连接持续不可用超过 maxWaitMs 才失败。
async function _backendRpcCall(method, params) {
  const maxWaitMs = 30_000
  const startedAt = Date.now()
  for (;;) {
    const ws = _backendWs
    if (ws && ws.readyState === WebSocket.OPEN) {
      return new Promise((resolve, reject) => doSend(resolve, reject, method, params))
    }
    if (ws && ws.readyState === WebSocket.CONNECTING) {
      // 已有连接在握手中：等它 open
      await new Promise((resolve) => {
        const onOpen = () => { ws.removeListener('open', onOpen); ws.removeListener('close', onClose); resolve() }
        const onClose = () => { ws.removeListener('open', onOpen); ws.removeListener('close', onClose); resolve() }
        ws.once('open', onOpen)
        ws.once('close', onClose)
      })
      continue
    }
    // 无连接或已断开：尝试重连（受退避期约束），轮询等待
    try { _ensureBackendWs() } catch (err) {
      if (Date.now() - startedAt >= maxWaitMs) {
        return Promise.reject(new Error('Backend not connected'))
      }
      await _sleep(300)
      continue
    }
    if (Date.now() - startedAt >= maxWaitMs) {
      return Promise.reject(new Error('Backend not connected'))
    }
    await _sleep(300)
  }
}

const _BACKEND_CALL_ALLOWLIST = new Set([
  'translate.text', 'translate.languages', 'translate.full',
  'chat.send', 'chat.history', 'chat.list_conversations', 'chat.get_conversation',
  'memory.search', 'memory.recent', 'memory.related', 'memory.related_text',
  'notes.list', 'notes.get', 'notes.create', 'notes.update', 'notes.delete', 'notes.auto_tag',
  'tasks.list', 'tasks.get', 'tasks.create', 'tasks.update', 'tasks.delete', 'tasks.toggle',
  'inbox.list', 'inbox.add', 'inbox.update',
  'web.search', 'web.fetch', 'url_learning.learn',
  'wordbook.list', 'wordbook.create', 'wordbook.rename', 'wordbook.delete',
  'word.list', 'word.add', 'word.update', 'word.delete',
  'knowledge.search',
  'settings.get', 'settings.set',
  'system.status', 'system.info',
  'model.list',
  'ping',
])

ipcMain.handle('backend:call', async (_event, method, params) => {
  if (!_BACKEND_CALL_ALLOWLIST.has(method)) {
    return { success: false, error: `RPC method not allowed: ${method}` }
  }
  try {
    const resp = await _backendRpcCall(method, params)
    if (resp.error) return { success: false, error: resp.error.message || String(resp.error) }
    return { success: true, data: resp.result }
  } catch (err) {
    return { success: false, error: err.message }
  }
})

module.exports = { _backendRpcCall, computeBackoffDelayMs, shouldAttemptConnect }
