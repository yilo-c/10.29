/**
 * JSON-RPC client — WS 优先 + HTTP 回落。
 *
 * 设计:
 *   1. 默认走 shared 包的 WsClient（与桌面端共享同一长连接抽象，享有重连/心跳/事件）。
 *   2. WS 不可用（未连接、初始化失败、调用超时）时自动回落到 HTTP POST /rpc，
 *      保留旧调用点全部能继续工作。
 *   3. `configureWs(baseUrl)` 在配对/解除配对后调用一次，把 WS URL 推到 globalThis
 *      并 `resetWsClient()`，让下一次 `getWsClient()` 拿到新 URL 的单例。
 */
import { getWsClient, resetWsClient, configureWsAuthToken } from '@desk-notes/shared'
import type { TypedStreamChunk, ServerPushEvent } from '@desk-notes/shared'
import { tokenManager } from './tokenManager'

// DevicePairingSheet 解除配对时需清空 WS 认证 token（批次4 b4-m2 配对重做）
export { configureWsAuthToken }

export interface RpcResponse<T = unknown> {
  jsonrpc: string
  id: number
  result?: T
  error?: { code: number; message: string }
}

type RuntimeWithWsUrl = typeof globalThis & {
  __SIYI_WS_URL__?: string
}

let lastConfiguredUrl: string | null = null

/** 推导出 WebSocket URL（端口换成 backendPort，scheme 升 wss）。返回 null 表示未配对。 */
function deriveWsUrl(baseUrl: string | null): string | null {
  if (!baseUrl) return null
  try {
    const urlObj = new URL(baseUrl)
    urlObj.port = String(tokenManager.backendPort)
    const wsScheme = urlObj.protocol === 'https:' ? 'wss:' : 'ws:'
    return `${wsScheme}//${urlObj.host}/ws`
  } catch {
    return null
  }
}

/**
 * 同步 WS URL 到全局并触发 WsClient 重建。配对成功 / token 变更 / 解除配对都应调用。
 * 不传参时根据 tokenManager 自动推导。
 */
export function configureWs(baseUrl?: string | null): void {
  const url = baseUrl !== undefined ? deriveWsUrl(baseUrl) : deriveWsUrl(tokenManager.baseUrl)
  if (!url) {
    lastConfiguredUrl = null
    return
  }
  if (url === lastConfiguredUrl) return
  ;(globalThis as RuntimeWithWsUrl).__SIYI_WS_URL__ = url
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem('siyi_ws_url', url)
  } catch { /* ignore */ }
  // P1-4: 把真实主 token 推给 WsClient；远端连接时自动以 ?token=<token> 形式携带
  // （浏览器/WebView 的 WS 无法带 Authorization header 或 cookie，URL 是唯一可行通道）
  configureWsAuthToken(tokenManager.apiToken)
  resetWsClient()
  lastConfiguredUrl = url
}

/** 显式启动 WS：在 App 启动时调用一次，确保 getWsClient().connect() 已发起。 */
export function ensureWsConnected(): void {
  configureWs()
  try {
    const client = getWsClient()
    if (client.status === 'disconnected') client.connect()
  } catch { /* ignore — fall back to HTTP */ }
}

function wsConnected(): boolean {
  try {
    return getWsClient().status === 'connected'
  } catch {
    return false
  }
}

/** P0.2: 无 token 时抛出明确错误，避免静默失败 / 误导性的 401。 */
export class UnauthorizedError extends Error {
  readonly statusCode = 401
  constructor(message = 'Unauthorized — pair device or set apiToken first') {
    super(message)
    this.name = 'UnauthorizedError'
  }
}

