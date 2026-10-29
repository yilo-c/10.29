import { describe, it, expect, vi, beforeEach } from 'vitest'
import { undoableDeleteTodo, undoableToggleTodo, undoableClearDone, getTodoContextMenuItems } from '../todoActions'
import { useStore } from '../../../store/useStore'
import { undoManager } from '../../../store/undoManager'

// Mocks
vi.mock('../../../store/useStore', () => ({
  useStore: { getState: vi.fn(), setState: vi.fn() },
}))
vi.mock('../../../store/undoManager', () => ({
  undoManager: { push: vi.fn() },
}))
vi.mock('../../../store/helpers', () => ({
  getDescendantIds: vi.fn((_todos, id) => {
    if (id === 'p1') return ['c1', 'c2']
    return []
  }),
}))

const t = (key: string) => key

describe('todoActions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('undoableDeleteTodo', () => {
    it('pushes undo entry and calls deleteTodo', () => {
      vi.mocked(useStore.getState).mockReturnValue({
        todos: [{ id: 't1', text: 'test', done: false, createdAt: 0 }],
      } as any)
      const deleteTodo = vi.fn()

      undoableDeleteTodo('t1', t, deleteTodo)

      expect(undoManager.push).toHaveBeenCalledOnce()
      expect(deleteTodo).toHaveBeenCalledWith('t1')
    })

    it('skips undo when no todos to restore', () => {
      vi.mocked(useStore.getState).mockReturnValue({ todos: [] } as any)
      const deleteTodo = vi.fn()

      undoableDeleteTodo('gone', t, deleteTodo)

      expect(undoManager.push).not.toHaveBeenCalled()
      expect(deleteTodo).toHaveBeenCalledWith('gone')
    })

    it('includes descendant ids in restore', () => {
      vi.mocked(useStore.getState).mockReturnValue({
        todos: [
          { id: 'p1', text: 'parent', done: false, createdAt: 0 },
          { id: 'c1', text: 'child1', done: false, createdAt: 0 },
          { id: 'c2', text: 'child2', done: false, createdAt: 0 },
        ],
      } as any)
      const deleteTodo = vi.fn()

      undoableDeleteTodo('p1', t, deleteTodo)

      expect(undoManager.push).toHaveBeenCalled()
      const undoFn = vi.mocked(undoManager.push).mock.calls[0][1]
      useStore.setState({})
      vi.mocked(useStore.setState).mockClear()
      undoFn()
      expect(useStore.setState).toHaveBeenCalled()
    })
  })

  describe('undoableToggleTodo', () => {
    it('pushes undo entry and calls toggleTodo', () => {
      vi.mocked(useStore.getState).mockReturnValue({
        todos: [{ id: 't1', text: 'test', done: false, createdAt: 0 }],
      } as any)
      const toggleTodo = vi.fn()

      undoableToggleTodo('t1', t, toggleTodo)

      expect(undoManager.push).toHaveBeenCalledOnce()
      expect(toggleTodo).toHaveBeenCalledWith('t1')
    })

    it('skips undo when todo not found', () => {
      vi.mocked(useStore.getState).mockReturnValue({ todos: [] } as any)
      const toggleTodo = vi.fn()

      undoableToggleTodo('gone', t, toggleTodo)

      expect(undoManager.push).not.toHaveBeenCalled()
      expect(toggleTodo).toHaveBeenCalledWith('gone')
    })
  })

  describe('undoableClearDone', () => {
    it('pushes undo and calls clearDoneTodos when done items exist', () => {
      vi.mocked(useStore.getState).mockReturnValue({
        todos: [
          { id: 'd1', text: 'done1', done: true, createdAt: 0 },
          { id: 'a1', text: 'active', done: false, createdAt: 0 },
        ],
      } as any)
      const clearDoneTodos = vi.fn()

      undoableClearDone(t, clearDoneTodos)

      expect(undoManager.push).toHaveBeenCalledOnce()
      expect(clearDoneTodos).toHaveBeenCalledOnce()
    })

    it('does nothing when no done items', () => {
      vi.mocked(useStore.getState).mockReturnValue({
        todos: [
          { id: 'a1', text: 'active', done: false, createdAt: 0 },
        ],
      } as any)
      const clearDoneTodos = vi.fn()

      undoableClearDone(t, clearDoneTodos)

      expect(undoManager.push).not.toHaveBeenCalled()
      expect(clearDoneTodos).not.toHaveBeenCalled()
    })
  })

  describe('getTodoContextMenuItems', () => {
    const filtered = [
      { id: 't1', text: 'test', done: false, repeatInterval: undefined, createdAt: 0 },
    ] as any

    it('returns empty array when no ctxMenu todoId', () => {
      const items = getTodoContextMenuItems(null, filtered, t, vi.fn(), vi.fn())
      expect(items).toEqual([])
    })

    it('returns copy, repeat, and delete items', () => {
      const items = getTodoContextMenuItems(
        { todoId: 't1' }, filtered, t, vi.fn(), vi.fn(),
      )
      const labels = items.map(i => i.label)
      expect(labels).toContain('todo.copyText')
      expect(labels.some(l => l?.includes('todo.repeat.none'))).toBe(true)
      expect(labels).toContain('common.delete')
    })

    it('highlights current repeat interval', () => {
      const filteredWithRepeat = [
        { id: 't1', text: 'test', done: false, repeatInterval: 7, createdAt: 0 },
      ] as any
      const items = getTodoContextMenuItems(
        { todoId: 't1' }, filteredWithRepeat, t, vi.fn(), vi.fn(),
      )
      const weekly = items.find(i => i.label?.includes('todo.repeat.weekly'))
      expect(weekly?.label).toContain('●')
    })

    it('delete item calls onDelete with id', () => {
      const onDelete = vi.fn()
      const items = getTodoContextMenuItems(
        { todoId: 't1' }, filtered, t, vi.fn(), onDelete,
      )
      const deleteItem = items.find(i => i.label === 'common.delete')
      deleteItem!.onClick()
      expect(onDelete).toHaveBeenCalledWith('t1')
    })
  })
})
