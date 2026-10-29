import { describe, it, expect } from 'vitest'
import { WsClient } from '../ws-client'

/**
 * 测试 WsClient.stream 的 chunk 缓冲行为。
 *
 * 历史 bug：当 backend 在前端 await 之间发送多个 chunks 时，旧实现会丢弃
 * 在 onChunk 触发时 resolveChunk == null 的 chunks。修复后用 buffer 队列。
 */

class FakeWebSocket {
  readyState = 1 // OPEN
  static OPEN = 1
  sentMessages: string[] = []

  send(msg: string) {
    this.sentMessages.push(msg)
  }
  close() { this.readyState = 3 }
  addEventListener() { /* noop */ }
  removeEventListener() { /* noop */ }
  // hooks for tests to drive incoming messages
  onmessage: ((ev: MessageEvent) => void) | null = null
  onopen: ((ev: Event) => void) | null = null
  onclose: ((ev: CloseEvent) => void) | null = null
  onerror: ((ev: Event) => void) | null = null
}

describe('WsClient.stream chunk buffering', () => {
  it('does not lose chunks that arrive before consumer awaits', async () => {
    const fake = new FakeWebSocket()
    const client = new WsClient({ url: 'ws://test' })
    // Inject our fake socket
    ;(client as unknown as { _ws: FakeWebSocket; _statusListeners: Set<(s: string) => void> })._ws = fake
    ;(client as unknown as { _status: string })._status = 'connected'

    // Start a stream (lazy: nothing runs until first .next()).
    const iter = client.stream<string>('chat.send', { message: 'hi' })

    // Kick off the generator so it issues ws.send and registers the handler.
    const firstNextPromise = iter.next()
    // After awaiting a microtask we know the body has run up to the first await.
    await Promise.resolve()

    const req = JSON.parse(fake.sentMessages[0])
    const id = req.id

    // Backend pushes 3 chunks back-to-back BEFORE consumer awaits next() further.
    const handlers = (client as unknown as { _streamHandlers: Map<number, { onChunk: (s: string) => void; onDone: () => void }> })._streamHandlers
    const h = handlers.get(id)
    expect(h).toBeDefined()
    h!.onChunk('a')
    h!.onChunk('b')
    h!.onChunk('c')
    h!.onDone()

    // Consume — must receive ALL three chunks in order.
    const collected: string[] = []
    const first = await firstNextPromise
    if (!first.done) collected.push(first.value as string)
    while (true) {
      const r = await iter.next()
      if (r.done) break
      collected.push(r.value as string)
    }
    expect(collected).toEqual(['a', 'b', 'c'])
  })

  it('handles interleaved await + chunk arrivals', async () => {
    const fake = new FakeWebSocket()
    const client = new WsClient({ url: 'ws://test' })
    ;(client as unknown as { _ws: FakeWebSocket })._ws = fake
    ;(client as unknown as { _status: string })._status = 'connected'

    const iter = client.stream<string>('chat.send', { message: 'hi' })
    const firstNextPromise = iter.next()
    await Promise.resolve()
    const req = JSON.parse(fake.sentMessages[0])
    const id = req.id
    const handlers = (client as unknown as { _streamHandlers: Map<number, { onChunk: (s: string) => void; onDone: () => void }> })._streamHandlers
    const h = handlers.get(id)!

    const collected: string[] = []
    // Drive: send 1 chunk, satisfy first await; then 2 chunks then 2 awaits; then done
    h.onChunk('1')
    collected.push((await firstNextPromise).value as string)
    h.onChunk('2')
    h.onChunk('3')
    collected.push((await iter.next()).value as string)
    collected.push((await iter.next()).value as string)
    h.onDone()
    const last = await iter.next()
    expect(last.done).toBe(true)
    expect(collected).toEqual(['1', '2', '3'])
  })

  it('propagates onError as thrown exception', async () => {
    const fake = new FakeWebSocket()
    const client = new WsClient({ url: 'ws://test' })
    ;(client as unknown as { _ws: FakeWebSocket })._ws = fake
    ;(client as unknown as { _status: string })._status = 'connected'

    const iter = client.stream<string>('chat.send', { message: 'hi' })
    const firstNextPromise = iter.next()
    await Promise.resolve()
    const req = JSON.parse(fake.sentMessages[0])
    const id = req.id
    const handlers = (client as unknown as { _streamHandlers: Map<number, { onChunk: (s: string) => void; onError: (e: Error) => void }> })._streamHandlers
    const h = handlers.get(id)!

    // Send 1 chunk, then error.
    h.onChunk('partial')
    h.onError(new Error('backend boom'))

    const out: string[] = []
    let caught: Error | null = null
    try {
      const first = await firstNextPromise
      if (!first.done) out.push(first.value as string)
      while (true) {
        const r = await iter.next()
        if (r.done) break
        out.push(r.value as string)
      }
    } catch (e) {
      caught = e as Error
    }
    expect(out).toEqual(['partial'])
    expect(caught?.message).toBe('backend boom')
  })
})
