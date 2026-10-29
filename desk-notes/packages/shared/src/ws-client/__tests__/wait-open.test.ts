/**
 * WsClient 连接建立竞态修复回归测试。
 *
 * 旧实现：getWsClient() 创建客户端并 connect()（异步握手）后立即 call()，
 * socket 尚在 CONNECTING 时直接抛 'Not connected'，导致首屏 RPC
 * （model.list 等）失败、UI 显示"无法加载"。
 *
 * 修复后：call()/stream()/streamTyped() 在 CONNECTING 状态下等待连接就绪
 * （上限 5s），连接失败才拒绝。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { WsClient } from '../ws-client'

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 3
  readyState = FakeWebSocket.CONNECTING
  sentMessages: string[] = []
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  send(msg: string) { this.sentMessages.push(msg) }
  close(code?: number, reason?: string) {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.({ code, reason } as unknown as CloseEvent)
  }
  /** 模拟握手成功 */
  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.({} as Event)
  }
  /** 模拟握手失败 */
  fail() {
    this.readyState = FakeWebSocket.CLOSED
    this.onerror?.({} as Event)
    this.onclose?.({} as unknown as CloseEvent)
  }
}

let savedWebSocket: typeof WebSocket | undefined
let lastSocket: FakeWebSocket | null = null

beforeEach(() => {
  savedWebSocket = (globalThis as { WebSocket?: typeof WebSocket }).WebSocket
  lastSocket = null
  ;(globalThis as { WebSocket: unknown }).WebSocket = function (this: FakeWebSocket) {
    const s = new FakeWebSocket()
    lastSocket = s
    return s
  } as unknown as typeof WebSocket
  ;(globalThis as { WebSocket: { OPEN: number; CLOSED: number; CONNECTING: number } }).WebSocket.OPEN = FakeWebSocket.OPEN
  ;(globalThis as { WebSocket: { OPEN: number; CLOSED: number; CONNECTING: number } }).WebSocket.CLOSED = FakeWebSocket.CLOSED
  ;(globalThis as { WebSocket: { OPEN: number; CLOSED: number; CONNECTING: number } }).WebSocket.CONNECTING = FakeWebSocket.CONNECTING
})

afterEach(() => {
  vi.useRealTimers()
  if (savedWebSocket) {
    (globalThis as { WebSocket: typeof WebSocket }).WebSocket = savedWebSocket
  }
})

describe('WsClient.call() connection race', () => {
  it('waits for the handshake and sends once connected', async () => {
    const client = new WsClient({ url: 'ws://test', heartbeatIntervalMs: 0 })
    client.connect()

    const pending = client.call<string[]>('model.list')
    // 此刻 socket 仍在 CONNECTING — 旧实现会直接抛 'Not connected'
    expect(lastSocket!.readyState).toBe(FakeWebSocket.CONNECTING)

    lastSocket!.open()
    // 等 call() 的 await 链走完（注册 pending + send）再回包
    await new Promise((r) => setTimeout(r, 0))
    expect(lastSocket!.sentMessages.length).toBe(1)
    // 模拟后端回包
    lastSocket!.onmessage?.({
      data: JSON.stringify({ jsonrpc: '2.0', id: 1, result: ['deepseek-chat'] }),
    } as unknown as MessageEvent)

    await expect(pending).resolves.toEqual(['deepseek-chat'])
  })

  it('rejects with "Not connected" when the handshake fails and client is disconnected', async () => {
    const client = new WsClient({
      url: 'ws://test',
      heartbeatIntervalMs: 0,
      reconnectInterval: 60_000, // 避免重连干扰
    })
    client.connect()

    const pending = client.call('model.list')
    lastSocket!.fail()
    // 现行语义：握手失败后自动重连期内持续等待；用户显式断开才拒绝
    client.disconnect()

    await expect(pending).rejects.toThrow('Not connected')
  })

  it('rejects immediately when connect() was never called', async () => {
    const client = new WsClient({ url: 'ws://test', heartbeatIntervalMs: 0 })
    await expect(client.call('model.list')).rejects.toThrow('Not connected')
  })

  it('gives up waiting after the connect window', async () => {
    vi.useFakeTimers()
    const client = new WsClient({ url: 'ws://test', heartbeatIntervalMs: 0 })
    client.connect()

    // 显式指定 5s 等待窗口（call 默认 30s 超时跟随调用自身）
    const pending = client.call('model.list', undefined, 5_000)
    // 先挂载断言（即挂上 rejection handler），再推时钟，避免 unhandled rejection
    const assertion = expect(pending).rejects.toThrow('Not connected')
    await vi.advanceTimersByTimeAsync(5_100)
    await assertion
  })
})
