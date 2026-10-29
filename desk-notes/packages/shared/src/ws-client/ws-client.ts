/* WebSocket JSON-RPC client implementation. */
import type { ConnectionStatus, JsonRpcRequest, JsonRpcResponse, JsonRpcStreamChunk, ServerPushEvent, StreamChunkType, TypedStreamChunk, WsClientOptions } from './types'
import { resolveClientVersion } from './client-version'

export { PACKAGE_CLIENT_VERSION, resolveClientVersion } from './client-version'

const FALLBACK_URL = 'ws://127.0.0.1:18765/ws'

type RuntimeWithWsUrl = typeof globalThis & {
  __SIYI_WS_URL__?: string
  __SIYI_WS_AUTH_TOKEN__?: string
}

function readPersistedWsUrl(): string | null {
  try {
    if (typeof localStorage === 'undefined') return null
    const value = localStorage.getItem('siyi_ws_url')
    return value?.trim() || null
  } catch {
    return null
  }
}

export function resolveDefaultWsUrl(): string {
  const runtime = globalThis as RuntimeWithWsUrl
  const configuredUrl = runtime.__SIYI_WS_URL__?.trim() || readPersistedWsUrl()
  return configuredUrl || FALLBACK_URL
}

function readPersistedWsToken(): string | null {
  try {
    if (typeof localStorage === 'undefined') return null
    const value = localStorage.getItem('siyi_ws_token')
    return value?.trim() || null
  } catch {
    return null
  }
}

type EventHandler = (event: ServerPushEvent) => void

interface StreamHandler {
  onChunk: (d: string) => void
  onDone: () => void
  onError: (e: Error) => void
  /** Optional: receives non-`chunk`/`done` typed metadata (error/tool_progress/planner/etc.). */
  onTypedChunk?: (type: StreamChunkType, data: string) => void
}

export class WsClient {
  private _ws: WebSocket | null = null
  private _status: ConnectionStatus = 'disconnected'
  private _requestId = 0
  private _pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>()
  private _streamHandlers = new Map<number, StreamHandler>()
  private _eventHandlers = new Map<string, Set<EventHandler>>()
  private _reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private _reconnectAttempts = 0
  private _maxReconnectAttempts: number
  private _reconnectInterval: number
  private _url: string
  private _authToken: string | null
  /** M2.2 第0步：连接期与帧级共用的 client_version（后端 legacy_client 判定依据）。 */
  private _clientVersion: string
  private _statusListeners = new Set<(s: ConnectionStatus) => void>()
  // Review fix #8: 独立的手动断开标志，避免 _maxReconnectAttempts=0（无限重连）
  // 时 disconnect() 设置 _reconnectAttempts 无效的问题
  private _manualDisconnect = false
  // P2-10: 心跳 ping/pong + 死连检测
  private _heartbeatIntervalMs: number
  private _heartbeatTimeoutMs: number
  private _heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private _heartbeatDeadlineTimer: ReturnType<typeof setTimeout> | null = null

  constructor(options?: WsClientOptions) {
    this._url = options?.url ?? resolveDefaultWsUrl()
    // 优先级：显式 options > 运行时全局 > localStorage 持久化。
    // 兜底读 localStorage 确保桌面端「手填远程后端 + token」在应用重启后仍生效
    // （全局 __SIYI_WS_AUTH_TOKEN__ 不跨进程持久化）。loopback 地址不会携带 token。
    this._authToken = options?.authToken ?? ((globalThis as RuntimeWithWsUrl).__SIYI_WS_AUTH_TOKEN__?.trim() || readPersistedWsToken())
    this._clientVersion = resolveClientVersion(options?.clientVersion)
    this._reconnectInterval = options?.reconnectInterval ?? 3000
    this._maxReconnectAttempts = options?.maxReconnectAttempts ?? 0 // 0 = 无限重连
    this._heartbeatIntervalMs = options?.heartbeatIntervalMs ?? 25_000
    // 15s：后端事件循环偶发繁忙（定时任务/批量 RPC）时避免误杀连接，
    // 旧值 10s 会导致在途请求（model.list/notes.update 等）随连接被强杀而失败
    this._heartbeatTimeoutMs = options?.heartbeatTimeoutMs ?? 15_000
  }

