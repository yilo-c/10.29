/**
 * ChatSocketManager — 兼容层。
 *
 * 历史背景：早期手机端单独维护一条聊天 WebSocket。现在所有 RPC（含流式 chat）都
 * 走 `@desk-notes/shared` 暴露的单例 `WsClient`，由 `rpcClient.ts` 管理 URL / 重连 / 心跳。
 *
 * 这里保留 `ChatSocketManager` 的公开 API，仅做两件事：
 *   1. `configure(baseUrl, token)` → 把 URL 推到全局让 shared WsClient 用新 URL；
 *   2. `connect()` → 确保 shared WsClient 已连接，并把它的状态 fan-out 给消费者。
 *
 * `send/onMessage` 在新架构里不再走这条通道（聊天通过 `DesktopAiClient` 或 `chat.send`
 * 流式 RPC），保留为 no-op 以避免破坏旧的调用方。
 */
import { configureWs, ensureWsConnected, onConnectionStatusChange, getConnectionStatus } from './rpcClient'

export type ConnectionState = 'disconnected' | 'connecting' | 'connected'

export class ChatSocketManager {
  private unsubscribeStatus: (() => void) | null = null
  private configured = false

  onMessage: ((text: string) => void) | null = null
  onConnectionChange: ((state: ConnectionState) => void) | null = null

  configure(baseUrl: string, _token: string | null): void {
    // token 由 ws_server `_verify_auth` 在 handshake 时根据 URL query 校验；
    // 这里只把 URL 推到全局，shared WsClient 会读 `globalThis.__SIYI_WS_URL__`。
    try {
      configureWs(baseUrl)
      this.configured = true
    } catch {
      this.configured = false
    }
  }

  connect(): void {
    if (!this.configured) return

    // 先把当前状态回灌，避免外部一直停留在 'disconnected'
    this.emit(getConnectionStatus())

    // 订阅后续状态变化
    if (this.unsubscribeStatus) {
      this.unsubscribeStatus()
      this.unsubscribeStatus = null
    }
    this.unsubscribeStatus = onConnectionStatusChange((s) => this.emit(s))

    ensureWsConnected()
  }

  disconnect(): void {
    // 注意：shared WsClient 是单例，被 RPC/事件共用，这里不要真正关闭它，
    // 只是把本实例的订阅撤掉、把状态当作"我不再关心"重置为 disconnected。
    if (this.unsubscribeStatus) {
      this.unsubscribeStatus()
      this.unsubscribeStatus = null
    }
    this.emit('disconnected')
  }

  send(_text: string): void {
    // No-op：聊天发送已迁移到 `DesktopAiClient.sendMessageStreaming` / `chat.send`。
  }

  private emit(state: ConnectionState): void {
    try {
      this.onConnectionChange?.(state)
    } catch {
      /* ignore */
    }
  }
}
