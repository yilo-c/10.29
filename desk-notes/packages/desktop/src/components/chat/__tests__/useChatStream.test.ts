/**
 * M2.2 第1步：chat 四 bug 中的 ①③（停流卡死 / session_id 透传）回归。
 *
 * bug①：stopChatStream 旧实现先 `_controllers.delete()` 再让 sendChatStream 的
 *   finally 守卫 `_controllers.get(id) === controller` 判定 → 守卫永不成立 →
 *   chatSessionEnd 不被调用 → isStreaming 永真、输入框卡死
 *   （e2e/chat.spec.ts:365-370 曾因此「刻意不断言 composer 回到可发送态」）。
 * bug③：chat.send / chat.cancel 旧实现都不带 session_id；后端
 *   _chat_memory.py:309 按 session_id 登记在途流、:504-515 支持定向取消，
 *   不带参数 = 取消全部在途流（停 A 误伤 B）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { sendChatStream, stopChatStream, resumeChatStream } from '../useChatStream'
import { useStore } from '../../../store/useStore'

const { mockGetWsClient, wsMock } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
  wsMock: {
    status: 'connected',
    call: vi.fn(),
    streamTyped: vi.fn(),
    on: vi.fn(() => () => {}),
    onStatusChange: vi.fn(() => () => {}),
  },
}))

vi.mock('../../../utils/ai', () => ({ getWsClient: () => mockGetWsClient() }))
vi.mock('../../../utils/trackUsage', () => ({ trackUsage: vi.fn() }))

/** 可控流：每次 yield 前等一个 gate，测试由此精确停在「流仍在跑」的时刻。 */
function controllableStream(chunks: Array<{ type: string; data: string }>) {
  const gates: Array<() => void> = chunks.map(() => () => {})
  let index = 0
  const gen = (async function* () {
    for (const chunk of chunks) {
      await new Promise<void>((resolve) => { gates[index] = resolve })
      index += 1
      yield chunk
    }
  })()
  return { gen, release: (i: number) => gates[i]?.() }
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetWsClient.mockReturnValue(wsMock)
  wsMock.status = 'connected'
  wsMock.call.mockResolvedValue({ cancelled: true, streams: [] })
  useStore.setState({ chatSessions: {} })
})

describe('useChatStream bug① 停流不复位', () => {
  it('stopChatStream 立即把 isStreaming 置回 false（旧实现停在 true）', async () => {
    const stream = controllableStream([
      { type: 'chunk', data: '第一段' },
      { type: 'chunk', data: '第二段' },
    ])
    wsMock.streamTyped.mockReturnValue(stream.gen)

    const running = sendChatStream('c1', '你好', [])
    stream.release(0)
    await vi.waitFor(() => {
      expect(useStore.getState().chatSessions['c1']?.isStreaming).toBe(true)
    })

    stopChatStream('c1')
    // 关键断言：停的瞬间会话态即复位，不等下一个 chunk、不依赖 finally 守卫
    expect(useStore.getState().chatSessions['c1']?.isStreaming).toBe(false)

    stream.release(1)
    await running
    expect(useStore.getState().chatSessions['c1']?.isStreaming).toBe(false)
  })

  it('自然收尾（无 stop）也要复位 isStreaming', async () => {
    const stream = controllableStream([{ type: 'chunk', data: '只有一段' }])
    wsMock.streamTyped.mockReturnValue(stream.gen)
    const running = sendChatStream('c2', '你好', [])
    stream.release(0)
    const result = await running
    expect(result.text).toBe('只有一段')
    expect(useStore.getState().chatSessions['c2']?.isStreaming).toBe(false)
  })

  it('新一轮 send 的 finally 不得把新流误判收尾（守卫仍生效）', async () => {
    const first = controllableStream([{ type: 'chunk', data: 'A' }])
    wsMock.streamTyped.mockReturnValueOnce(first.gen)
    const running1 = sendChatStream('c3', '一', [])
    first.release(0)
    await vi.waitFor(() => expect(useStore.getState().chatSessions['c3']?.isStreaming).toBe(true))

    const second = controllableStream([{ type: 'chunk', data: 'B' }])
    wsMock.streamTyped.mockReturnValueOnce(second.gen)
    const running2 = sendChatStream('c3', '二', [])
    await vi.waitFor(() => expect(useStore.getState().chatSessions['c3']?.isStreaming).toBe(true))

    second.release(0)
    await running2
    await running1
    // 新流已收尾 → false；若旧流的 finally 抢先复位，这里同样为 false，
    // 但真正要守的是「旧流不得覆盖新流的 start」：新流开始时必须为 true（上一段已断言）
    expect(useStore.getState().chatSessions['c3']?.isStreaming).toBe(false)
  })
})

describe('useChatStream bug③ session_id 透传', () => {
  it('chat.send 带当前会话 id，chat.cancel 定向取消该会话', async () => {
    const stream = controllableStream([{ type: 'chunk', data: 'x' }])
    wsMock.streamTyped.mockReturnValue(stream.gen)
    const running = sendChatStream('conv-42', '你好', [])
    stream.release(0)
    await running

    const sendCall = wsMock.streamTyped.mock.calls.find((c) => c[0] === 'chat.send')
    expect(sendCall).toBeTruthy()
    expect((sendCall![1] as Record<string, unknown>).session_id).toBe('conv-42')

    stopChatStream('conv-42')
    expect(wsMock.call).toHaveBeenCalledWith('chat.cancel', { session_id: 'conv-42' })
  })

  it('chat.resume 继续带 session_id（既有行为不回归）', async () => {
    const stream = controllableStream([{ type: 'chat_resume_status', data: '{}' }])
    wsMock.streamTyped.mockReturnValue(stream.gen)
    const running = resumeChatStream('conv-77')
    stream.release(0)
    await running
    const call = wsMock.streamTyped.mock.calls.find((c) => c[0] === 'chat.resume')
    expect((call![1] as Record<string, unknown>).session_id).toBe('conv-77')
  })
})

describe('U1 扩白名单：tool/confirm 帧被收入 events 不被丢弃', () => {
  it('tool / confirm / notice 帧进入 store events（旧实现静默丢弃 tool/confirm）', async () => {
    // 无需 gate 精确控时：只要证明帧被捕获即可，用普通 async generator 顺序吐出。
    async function* plainStream() {
      yield { type: 'tool', data: '{"tool":"siyi_notes_search","status":"running"}' }
      yield { type: 'confirm', data: '{"confirmation_id":"c1"}' }
      yield { type: 'notice', data: '{"phase":"waiting","message":"仍在执行"}' }
      yield { type: 'chunk', data: '正文' }
    }
    wsMock.streamTyped.mockReturnValue(plainStream())
    const running = sendChatStream('c-tool', '你好', [])
    await running
    const evs = useStore.getState().chatSessions['c-tool']?.events ?? []
    expect(evs.find((e) => e.type === 'tool')).toBeTruthy()
    expect(evs.find((e) => e.type === 'confirm')).toBeTruthy()
    expect(evs.find((e) => e.type === 'notice')).toBeTruthy()
    // chunk 仍是正文，不进 events
    expect(evs.some((e) => e.type === 'chunk')).toBe(false)
  })
})