  get status(): ConnectionStatus {
    return this._status
  }

  onStatusChange(listener: (s: ConnectionStatus) => void): () => void {
    this._statusListeners.add(listener)
    return () => this._statusListeners.delete(listener)
  }

  private _setStatus(s: ConnectionStatus): void {
    this._status = s
    for (const listener of this._statusListeners) {
      try { listener(s) } catch { /* ignore */ }
    }
  }

  connect(): void {
    if (this._ws && (this._ws.readyState === WebSocket.OPEN || this._ws.readyState === WebSocket.CONNECTING)) {
      return
    }
    // Review fix #8: 用户显式调用 connect()，清除手动断开标志
    this._manualDisconnect = false
    this._setStatus('connecting')
    void this._connectWithHandshake()
  }

  /**
   * P1-4: 远端设备（手机）持真实 token 时，浏览器/WebView 的 WebSocket 无法带
   * Authorization header 或 cookie，唯一可行通道是把 token 附到 URL：
   * ``ws://host:18765/ws?token=<authToken>``。loopback（本机桌面）由服务端直接信任，
   * 无需任何 token。连接失败（1008）仍按原重连逻辑处理。
   */
  private _isLoopback(url: string): boolean {
    try {
      const h = new URL(url).hostname
      return h === '127.0.0.1' || h === '::1' || h === 'localhost'
    } catch {
      return false
    }
  }

  private _urlWithToken(): string {
    try {
      const u = new URL(this._url)
      // token 仅在远端地址附带（loopback 由服务端直接信任，带 token 反而暴露凭证）
      if (this._authToken && !this._isLoopback(this._url)) u.searchParams.set('token', this._authToken)
      // M2.2 第0步：握手 query 声明版本（后端 ws_server.py:952-955 建连即记连接标记，
      // 使首帧之前的任何调用都不被判为 legacy_client）。
      u.searchParams.set('client_version', this._clientVersion)
      return u.toString()
    } catch {
      return this._url
    }
  }

