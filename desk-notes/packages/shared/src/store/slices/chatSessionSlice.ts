import type { StateCreator } from 'zustand'
import type { Store } from '../types'

/** 聊天流式会话状态（按 conversationId 保存）。
 *
 * P3 D2：把流式状态从组件 useState 提升到全局 store，
 * 切换页面/会话后流在后台继续跑，回到页面能看到"思考中"与暂停按钮。
 * 该 slice 属于瞬态状态，不参与持久化（见 useStore TRANSIENT_KEYS）。
 */

export interface ChatSessionEventItem {
  type: string
  data: string
}

export interface ChatSessionState {
  isStreaming: boolean
  /** 流开始时间戳（P6a：计时从 store 派生，切页回来不重置） */
  startedAt: number
  streamedText: string
  events: ChatSessionEventItem[]
  error: string | null
}

export interface ChatSessionSlice {
  chatSessions: Record<string, ChatSessionState>
  chatSessionStart: (id: string) => void
  chatSessionChunk: (id: string, delta: string) => void
  chatSessionEvent: (id: string, event: ChatSessionEventItem) => void
  /** error 传 undefined 时保留已有 error（finally 收尾不覆盖 catch 设置的错误） */
  chatSessionEnd: (id: string, error?: string | null) => void
  chatSessionClearError: (id: string) => void
  chatSessionClear: (id: string) => void
}

function emptySession(): ChatSessionState {
  return { isStreaming: false, startedAt: 0, streamedText: '', events: [], error: null }
}

export const createChatSessionSlice: StateCreator<Store, [], [], ChatSessionSlice> = (set) => ({
  chatSessions: {},

  chatSessionStart: (id) => set((s) => ({
    chatSessions: { ...s.chatSessions, [id]: { ...emptySession(), isStreaming: true, startedAt: Date.now() } },
  })),

  chatSessionChunk: (id, delta) => set((s) => {
    const cur = s.chatSessions[id]
    if (!cur) return s
    return {
      chatSessions: {
        ...s.chatSessions,
        [id]: { ...cur, streamedText: cur.streamedText + delta },
      },
    }
  }),

  chatSessionEvent: (id, event) => set((s) => {
    const cur = s.chatSessions[id]
    if (!cur) return s
    return {
      chatSessions: {
        ...s.chatSessions,
        [id]: { ...cur, events: [...cur.events.slice(-19), event] },
      },
    }
  }),

  chatSessionEnd: (id, error) => set((s) => {
    const cur = s.chatSessions[id]
    if (!cur) return s
    return {
      chatSessions: {
        ...s.chatSessions,
        [id]: { ...cur, isStreaming: false, error: error !== undefined ? error : cur.error },
      },
    }
  }),

  chatSessionClearError: (id) => set((s) => {
    const cur = s.chatSessions[id]
    if (!cur || cur.error === null) return s
    return {
      chatSessions: { ...s.chatSessions, [id]: { ...cur, error: null } },
    }
  }),

  chatSessionClear: (id) => set((s) => {
    if (!s.chatSessions[id]) return s
    const next = { ...s.chatSessions }
    delete next[id]
    return { chatSessions: next }
  }),
})
