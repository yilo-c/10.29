/**
 * M2.2 第0步：client_version 接线回归。
 *
 * 后端 M2.1 起，未声明 client_version 的连接 = legacy_client，其 R4 方法
 * （notes.delete / tasks.delete 等）不再回 {pending:true}，而是显式
 * -32010 CLIENT_UPGRADE_REQUIRED（判定点 method_registry.py:229-292、
 * ws_server.py:952-955 握手 query、:1153-1158 帧字段、:711 HTTP body）。
 * 本测试锁住前端两处声明点：握手 URL query + 每一帧的 client_version 字段。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { WsClient } from '../ws-client'
import { PACKAGE_CLIENT_VERSION, resolveClientVersion } from '../client-version'
import type { WsClientOptions } from '../types'

class FakeWebSocket {
  static OPEN = 1
  static CLOSED = 3
  readyState = FakeWebSocket.OPEN
  sentMessages: string[] = []
  url: string
  onopen: ((ev: Event) => void) | null = null
  onmessage: ((ev: MessageEvent) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
  constructor(url: string | URL) { this.url = String(url) }
  send(msg: string) { this.sentMessages.push(msg) }
  close() { this.readyState = FakeWebSocket.CLOSED; this.onclose?.({} as CloseEvent) }
}

let lastSocket: FakeWebSocket | null = null
let savedWebSocket: typeof WebSocket | undefined

beforeEach(() => {
  savedWebSocket = (globalThis as { WebSocket?: typeof WebSocket }).WebSocket
  lastSocket = null
  ;(globalThis as { WebSocket: unknown }).WebSocket = function (this: FakeWebSocket, url: string | URL) {
    const s = new FakeWebSocket(url)
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
  if (savedWebSocket) (globalThis as { WebSocket: typeof WebSocket }).WebSocket = savedWebSocket
  delete (globalThis as Record<string, unknown>).__SIYI_CLIENT_VERSION__
})

async function connected(opts?: WsClientOptions): Promise<WsClient> {
  const client = new WsClient({ url: 'ws://127.0.0.1:18765/ws', heartbeatIntervalMs: 0, ...opts })
  client.connect()
  await vi.advanceTimersByTimeAsync(1)
  return client
}

describe('M2.2 client_version 声明', () => {
  it('握手 URL 带 client_version query（后端建连即记连接标记）', async () => {
    await connected()
    expect(lastSocket).toBeTruthy()
    const url = new URL(lastSocket!.url)
    expect(url.searchParams.get('client_version')).toBe(PACKAGE_CLIENT_VERSION)
  })

  it('call() 帧带 client_version（后端按帧升级连接标记）', async () => {
    const client = await connected()
    void client.call('notes.delete', { note_id: 'n1' })
    await vi.advanceTimersByTimeAsync(0)
    const frame = JSON.parse(lastSocket!.sentMessages.at(-1)!)
    expect(frame.method).toBe('notes.delete')
    expect(frame.client_version).toBe(PACKAGE_CLIENT_VERSION)
  })

  it('streamTyped() 帧同样带 client_version（chat.send 走这条通道）', async () => {
    const client = await connected()
    const run = (async () => { for await (const _c of client.streamTyped('chat.send', { message: 'x' })) { void _c } })()
    await vi.advanceTimersByTimeAsync(0)
    const frame = JSON.parse(lastSocket!.sentMessages.at(-1)!)
    expect(frame.method).toBe('chat.send')
    expect(frame.stream).toBe(true)
    expect(frame.client_version).toBe(PACKAGE_CLIENT_VERSION)
    lastSocket!.onmessage?.({ data: JSON.stringify({ jsonrpc: '2.0', id: frame.id, result: { type: 'done' } }) } as MessageEvent)
    await run
  })

  it('远端地址保留 token 且不因版本改动丢失', async () => {
    await connected({ url: 'wss://remote.example/ws', authToken: 'tok-1' })
    const url = new URL(lastSocket!.url)
    expect(url.searchParams.get('token')).toBe('tok-1')
    expect(url.searchParams.get('client_version')).toBe(PACKAGE_CLIENT_VERSION)
  })

  it('resolveClientVersion 优先级：override > 运行时全局 > 兜底常量，且永不为空', () => {
    expect(resolveClientVersion('9.9.9')).toBe('9.9.9')
    ;(globalThis as Record<string, unknown>).__SIYI_CLIENT_VERSION__ = '8.8.8'
    expect(resolveClientVersion()).toBe('8.8.8')
    delete (globalThis as Record<string, unknown>).__SIYI_CLIENT_VERSION__
    expect(resolveClientVersion('   ')).toBe(PACKAGE_CLIENT_VERSION)
    expect(PACKAGE_CLIENT_VERSION.trim().length).toBeGreaterThan(0)
  })

  it('兜底常量与 desktop/package.json 的 version 同步（防漏改）', () => {
    // vitest 的 jsdom 环境下 import.meta.url 非 file: 协议，只能按 cwd 定位
    // （vitest 配置在 desktop 包，默认 cwd=packages/desktop；从仓库根跑时走第二条）。
    const candidates = ['../desktop/package.json', 'packages/desktop/package.json', './package.json']
      .map((p) => resolve(process.cwd(), p))
    const file = candidates.find((p) => existsSync(p))
    if (!file) throw new Error(`找不到 desktop/package.json（尝试: ${candidates.join(', ')}）`)
    const pkg = JSON.parse(readFileSync(file, 'utf-8')) as { name: string; version: string }
    expect(pkg.name).toBe('@desk-notes/desktop')
    expect(PACKAGE_CLIENT_VERSION).toBe(pkg.version)
  })
})