  private async _connectWithHandshake(): Promise<void> {
    const url = this._urlWithToken()

    try {
      this._ws = new WebSocket(url)
    } catch (err) {
      this._setStatus('disconnected')
      this._scheduleReconnect()
      return
    }

    this._ws.onopen = () => {
      this._setStatus('connected')
      this._reconnectAttempts = 0
      this._startHeartbeat()
    }

    this._ws.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data as string)
        this._handleMessage(data)
      } catch { /* ignore malformed */ }
    }

    this._ws.onclose = () => {
      this._setStatus('disconnected')
      this._stopHeartbeat()
      this._rejectAll(new Error('WebSocket closed'))
      this._ws = null
      this._scheduleReconnect()
    }

    this._ws.onerror = () => {
      // onclose will fire after this
    }
  }

  disconnect(): void {
    // Review fix #8: 用独立标志阻止重连，原来设置 _reconnectAttempts=_maxReconnectAttempts
    // 在 _maxReconnectAttempts=0（无限重连）时无效
    this._manualDisconnect = true
    this._cancelReconnect()
    this._stopHeartbeat()
    this._reconnectAttempts = 0
    if (this._ws) {
      this._ws.onclose = null // prevent reconnect from onclose
      this._ws.close()
      this._ws = null
    }
    this._setStatus('disconnected')
    this._rejectAll(new Error('Client disconnected'))
  }

  /**
   * 等待连接就绪：OPEN 立即返回；CONNECTING 或退避窗口中则等待到 connected。
   * 修复启动竞态 + 间歇性连接失败：旧逻辑在 socket 为 null（退避期）时直接
   * 抛 'Not connected'，导致启动后 30s 退避窗口内所有 RPC 立即失败。
   * 新逻辑：只要未手动断开，就等待重连成功（或超时）。
   */
  private _waitOpen(timeoutMs: number): Promise<void> {
    const ws = this._ws
    if (ws && ws.readyState === WebSocket.OPEN) return Promise.resolve()
    if (this._manualDisconnect) return Promise.reject(new Error('Not connected'))
    // 空闲客户端快拒：从未建立连接且无重连计划（未 connect 即调用属编程错误，
    // 避免隐式等待 30s RPC 超时才报错）
    if (!ws && !this._reconnectTimer) return Promise.reject(new Error('Not connected'))
    return new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => {
        off()
        reject(new Error('Not connected'))
      }, timeoutMs)
      const off = this.onStatusChange((s) => {
        if (s === 'connected') {
          clearTimeout(timer)
          off()
          resolve()
        } else if (s === 'disconnected' && this._manualDisconnect) {
          clearTimeout(timer)
          off()
          reject(new Error('Not connected'))
        }
        // 注意：普通 disconnected（自动重连中）不 reject，继续等待重连成功
      })
    })
  }

  async call<T = unknown>(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<T> {
    const timeout = timeoutMs ?? 30000
    // 等待连接就绪：CONNECTING 或退避期都等待重连成功（跟随调用自身超时）。
    // 旧逻辑只等 5s 握手，退避期直接抛 'Not connected'，导致启动竞态失败。
    await this._waitOpen(timeout)
    const ws = this._ws
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      throw new Error('Not connected')
    }

    const id = ++this._requestId
    const request: JsonRpcRequest = { jsonrpc: '2.0', id, method, params, client_version: this._clientVersion }

    return new Promise<T>((resolve, reject) => {
      this._pending.set(id, { resolve: resolve as (v: unknown) => void, reject })
      const timer = setTimeout(() => {
        this._pending.delete(id)
        reject(new Error(`RPC ${method} timed out after ${timeout}ms`))
      }, timeout)
      try {
        ws.send(JSON.stringify(request))
      } catch (err) {
        clearTimeout(timer)
        this._pending.delete(id)
        reject(err instanceof Error ? err : new Error(String(err)))
      }
      // Wrap resolve/reject to clear timer
      const entry = this._pending.get(id)
      if (entry) {
        this._pending.set(id, {
          resolve: (v: unknown) => { clearTimeout(timer); entry.resolve(v) },
          reject: (e: Error) => { clearTimeout(timer); entry.reject(e) },
        })
      }
    })
  }

  /**
   * Stream RPC invocation. Yields `chunk`-type data only (backward compatible).
   *
   * To receive metadata chunks (tool_progress / planner / subagent_* / etc.),
   * pass an `onTypedChunk` callback via the options bag, or use `streamTyped()`.
   */
  async *stream<T = string>(
    method: string,
    params?: Record<string, unknown>,
    options?: {
      onTypedChunk?: (type: StreamChunkType, data: string) => void
      /** 空闲超时（ms）：等待下一 chunk 超过该时长则终止流并抛错；0/未设 = 不启用。 */
      idleTimeoutMs?: number
    },
  ): AsyncGenerator<T> {
    await this._waitOpen(5000)
    const ws = this._ws
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      throw new Error('Not connected')
    }

    const id = ++this._requestId
    const request: JsonRpcRequest = { jsonrpc: '2.0', id, method, params, stream: true, client_version: this._clientVersion }

    // Buffered chunks to prevent loss when chunks arrive faster than the consumer awaits.
    const buffer: T[] = []
    let resolveChunk: ((v: IteratorResult<T>) => void) | null = null
    let rejectStream: ((e: Error) => void) | null = null
    let done = false
    let pendingError: Error | null = null

    // 空闲看门狗：仅在实际等待 chunk 期间计时，避免消费者处理耗时误触发。
    const idleMs = options?.idleTimeoutMs ?? 0
    let watchdog: ReturnType<typeof setTimeout> | null = null
    const clearWatchdog = (): void => {
      if (watchdog !== null) {
        clearTimeout(watchdog)
        watchdog = null
      }
    }

    this._streamHandlers.set(id, {
      onChunk: (data: string) => {
        if (done) return
        if (resolveChunk) {
          // Consumer is awaiting — hand it directly.
          const r = resolveChunk
          resolveChunk = null
          r({ value: data as unknown as T, done: false })
        } else {
          // No one waiting yet — queue.
          buffer.push(data as unknown as T)
        }
      },
      onDone: () => {
        done = true
        if (resolveChunk) {
          const r = resolveChunk
          resolveChunk = null
          r({ value: undefined as unknown as T, done: true })
        }
      },
      onError: (err: Error) => {
        done = true
        pendingError = err
        if (rejectStream) {
          const r = rejectStream
          rejectStream = null
          r(err)
        }
      },
      onTypedChunk: options?.onTypedChunk,
    })

    try {
      ws.send(JSON.stringify(request))
    } catch (err) {
      this._streamHandlers.delete(id)
      throw err
    }

    try {
      while (true) {
        // Drain buffered chunks first.
        if (buffer.length > 0) {
          yield buffer.shift() as T
          continue
        }
        if (done) {
          if (pendingError) throw pendingError
          return
        }
        const result = await new Promise<IteratorResult<T>>((resolve, reject) => {
          resolveChunk = resolve
          rejectStream = reject
          if (idleMs > 0) {
            watchdog = setTimeout(() => {
              done = true
              const err = new Error(`Stream ${method} idle timeout: no data for ${idleMs}ms`)
              if (rejectStream) {
                const r = rejectStream
                rejectStream = null
                r(err)
              } else {
                pendingError = err
              }
            }, idleMs)
          }
        })
        clearWatchdog()
        if (result.done) {
          if (pendingError) throw pendingError
          return
        }
        yield result.value
      }
    } finally {
      clearWatchdog()
      this._streamHandlers.delete(id)
    }
  }

  /**
   * Typed stream RPC invocation. Yields every chunk (including metadata types
   * like `tool_progress` / `planner` / `subagent_start` / `multi_agent_summary`).
   *
   * The consumer is responsible for filtering by `chunk.type` and rendering
   * appropriately. `chunk`-type entries carry the AI text delta in `data`;
   * metadata-type entries carry human-readable status text.
   */
  async *streamTyped(
    method: string,
    params?: Record<string, unknown>,
    options?: {
      /** 空闲超时（ms）：等待下一 chunk 超过该时长则终止流并抛错；0/未设 = 不启用。 */
      idleTimeoutMs?: number
    },
  ): AsyncGenerator<TypedStreamChunk> {
    await this._waitOpen(5000)
    const ws = this._ws
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      throw new Error('Not connected')
    }

    const id = ++this._requestId
    const request: JsonRpcRequest = { jsonrpc: '2.0', id, method, params, stream: true, client_version: this._clientVersion }

    const buffer: TypedStreamChunk[] = []
    let resolveChunk: ((v: IteratorResult<TypedStreamChunk>) => void) | null = null
    let rejectStream: ((e: Error) => void) | null = null
    let done = false
    let pendingError: Error | null = null

    // 空闲看门狗：仅在实际等待 chunk 期间计时，避免消费者处理耗时误触发。
    const idleMs = options?.idleTimeoutMs ?? 0
    let watchdog: ReturnType<typeof setTimeout> | null = null
    const clearWatchdog = (): void => {
      if (watchdog !== null) {
        clearTimeout(watchdog)
        watchdog = null
      }
    }

    this._streamHandlers.set(id, {
      onChunk: (data: string) => {
        if (done) return
        const typed: TypedStreamChunk = { type: 'chunk', data }
        if (resolveChunk) {
          const r = resolveChunk
          resolveChunk = null
          r({ value: typed, done: false })
        } else {
          buffer.push(typed)
        }
      },
      onTypedChunk: (type: StreamChunkType, data: string) => {
        if (done) return
        const typed: TypedStreamChunk = { type, data }
        if (resolveChunk) {
          const r = resolveChunk
          resolveChunk = null
          r({ value: typed, done: false })
        } else {
          buffer.push(typed)
        }
      },
      onDone: () => {
        done = true
        if (resolveChunk) {
          const r = resolveChunk
          resolveChunk = null
          r({ value: undefined as unknown as TypedStreamChunk, done: true })
        }
      },
      onError: (err: Error) => {
        done = true
        pendingError = err
        if (rejectStream) {
          const r = rejectStream
          rejectStream = null
          r(err)
        }
      },
    })

    try {
      ws.send(JSON.stringify(request))
    } catch (err) {
      this._streamHandlers.delete(id)
      throw err
    }

    try {
      while (true) {
        if (buffer.length > 0) {
          yield buffer.shift() as TypedStreamChunk
          continue
        }
        if (done) {
          if (pendingError) throw pendingError
          return
        }
        const result = await new Promise<IteratorResult<TypedStreamChunk>>((resolve, reject) => {
          resolveChunk = resolve
          rejectStream = reject
          if (idleMs > 0) {
            watchdog = setTimeout(() => {
              done = true
              const err = new Error(`Stream ${method} idle timeout: no data for ${idleMs}ms`)
              if (rejectStream) {
                const r = rejectStream
                rejectStream = null
                r(err)
              } else {
                pendingError = err
              }
            }, idleMs)
          }
        })
        clearWatchdog()
        if (result.done) {
          if (pendingError) throw pendingError
          return
        }
        yield result.value
      }
    } finally {
      clearWatchdog()
      this._streamHandlers.delete(id)
    }
  }

  on(eventType: string, handler: EventHandler): () => void {
    if (!this._eventHandlers.has(eventType)) {
      this._eventHandlers.set(eventType, new Set())
    }
    this._eventHandlers.get(eventType)!.add(handler)
    return () => { this._eventHandlers.get(eventType)?.delete(handler) }
  }

  /* ---- internal ---- */

  private _handleMessage(data: unknown): void {
    // Server push event
    if (typeof data === 'object' && data !== null && 'type' in data && (data as Record<string, unknown>).type === 'event') {
      this._dispatchEvent(data as ServerPushEvent)
      return
    }

    const msg = data as JsonRpcResponse | JsonRpcStreamChunk

    // Stream chunk
    if ('result' in msg && typeof msg.result === 'object' && msg.result !== null && 'type' in msg.result) {
      this._handleStreamChunk(msg as JsonRpcStreamChunk)
      return
    }

    // Standard response
    const resp = msg as JsonRpcResponse
    const pending = this._pending.get(resp.id)
    if (pending) {
      this._pending.delete(resp.id)
      if (resp.error) {
        pending.reject(new Error(resp.error.message))
      } else {
        pending.resolve(resp.result)
      }
    }
  }

  private _handleStreamChunk(msg: JsonRpcStreamChunk): void {
    const handler = this._streamHandlers.get(msg.id)
    if (!handler) return

    const type = msg.result.type
    const data = msg.result.data ?? ''

    if (type === 'chunk') {
      if (data) handler.onChunk(data)
    } else if (type === 'done') {
      handler.onDone()
      this._streamHandlers.delete(msg.id)
    } else if (type === 'error') {
      // Terminal error — surface to consumer via onError so the async generator
      // throws and UI can show the error. Previously this was silently ignored,
      // causing the stream to hang until ws close/timeout.
      handler.onError(new Error(data || 'stream error'))
      this._streamHandlers.delete(msg.id)
    } else {
      // Metadata chunks (tool_progress / tool_result / planner / subagent_*
      // / multi_agent_summary). Forward to typed callback if registered;
      // otherwise silently drop to preserve backward compatibility.
      try {
        handler.onTypedChunk?.(type, data)
      } catch {
        /* never let a typed-chunk handler crash the stream */
      }
    }
  }

  private _dispatchEvent(event: ServerPushEvent): void {
    const wildcardHandlers = this._eventHandlers.get('*')
    if (wildcardHandlers) {
      for (const h of wildcardHandlers) {
        try { h(event) } catch { /* ignore */ }
      }
    }
    const exactHandlers = this._eventHandlers.get(event.event_type)
    if (exactHandlers) {
      for (const h of exactHandlers) {
        try { h(event) } catch { /* ignore */ }
      }
    }
  }

  private _scheduleReconnect(): void {
    // Review fix #8: 检查手动断开标志，避免 disconnect() 后仍触发重连
    if (this._manualDisconnect) return
    // P1 修复：无限重连 + 指数退避（上限 5s），避免断网后永久放弃。
    // 旧值 30s 上限导致启动竞态时等待过长，表现为"有时能连有时不能"。
    if (this._maxReconnectAttempts > 0 && this._reconnectAttempts >= this._maxReconnectAttempts) return
    this._reconnectAttempts++
    const delay = Math.min(
      this._reconnectInterval * Math.pow(2, this._reconnectAttempts - 1),
      5000, // 上限 5 秒
    )
    this._reconnectTimer = setTimeout(() => {
      this.connect()
    }, delay)
  }

  private _cancelReconnect(): void {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer)
      this._reconnectTimer = null
    }
  }

  /**
   * P2-10: 心跳 — 周期性发送 `ping` JSON-RPC，若超时未收到 pong 则主动关闭连接。
   *
   * - 走标准 `call('ping')`：服务端已注册同名方法且返回 `"pong"`。
   * - `_heartbeatIntervalMs <= 0` 时整个机制关闭（用于测试或后端不支持时）。
   * - `_heartbeatTimeoutMs <= 0` 时只发心跳不做死连判定。
   */
  private _startHeartbeat(): void {
    this._stopHeartbeat()
    if (this._heartbeatIntervalMs <= 0) return
    this._heartbeatTimer = setInterval(() => {
      this._sendHeartbeat()
    }, this._heartbeatIntervalMs)
  }

  private _stopHeartbeat(): void {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer)
      this._heartbeatTimer = null
    }
    if (this._heartbeatDeadlineTimer) {
      clearTimeout(this._heartbeatDeadlineTimer)
      this._heartbeatDeadlineTimer = null
    }
  }

  private _sendHeartbeat(): void {
    const ws = this._ws
    if (!ws || ws.readyState !== WebSocket.OPEN) return

    // 启动死连截止计时器（若启用）
    if (this._heartbeatTimeoutMs > 0 && !this._heartbeatDeadlineTimer) {
      this._heartbeatDeadlineTimer = setTimeout(() => {
        // 超时未收到 pong，视作 TCP 静默断连：强制关闭，触发 onclose → 重连
        this._heartbeatDeadlineTimer = null
        this._forceCloseDeadConnection()
      }, this._heartbeatTimeoutMs)
    }

    this.call<string>('ping', undefined, this._heartbeatTimeoutMs > 0 ? this._heartbeatTimeoutMs : 30_000)
      .then(() => {
        // pong 已收到，清除死连截止
        if (this._heartbeatDeadlineTimer) {
          clearTimeout(this._heartbeatDeadlineTimer)
          this._heartbeatDeadlineTimer = null
        }
      })
      .catch(() => {
        // RPC 失败由 deadline 兜底处理（或调用方异常时无 deadline，立即标记死连）
        if (!this._heartbeatDeadlineTimer && this._heartbeatTimeoutMs > 0) {
          this._forceCloseDeadConnection()
        }
      })
  }

  private _forceCloseDeadConnection(): void {
    const ws = this._ws
    if (!ws) return
    // 不要清空 onclose — 让它走正常重连路径
    try {
      ws.close(4000, 'heartbeat timeout')
    } catch {
      /* ignore */
    }
  }

  private _rejectAll(error: Error): void {
    for (const [id, { reject }] of this._pending) {
      reject(error)
      this._pending.delete(id)
    }
    for (const [id, { onError }] of this._streamHandlers) {
      onError(error)
      this._streamHandlers.delete(id)
    }
  }
}
