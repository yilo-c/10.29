import { describe, it, expect, vi, beforeEach } from 'vitest'
import { extractTodosFromContent, addNoteTodo, toggleNoteTodo, deleteNoteTodo } from '../floatingNoteTodos'

// ─── Mocks ──────────────────────────────────────────────────
const mockUndoPush = vi.fn()
vi.mock('../../../store/undoManager', () => ({
  undoManager: { push: (...args: unknown[]) => mockUndoPush(...args) },
}))

// Minimal store mock so useStore.setState doesn't crash
vi.mock('../../../store/useStore', () => ({
  useStore: { getState: () => ({ floatingNotes: [] }), setState: vi.fn() },
}))

describe('extractTodosFromContent', () => {
  const updateFn = vi.fn()

  beforeEach(() => { vi.clearAllMocks() })

  it('extracts todos from plain text', () => {
    const content = '- [ ] 买牛奶\n- [x] 交房租'
    extractTodosFromContent(content, [], 'n1', updateFn)
    expect(updateFn).toHaveBeenCalledWith('n1', {
      todos: [
        expect.objectContaining({ text: '买牛奶', done: false }),
        expect.objectContaining({ text: '交房租', done: true }),
      ],
    })
  })

  it('skips duplicate todos by text', () => {
    const existing = [{ id: '1', text: '买牛奶', done: false, createdAt: 0 }]
    extractTodosFromContent('- [ ] 买牛奶\n- [ ] 新事项', existing, 'n1', updateFn)
    // Only the new item should be added
    expect(updateFn).toHaveBeenCalledWith('n1', {
      todos: [
        ...existing,
        expect.objectContaining({ text: '新事项', done: false }),
      ],
    })
  })

  it('does nothing when content has no todos', () => {
    extractTodosFromContent('普通文本', [], 'n1', updateFn)
    expect(updateFn).not.toHaveBeenCalled()
  })

  it('strips HTML before matching', () => {
    const content = '<p>- [ ] 买牛奶</p>'
    extractTodosFromContent(content, [], 'n1', updateFn)
    expect(updateFn).toHaveBeenCalledWith('n1', {
      todos: [expect.objectContaining({ text: '买牛奶' })],
    })
  })
})

describe('addNoteTodo', () => {
  const updateFn = vi.fn()
  const setTextFn = vi.fn()

  beforeEach(() => { vi.clearAllMocks() })

  it('adds a todo to an empty list', () => {
    addNoteTodo(false, ' 新事项 ', [], 'n1', updateFn, setTextFn)
    expect(updateFn).toHaveBeenCalledWith('n1', {
      todos: [expect.objectContaining({ text: '新事项', done: false })],
    })
    expect(setTextFn).toHaveBeenCalledWith('')
  })

  it('appends to existing todos', () => {
    const existing = [{ id: '1', text: '旧事项', done: false, createdAt: 0 }]
    addNoteTodo(false, '新事项', existing, 'n1', updateFn, setTextFn)
    expect(updateFn).toHaveBeenCalledWith('n1', {
      todos: [...existing, expect.objectContaining({ text: '新事项' })],
    })
  })

  it('does nothing when locked', () => {
    addNoteTodo(true, '事项', [], 'n1', updateFn, setTextFn)
    expect(updateFn).not.toHaveBeenCalled()
  })

  it('does nothing for empty text', () => {
    addNoteTodo(false, '  ', [], 'n1', updateFn, setTextFn)
    expect(updateFn).not.toHaveBeenCalled()
  })
})

describe('toggleNoteTodo', () => {
  const updateFn = vi.fn()
  const baseTodos = [
    { id: '1', text: '事项A', done: false, createdAt: 0 },
    { id: '2', text: '事项B', done: true, createdAt: 0 },
  ]

  beforeEach(() => { vi.clearAllMocks() })

  it('toggles done from false to true', () => {
    toggleNoteTodo('1', false, baseTodos, 'n1', updateFn, vi.fn())
    expect(updateFn).toHaveBeenCalledWith('n1', {
      todos: [
        { id: '1', text: '事项A', done: true, createdAt: 0 },
        { id: '2', text: '事项B', done: true, createdAt: 0 },
      ],
    })
  })

  it('toggles done from true to false', () => {
    toggleNoteTodo('2', false, baseTodos, 'n1', updateFn, vi.fn())
    expect(updateFn).toHaveBeenCalledWith('n1', {
      todos: [
        { id: '1', text: '事项A', done: false, createdAt: 0 },
        { id: '2', text: '事项B', done: false, createdAt: 0 },
      ],
    })
  })

  it('does nothing when locked', () => {
    toggleNoteTodo('1', true, baseTodos, 'n1', updateFn, vi.fn())
    expect(updateFn).not.toHaveBeenCalled()
  })

  it('does nothing for non-existent todo', () => {
    toggleNoteTodo('999', false, baseTodos, 'n1', updateFn, vi.fn())
    expect(updateFn).not.toHaveBeenCalled()
  })

  it('pushes undo action', () => {
    toggleNoteTodo('1', false, baseTodos, 'n1', updateFn, vi.fn())
    expect(mockUndoPush).toHaveBeenCalled()
  })
})

describe('deleteNoteTodo', () => {
  const updateFn = vi.fn()
  const baseTodos = [
    { id: '1', text: '事项A', done: false, createdAt: 0 },
    { id: '2', text: '事项B', done: true, createdAt: 0 },
  ]

  beforeEach(() => { vi.clearAllMocks() })

  it('removes the specified todo', () => {
    deleteNoteTodo('1', false, baseTodos, 'n1', updateFn, vi.fn())
    expect(updateFn).toHaveBeenCalledWith('n1', {
      todos: [baseTodos[1]],
    })
  })

  it('does nothing when locked', () => {
    deleteNoteTodo('1', true, baseTodos, 'n1', updateFn, vi.fn())
    expect(updateFn).not.toHaveBeenCalled()
  })

  it('pushes undo action', () => {
    deleteNoteTodo('1', false, baseTodos, 'n1', updateFn, vi.fn())
    expect(mockUndoPush).toHaveBeenCalled()
  })
})
