/**
 * P2-10: WsClient 心跳 ping/pong + 死连检测回归测试。
 *
 * 旧实现没有心跳，TCP 静默断连时 UI 仍显示"已连接"。修复后：
 * - onopen 后周期性发送 `ping` JSON-RPC
 * - 超时未收到响应则强制关闭连接，触发 onclose → 重连
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { WsClient } from '../ws-client'

class FakeWebSocket {
  static OPEN = 1
  static CLOSED = 3
  readyState = FakeWebSocket.OPEN
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
}

let savedWebSocket: typeof WebSocket | undefined
let lastSocket: FakeWebSocket | null = null

beforeEach(() => {
  savedWebSocket = (globalThis as { WebSocket?: typeof WebSocket }).WebSocket
  lastSocket = null
  ;(globalThis as { WebSocket: unknown }).WebSocket = function (this: FakeWebSocket) {
    const s = new FakeWebSocket()
    lastSocket = s
    setTimeout(() => s.onopen?.({} as Event), 0)
    return s
  } as unknown as typeof WebSocket
  ;(globalThis as { WebSocket: { OPEN: number; CLOSED: number } }).WebSocket.OPEN = FakeWebSocket.OPEN
  ;(globalThis as { WebSocket: { OPEN: number; CLOSED: number } }).WebSocket.CLOSED = FakeWebSocket.CLOSED
  vi.useFakeTimers()
})

afterEach(() => {
  vi.useRealTimers()
  if (savedWebSocket) {
    (globalThis as { WebSocket: typeof WebSocket }).WebSocket = savedWebSocket
  }
})

describe('WsClient heartbeat', () => {
  it('sends ping after heartbeatIntervalMs once connected', async () => {
    const client = new WsClient({
      url: 'ws://test',
      heartbeatIntervalMs: 100,
      heartbeatTimeoutMs: 0, // 关闭死连检测，只看是否发心跳
    })
    client.connect()
    await vi.advanceTimersByTimeAsync(1) // 触发 onopen
    expect(lastSocket).not.toBeNull()
    const initialMsgCount = lastSocket!.sentMessages.length

    await vi.advanceTimersByTimeAsync(100)

    expect(lastSocket!.sentMessages.length).toBeGreaterThan(initialMsgCount)
    const last = JSON.parse(lastSocket!.sentMessages.at(-1)!)
    expect(last.method).toBe('ping')
  })

  it('does NOT send heartbeat when heartbeatIntervalMs <= 0', async () => {
    const client = new WsClient({
      url: 'ws://test',
      heartbeatIntervalMs: 0,
      heartbeatTimeoutMs: 0,
    })
    client.connect()
    await vi.advanceTimersByTimeAsync(1)
    const before = lastSocket!.sentMessages.length
    await vi.advanceTimersByTimeAsync(60_000)
    expect(lastSocket!.sentMessages.length).toBe(before)
  })

  it('forces close when pong does not arrive within heartbeatTimeoutMs', async () => {
    const client = new WsClient({
      url: 'ws://test',
      heartbeatIntervalMs: 100,
      heartbeatTimeoutMs: 50,
      maxReconnectAttempts: 1, // 防止测试中无限重连
    })
    client.connect()
    await vi.advanceTimersByTimeAsync(1) // onopen
    expect(client.status).toBe('connected')

    // 推进到第一次 ping 发出 + 超时
    await vi.advanceTimersByTimeAsync(100)
    expect(lastSocket!.readyState).toBe(FakeWebSocket.OPEN)

    // 等到 deadline 触发
    await vi.advanceTimersByTimeAsync(50)
    expect(lastSocket!.readyState).toBe(FakeWebSocket.CLOSED)
    expect(client.status).toBe('disconnected')
  })

  it('clears deadline when pong arrives in time', async () => {
    const client = new WsClient({
      url: 'ws://test',
      heartbeatIntervalMs: 100,
      heartbeatTimeoutMs: 50,
    })
    client.connect()
    await vi.advanceTimersByTimeAsync(1)

    await vi.advanceTimersByTimeAsync(100) // 第 1 次 ping 发出
    const sent = JSON.parse(lastSocket!.sentMessages.at(-1)!)
    expect(sent.method).toBe('ping')

    // 后端立刻回 pong
    lastSocket!.onmessage?.({
      data: JSON.stringify({ jsonrpc: '2.0', id: sent.id, result: 'pong' }),
    } as MessageEvent)

    // 推过 deadline，连接应仍然 OPEN
    await vi.advanceTimersByTimeAsync(60)
    expect(lastSocket!.readyState).toBe(FakeWebSocket.OPEN)
    expect(client.status).toBe('connected')
  })

  it('stops heartbeat on disconnect()', async () => {
    const client = new WsClient({
      url: 'ws://test',
      heartbeatIntervalMs: 100,
      heartbeatTimeoutMs: 0,
    })
    client.connect()
    await vi.advanceTimersByTimeAsync(1)
    await vi.advanceTimersByTimeAsync(100) // 第 1 次 ping
    const after1 = lastSocket!.sentMessages.length

    client.disconnect()
    await vi.advanceTimersByTimeAsync(500) // 远超 100ms
    expect(lastSocket!.sentMessages.length).toBe(after1)
  })
})