async function callViaHttp<T>(method: string, params: Record<string, unknown>): Promise<T> {
  const baseUrl = tokenManager.baseUrl
  if (!baseUrl) throw new UnauthorizedError('Backend not paired')

  // P0.2: 必须 token + 配对成功才允许走 HTTP /rpc —— 旧的匿名 LAN 路径已废弃
  const apiToken = tokenManager.apiToken
  if (!apiToken) {
    throw new UnauthorizedError('No apiToken — call pairDevice() before RPC')
  }

  const urlObj = new URL(baseUrl)
  urlObj.port = String(tokenManager.backendPort)
  const rpcUrl = `${urlObj.origin}/rpc`

  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 15_000)
  let res: Response
  try {
    res = await fetch(rpcUrl, {
      signal: controller.signal,
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${apiToken}`,
      },
      body: JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }),
    })
  } catch (err) {
    if ((err as Error)?.name === 'AbortError') throw new Error('RPC timeout after 15s')
    throw err
  } finally {
    clearTimeout(timeoutId)
  }

  // P0.2: 401 单独抛 UnauthorizedError，让 UI 能给"重新配对"的明确提示
  if (res.status === 401) {
    let detail = ''
    try {
      const body = (await res.json()) as RpcResponse<T>
      detail = body?.error?.message ?? ''
    } catch { /* ignore parse error */ }
    throw new UnauthorizedError(
      detail ? `Unauthorized (401): ${detail}` : 'Unauthorized (401) — token rejected by backend',
    )
  }

  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`RPC ${method} failed (${res.status}): ${text}`)
  }

  const json = (await res.json()) as RpcResponse<T>
  if (json.error) {
    throw new Error(`RPC ${method} error: ${json.error.message}`)
  }
  return json.result as T
}

/**
 * 调用 JSON-RPC 方法。优先 WS，断线时透明回落到 HTTP /rpc。
 */
export async function callRpc<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  // Lazy 配置 WS（如果调用方还没有调过 configureWs）
  if (!lastConfiguredUrl) configureWs()

  if (wsConnected()) {
    try {
      return await getWsClient().call<T>(method, params)
    } catch (err) {
      const msg = (err as Error)?.message ?? ''
      // 仅在显式连接异常时回落；业务错误（如 "RPC xxx error"）让调用方处理。
      if (msg.includes('Not connected') || msg.includes('WebSocket') || msg.includes('Client disconnected')) {
        return callViaHttp<T>(method, params)
      }
      throw err
    }
  }
  return callViaHttp<T>(method, params)
}

/**
 * 流式调用（仅 WS）。WS 不可用时直接抛错，调用方需要自己决定是否回退到一次性调用。
 */
export async function* callRpcStream(
  method: string,
  params: Record<string, unknown> = {},
): AsyncGenerator<TypedStreamChunk> {
  if (!lastConfiguredUrl) configureWs()
  if (!wsConnected()) {
    // 给一次机会尝试拉起 WS
    try { getWsClient().connect() } catch { /* ignore */ }
    throw new Error('WebSocket not connected — streaming requires WS transport')
  }
  yield* getWsClient().streamTyped(method, params)
}

/** 订阅服务端推送事件。返回取消订阅函数。 */
export function onEvent(eventType: string, handler: (event: ServerPushEvent) => void): () => void {
  if (!lastConfiguredUrl) configureWs()
  try {
    return getWsClient().on(eventType, handler)
  } catch {
    return () => { /* no-op */ }
  }
}

/** 监听连接状态变化（已连接 / 重连中 / 断开）。 */
export function onConnectionStatusChange(listener: (s: 'connected' | 'connecting' | 'disconnected') => void): () => void {
  try {
    return getWsClient().onStatusChange(listener)
  } catch {
    return () => { /* no-op */ }
  }
}

/** 当前 WS 连接状态。 */
export function getConnectionStatus(): 'connected' | 'connecting' | 'disconnected' {
  try { return getWsClient().status } catch { return 'disconnected' }
}

/** 桌面后端是否已连接（同步检查，仅判断 WS 状态）。 */
export function isDesktopConnected(): boolean {
  return !!tokenManager.baseUrl && wsConnected()
}

/** 向后兼容别名：同步版本，已配对且 WS 已连接。 */
export const isDesktopOnline = isDesktopConnected

/* ---------- 沙箱审批便捷函数（旧调用点保持原签名） ---------- */

export async function listPendingApprovals(): Promise<PendingApproval[]> {
  return callRpc<PendingApproval[]>('sandbox.list_pending', {})
}

export async function approveApproval(pendingId: string): Promise<{ success?: boolean; error?: string }> {
  return callRpc('sandbox.approve', { pending_id: pendingId })
}

export async function rejectApproval(pendingId: string): Promise<{ success?: boolean; error?: string }> {
  return callRpc('sandbox.reject', { pending_id: pendingId })
}

export interface PendingApproval {
  pending_id: string
  action_id: string
  status: string
  created_at: number
  preview?: {
    action?: string
    path?: string
    diff?: string
    dry_run?: boolean
  }
}
