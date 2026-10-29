import { describe, it, expect } from 'vitest'
import { WsClient } from '../ws-client'

/**
 * 空闲看门狗（stream / streamTyped）测试。
 *
 * 背景：后端异常挂起（无任何 chunk/事件）时，若前端无空闲超时，生成器会永远
 * 卡在 await 上，UI 停在"思考中"。看门狗仅在真正等待下一 chunk 期间计时，
 * 收到 chunk 即重置——这样消费者处理耗时不会误触发。
 */

class FakeWebSocket {
  readyState = 1 // OPEN
  static OPEN = 1
  sentMessages: string[] = []
  send(msg: string) { this.sentMessages.push(msg) }
  close() { this.readyState = 3 }
  addEventListener() { /* noop */ }
  removeEventListener() { /* noop */ }
}

function makeOpenClient(): { client: WsClient; fake: FakeWebSocket; handlers: () => Map<number, Record<string, (v?: unknown) => void>> } {
  const fake = new FakeWebSocket()
  const client = new WsClient({ url: 'ws://test' })
  ;(client as unknown as { _ws: FakeWebSocket })._ws = fake
  ;(client as unknown as { _status: string })._status = 'connected'
  const handlers = () => (client as unknown as { _streamHandlers: Map<number, Record<string, (v?: unknown) => void>> })._streamHandlers
  return { client, fake, handlers }
}

function streamId(client: WsClient): number {
  const sent = (client as unknown as { _ws: FakeWebSocket })._ws.sentMessages
  return JSON.parse(sent[sent.length - 1]).id as number
}

describe('WsClient.stream idle watchdog', () => {
  it('aborts with idle timeout error when backend goes silent', async () => {
    const { client } = makeOpenClient()
    const iter = client.stream<string>('chat.send', { message: 'hi' }, { idleTimeoutMs: 25 })
    const first = iter.next()
    await Promise.resolve() // 让 body 跑到第一个 await 并注册 watchdog

    await expect(first).rejects.toThrow(/idle timeout.+/i)
    // 超时后流应已终止，后续 next 直接结束（不会再次挂起）
    await expect(iter.next()).resolves.toEqual({ value: undefined, done: true })
  })

  it('resets the watchdog on each received chunk (does not fire early)', async () => {
    const { client, handlers } = makeOpenClient()
    const iter = client.stream<string>('chat.send', { message: 'hi' }, { idleTimeoutMs: 60 })
    const first = iter.next()
    await Promise.resolve()
    const h = handlers().get(streamId(client))!

    // 0ms 内立刻来一个 chunk —— 应在超时（60ms）之前被消费
    h.onChunk('a')
    const r = await first
    expect(r).toEqual({ value: 'a', done: false })

    // 消费后进入新一轮等待；不再推送 chunk，应再次触发看门狗
    const second = iter.next()
    await expect(second).rejects.toThrow(/idle timeout.+/i)
  })
})

describe('WsClient.streamTyped idle watchdog', () => {
  it('aborts with idle timeout error on silence', async () => {
    const { client } = makeOpenClient()
    const iter = client.streamTyped('chat.send', { message: 'hi' }, { idleTimeoutMs: 25 })
    const first = iter.next()
    await Promise.resolve()

    await expect(first).rejects.toThrow(/idle timeout.+/i)
    await expect(iter.next()).resolves.toEqual({ value: undefined, done: true })
  })

  it('resets watchdog on a metadata/typed chunk', async () => {
    const { client, handlers } = makeOpenClient()
    const iter = client.streamTyped('chat.send', { message: 'hi' }, { idleTimeoutMs: 60 })
    const first = iter.next()
    await Promise.resolve()
    const h = handlers().get(streamId(client))!

    h.onTypedChunk('tool_progress', 'running step 1')
    const r = await first
    expect(r.value).toEqual({ type: 'tool_progress', data: 'running step 1' })

    const second = iter.next()
    await expect(second).rejects.toThrow(/idle timeout.+/i)
  })
})