import { describe, test, expect, beforeEach } from 'vitest'
import { useStore } from '../useStore'

// Reset store between tests
beforeEach(() => {
  useStore.setState({ conversations: [], activeConversationId: null })
})

describe('conversation store', () => {
  test('createConversation adds a conversation and sets it active', () => {
    const id = useStore.getState().createConversation()
    const state = useStore.getState()
    expect(state.conversations).toHaveLength(1)
    expect(state.conversations[0].id).toBe(id)
    expect(state.activeConversationId).toBe(id)
    expect(state.conversations[0].title).toBe('新对话')
    expect(state.conversations[0].messages).toEqual([])
  })

  test('createConversation returns unique ids', () => {
    const id1 = useStore.getState().createConversation()
    const id2 = useStore.getState().createConversation()
    expect(id1).not.toBe(id2)
    expect(useStore.getState().conversations).toHaveLength(2)
  })

  test('addMessage appends to active conversation', () => {
    const id = useStore.getState().createConversation()
    useStore.getState().addMessage('你好', 'user')
    useStore.getState().addMessage('你好！我是小问', 'assistant')

    const conv = useStore.getState().conversations.find(c => c.id === id)
    expect(conv?.messages).toHaveLength(2)
    expect(conv?.messages[0].content).toBe('你好')
    expect(conv?.messages[0].role).toBe('user')
    expect(conv?.messages[1].content).toBe('你好！我是小问')
    expect(conv?.messages[1].role).toBe('assistant')
  })

  test('addMessage with no active conversation is no-op', () => {
    useStore.getState().addMessage('test', 'user')
    expect(useStore.getState().conversations).toHaveLength(0)
  })

  test('setActiveConversation switches active conversation', () => {
    const id1 = useStore.getState().createConversation()
    const id2 = useStore.getState().createConversation()
    useStore.getState().setActiveConversation(id1)
    expect(useStore.getState().activeConversationId).toBe(id1)
    useStore.getState().setActiveConversation(id2)
    expect(useStore.getState().activeConversationId).toBe(id2)
  })

  test('deleteConversation removes conversation', () => {
    const id1 = useStore.getState().createConversation()
    const id2 = useStore.getState().createConversation()
    useStore.getState().deleteConversation(id1)
    expect(useStore.getState().conversations).toHaveLength(1)
    expect(useStore.getState().conversations[0].id).toBe(id2)
  })

  test('deleteConversation switches active when deleting active', () => {
    const id1 = useStore.getState().createConversation()
    const id2 = useStore.getState().createConversation()
    useStore.getState().setActiveConversation(id1)
    useStore.getState().deleteConversation(id1)
    expect(useStore.getState().activeConversationId).toBe(id2)
  })

  test('deleteConversation sets null when deleting last conversation', () => {
    const id = useStore.getState().createConversation()
    useStore.getState().deleteConversation(id)
    expect(useStore.getState().conversations).toHaveLength(0)
    expect(useStore.getState().activeConversationId).toBeNull()
  })

  test('clearConversation empties messages', () => {
    const id = useStore.getState().createConversation()
    useStore.getState().addMessage('hi', 'user')
    useStore.getState().clearConversation(id)
    const conv = useStore.getState().conversations.find(c => c.id === id)
    expect(conv?.messages).toEqual([])
  })

  test('setConversationTitle updates title', () => {
    const id = useStore.getState().createConversation()
    useStore.getState().setConversationTitle(id, '测试对话')
    const conv = useStore.getState().conversations.find(c => c.id === id)
    expect(conv?.title).toBe('测试对话')
  })

  test('messages have timestamps', () => {
    const before = Date.now()
    const id = useStore.getState().createConversation()
    useStore.getState().addMessage('hi', 'user')
    const after = Date.now()
    const msg = useStore.getState().conversations.find(c => c.id === id)?.messages[0]
    expect(msg?.timestamp).toBeGreaterThanOrEqual(before)
    expect(msg?.timestamp).toBeLessThanOrEqual(after)
  })

  test('multiple conversations persist independently', () => {
    const id1 = useStore.getState().createConversation()
    useStore.getState().addMessage('msg1', 'user')

    const id2 = useStore.getState().createConversation()
    useStore.getState().addMessage('msg2', 'user')

    expect(useStore.getState().conversations).toHaveLength(2)
    const c1 = useStore.getState().conversations.find(c => c.id === id1)
    const c2 = useStore.getState().conversations.find(c => c.id === id2)
    expect(c1?.messages).toHaveLength(1)
    expect(c1?.messages[0].content).toBe('msg1')
    expect(c2?.messages).toHaveLength(1)
    expect(c2?.messages[0].content).toBe('msg2')
  })
})
