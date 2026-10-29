/**
 * P3 D2: chatSessionSlice 回归测试 — 流式状态跨页面保持的状态流转。
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { createChatSessionSlice, type ChatSessionSlice } from '../slices/chatSessionSlice'

/** 用最小 harness 驱动 slice（无需完整 Store）。 */
function makeHarness() {
  let state = {} as ChatSessionSlice
  const set = ((updater: (s: ChatSessionSlice) => Partial<ChatSessionSlice>) => {
    state = { ...state, ...updater(state) }
  }) as never
  state = createChatSessionSlice(set, (() => state) as never, {} as never)
  return { get: () => state }
}

describe('chatSessionSlice', () => {
  let h: ReturnType<typeof makeHarness>

  beforeEach(() => {
    h = makeHarness()
  })

  it('start 创建 streaming 会话', () => {
    const before = Date.now()
    h.get().chatSessionStart('c1')
    const s = h.get().chatSessions['c1']
    expect(s.isStreaming).toBe(true)
    expect(s.streamedText).toBe('')
    expect(s.events).toEqual([])
    expect(s.error).toBeNull()
    // P6a：startedAt 写入，供思考计时跨页面派生
    expect(s.startedAt).toBeGreaterThanOrEqual(before)
    expect(s.startedAt).toBeLessThanOrEqual(Date.now())
  })

  it('chunk 累积文本、event 保留最近 20 条', () => {
    h.get().chatSessionStart('c1')
    h.get().chatSessionChunk('c1', '你好')
    h.get().chatSessionChunk('c1', '，世界')
    expect(h.get().chatSessions['c1'].streamedText).toBe('你好，世界')

    for (let i = 0; i < 25; i++) {
      h.get().chatSessionEvent('c1', { type: 'tool_progress', data: String(i) })
    }
    const events = h.get().chatSessions['c1'].events
    expect(events.length).toBe(20)
    expect(events[0].data).toBe('5')
    expect(events[19].data).toBe('24')
  })

  it('end 不带 error 时保留已有 error（finally 不覆盖 catch）', () => {
    h.get().chatSessionStart('c1')
    h.get().chatSessionEnd('c1', 'boom')
    h.get().chatSessionEnd('c1')
    const s = h.get().chatSessions['c1']
    expect(s.isStreaming).toBe(false)
    expect(s.error).toBe('boom')
  })

  it('end 显式传 null 可清空 error', () => {
    h.get().chatSessionStart('c1')
    h.get().chatSessionEnd('c1', 'boom')
    h.get().chatSessionEnd('c1', null)
    expect(h.get().chatSessions['c1'].error).toBeNull()
  })

  it('clearError / clear 行为', () => {
    h.get().chatSessionStart('c1')
    h.get().chatSessionEnd('c1', 'err')
    h.get().chatSessionClearError('c1')
    expect(h.get().chatSessions['c1'].error).toBeNull()

    h.get().chatSessionClear('c1')
    expect(h.get().chatSessions['c1']).toBeUndefined()
  })

  it('对不存在的会话操作是安全的 no-op', () => {
    const before = h.get().chatSessions
    h.get().chatSessionChunk('ghost', 'x')
    h.get().chatSessionEvent('ghost', { type: 'tool_progress', data: 'y' })
    h.get().chatSessionEnd('ghost')
    h.get().chatSessionClearError('ghost')
    h.get().chatSessionClear('ghost')
    expect(h.get().chatSessions).toBe(before)
  })

  it('重新 start 会重置会话状态（新对话覆盖旧流）', () => {
    h.get().chatSessionStart('c1')
    h.get().chatSessionChunk('c1', '旧文本')
    h.get().chatSessionEvent('c1', { type: 'tool_progress', data: 'old' })
    h.get().chatSessionStart('c1')
    const s = h.get().chatSessions['c1']
    expect(s.isStreaming).toBe(true)
    expect(s.streamedText).toBe('')
    expect(s.events).toEqual([])
  })
})
