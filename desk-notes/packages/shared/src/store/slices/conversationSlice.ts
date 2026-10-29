import type { StateCreator } from 'zustand'
import type { Store } from '../types'
import { Conversation } from '../../types'
import { uid } from '../../utils/helpers'

export interface ConversationSlice {
  conversations: Conversation[]
  activeConversationId: string | null

  createConversation: () => string
  importConversations: (conversations: Conversation[]) => void
  deleteConversation: (id: string) => void
  setActiveConversation: (id: string | null) => void
  addMessage: (content: string, role: 'user' | 'assistant') => void
  addMessageToConversation: (id: string, content: string, role: 'user' | 'assistant') => void
  /** 整体替换会话消息（P6：重新生成/编辑重发/删除单条） */
  replaceConversationMessages: (id: string, messages: Array<{ role: 'user' | 'assistant'; content: string; timestamp?: number }>) => void
  clearConversation: (id: string) => void
  setConversationTitle: (id: string, title: string) => void
}

export const createConversationSlice: StateCreator<Store, [], [], ConversationSlice> = (set, _get, _api) => ({
    conversations: [],
    activeConversationId: null,

    createConversation: () => {
      const id = uid()
      const now = Date.now()
      const conv: Conversation = { id, title: '新对话', createdAt: now, updatedAt: now, messages: [] }
      set((s: ConversationSlice) => ({ conversations: [...s.conversations, conv], activeConversationId: id }))
      return id
    },

    importConversations: (incoming) => set((s: ConversationSlice) => {
      const existingIds = new Set(s.conversations.map((conversation) => conversation.id))
      const additions = incoming.filter((conversation) => !existingIds.has(conversation.id))
      if (additions.length === 0) return s
      return { conversations: [...s.conversations, ...additions] }
    }),

    deleteConversation: (id) => set((s: ConversationSlice) => {
      const filtered = s.conversations.filter(c => c.id !== id)
      const newActive = s.activeConversationId === id
        ? (filtered.length > 0 ? filtered[filtered.length - 1].id : null)
        : s.activeConversationId
      return { conversations: filtered, activeConversationId: newActive }
    }),

    setActiveConversation: (id) => set({ activeConversationId: id }),

    addMessage: (content, role) => set((s: ConversationSlice) => {
      const id = s.activeConversationId
      if (!id) return s
      return addMessageToState(s, id, content, role)
    }),

    addMessageToConversation: (id, content, role) => set((s: ConversationSlice) => addMessageToState(s, id, content, role)),

    replaceConversationMessages: (id, messages) => set((s: ConversationSlice) => ({
      conversations: s.conversations.map(c =>
        c.id === id
          ? { ...c, messages: messages.map(m => ({ role: m.role, content: m.content, timestamp: m.timestamp ?? Date.now() })), updatedAt: Date.now() }
          : c
      ),
    })),

    clearConversation: (id) => set((s: ConversationSlice) => ({
      conversations: s.conversations.map(c =>
        c.id === id ? { ...c, messages: [], updatedAt: Date.now() } : c
      ),
    })),

    setConversationTitle: (id, title) => set((s: ConversationSlice) => ({
      conversations: s.conversations.map(c =>
        c.id === id ? { ...c, title, updatedAt: Date.now() } : c
      ),
    })),
  }
)

function addMessageToState(state: ConversationSlice, id: string, content: string, role: 'user' | 'assistant') {
  const now = Date.now()
  return {
    conversations: state.conversations.map((conversation) =>
      conversation.id === id
        ? { ...conversation, messages: [...conversation.messages, { role, content, timestamp: now }], updatedAt: now }
        : conversation
    ),
  }
}
