import type { StateCreator } from 'zustand'
import type { Store } from '../types'
import { Category, TodoItem, QimenEvent } from '../../types'
import { defaultTodos, defaultCategories, uid } from '../../utils/helpers'
import { syncCall } from '../../utils/syncCall'
import { getDescendantIds } from '../helpers'

// ── Trash handler (injected from useStore.ts) ──
type TrashPayload = { id: string; type: 'note' | 'todo'; data: unknown; deletedAt: number }
let _trashHandler: (item: TrashPayload) => void = () => {}
export function setTrashHandler(handler: (item: TrashPayload) => void) {
  _trashHandler = handler
}

export interface TodoSlice {
  todos: TodoItem[]
  categories: Category[]
  activeCat: string
  addingTodo: boolean

  setAddingTodo: (v: boolean) => void
  setCategory: (c: string) => void
  addTodo: (text: string, category?: string) => void
  addEvent: (text: string, dueDate?: number) => void
  editTodo: (id: string, text: string) => void
  addSubtask: (parentId: string, text: string) => void
  toggleTodo: (id: string) => void
  deleteTodo: (id: string) => void
  permanentlyDeleteTodo: (id: string) => void
  setPriority: (id: string, level: 0 | 1 | 2 | undefined) => void
  setDueDate: (id: string, timestamp: number | undefined) => void
  setReminderTime: (id: string, timestamp: number) => void
  clearReminder: (id: string) => void
  snoozeReminder: (id: string, minutes: number) => void
  setRepeatInterval: (id: string, interval: number | undefined) => void
  setQimenEvent: (id: string, qimenEvent: QimenEvent | undefined) => void
  setSubject: (id: string, subject: string | undefined) => void
  moveTodo: (fromIndex: number, toIndex: number) => void
  moveTodoToPosition: (id: string, insertIdx: number) => void
  moveTodoToCategory: (id: string, category: string) => void
  reorderTodoInCategory: (todoId: string, targetCategory: string, targetCardId: string | null) => void
  addCategory: (c: Category) => void
  removeCategory: (id: string) => void
  renameCategory: (id: string, label: string) => void
  batchDeleteTodos: (ids: string[]) => void
  batchToggleTodos: (ids: string[], done: boolean) => void
  batchSetPriority: (ids: string[], level: 0 | 1 | 2 | undefined) => void
  clearDoneTodos: () => void
}

export const createTodoSlice: StateCreator<Store, [], [], TodoSlice> = (set, get, _api) => ({

    todos: defaultTodos,
    categories: defaultCategories,
    activeCat: 'all',
    addingTodo: false,

    setAddingTodo: (v) => set({ addingTodo: v }),
    setCategory: (c) => set({ activeCat: c }),

    addTodo: (text, category) => {
      const todoId = uid()
      set((s: TodoSlice) => ({
        todos: [{ id: todoId, text, done: false, createdAt: Date.now(), updatedAt: Date.now(), sortOrder: Date.now(), category }, ...s.todos]
      }))
      syncCall('tasks.create', {
        title: text, category: category || '', id: todoId,
      })
    },

    addEvent: (text, dueDate) => {
      const todoId = uid()
      const reminderTime = dueDate ? (() => { const d = new Date(dueDate); d.setHours(9, 0, 0, 0); return d.getTime() })() : undefined
      set((s: TodoSlice) => ({
        todos: [{ id: todoId, text, done: false, isEvent: true, dueDate, reminderTime, createdAt: Date.now(), updatedAt: Date.now(), sortOrder: Date.now() }, ...s.todos]
      }))
      syncCall('tasks.create', {
        title: text, due_date: dueDate ?? null, reminder_time: reminderTime ?? null, is_event: true, id: todoId,
      })
    },

    editTodo: (id, text) => {
      set((s: TodoSlice) => ({
        todos: s.todos.map(t => t.id === id ? { ...t, text, updatedAt: Date.now() } : t)
      }))
      syncCall('tasks.update', { task_id: id, title: text })
    },

    addSubtask: (parentId, text) => {
      const subtaskId = uid()
      set((s: TodoSlice) => {
        const parent = s.todos.find(t => t.id === parentId)
        return {
          todos: [{
            id: subtaskId, text, done: false, createdAt: Date.now(),
            updatedAt: Date.now(), sortOrder: Date.now(), category: parent?.category, parentId,
          }, ...s.todos]
        }
      })
      syncCall('tasks.create', {
        title: text, parent_id: parentId, id: subtaskId,
      })
    },

    toggleTodo: (id) => {
      const todo = get().todos.find((t: TodoItem) => t.id === id)
      if (!todo) return
      const newDone = !todo.done
      set((s: TodoSlice) => {
        if (!todo.done && todo.repeatInterval) {
          const intervalMs = todo.repeatInterval * 24 * 60 * 60 * 1000
          const newDueDate = todo.dueDate
            ? todo.dueDate + intervalMs
            : Date.now() + intervalMs
          return {
            todos: [{ ...todo, id: uid(), done: false, createdAt: todo.createdAt, updatedAt: Date.now(), sortOrder: Date.now(), dueDate: newDueDate }, ...s.todos.map(t => t.id === id ? { ...t, done: true, completedAt: Date.now(), updatedAt: Date.now() } : t)]
          }
        }
        return { todos: s.todos.map(t => t.id === id ? { ...t, done: newDone, completedAt: newDone ? Date.now() : undefined, updatedAt: Date.now() } : t) }
      })
      syncCall('tasks.toggle', { task_id: id, completed: newDone })
    },

    deleteTodo: (id) => {
      const toDelete: string[] = [id, ...getDescendantIds(get().todos, id)]
      for (const delId of toDelete) {
        const todo = get().todos.find((t: TodoItem) => t.id === delId)
        if (todo) _trashHandler({ id: delId, type: 'todo', data: todo, deletedAt: Date.now() })
      }
      set((s: TodoSlice) => ({ todos: s.todos.filter(t => !toDelete.includes(t.id)) }))
      syncCall('tasks.delete', { task_id: id })
    },

    batchDeleteTodos: (ids) => {
      const allIds = new Set<string>()
      for (const id of ids) {
        const idsToDel = [id, ...getDescendantIds(get().todos, id)]
        idsToDel.forEach(did => allIds.add(did))
      }
      for (const delId of allIds) {
        const todo = get().todos.find((t: TodoItem) => t.id === delId)
        if (todo) _trashHandler({ id: delId, type: 'todo', data: todo, deletedAt: Date.now() })
      }
      set((s: TodoSlice) => ({ todos: s.todos.filter(t => !allIds.has(t.id)) }))
    },

    batchToggleTodos: (ids, done) => set((s: TodoSlice) => {
      const idSet = new Set(ids)
      return { todos: s.todos.map(t =>
        idSet.has(t.id) ? { ...t, done, completedAt: done ? Date.now() : undefined, updatedAt: Date.now() } : t
      )}
    }),

    batchSetPriority: (ids, level) => set((s: TodoSlice) => {
      const idSet = new Set(ids)
      return { todos: s.todos.map(t =>
        idSet.has(t.id) ? { ...t, priority: level, updatedAt: Date.now() } : t
      )}
    }),

    permanentlyDeleteTodo: (id) => {
      const toDelete: string[] = [id, ...getDescendantIds(get().todos, id)]
      set((s: TodoSlice) => ({ todos: s.todos.filter(t => !toDelete.includes(t.id)) }))
    },

    setPriority: (id, level) => {
      set((s: TodoSlice) => ({
        todos: s.todos.map(t => t.id === id ? { ...t, priority: level, updatedAt: Date.now() } : t)
      }))
      syncCall('tasks.update', { task_id: id, priority: level })
    },

    setDueDate: (id, timestamp) => {
      set((s: TodoSlice) => {
        const existing = s.todos.find(t => t.id === id)
        // Preserve existing custom reminderTime if set; otherwise default to 9:00 AM
        const hasCustomReminder = existing?.reminderTime && existing.dueDate
          ? new Date(existing.reminderTime).getHours() !== 9
          : false
        const reminderTime = timestamp
          ? (hasCustomReminder ? existing!.reminderTime : (() => {
              const d = new Date(timestamp)
              d.setHours(9, 0, 0, 0)
              return d.getTime()
            })())
          : undefined

        return {
          todos: s.todos.map(t => t.id === id ? {
            ...t,
            dueDate: timestamp,
            reminderTime,
            updatedAt: Date.now(),
          } : t)
        }
      })
      syncCall('tasks.update', {
        task_id: id,
        due_date: timestamp ?? null,
        reminder_time: undefined, // computed on server
      })
    },

    setReminderTime: (id, timestamp) => {
      set((s: TodoSlice) => ({
        todos: s.todos.map(t => t.id === id ? { ...t, reminderTime: timestamp } : t)
      }))
      syncCall('tasks.update', { task_id: id, reminder_time: timestamp })
    },

    clearReminder: (id) => {
      set((s: TodoSlice) => ({
        todos: s.todos.map(t => t.id === id ? { ...t, reminderTime: undefined, reminderSnoozedUntil: undefined } : t)
      }))
      syncCall('tasks.update', { task_id: id, reminder_time: null })
    },

    snoozeReminder: (id, minutes) => set((s: TodoSlice) => ({
      todos: s.todos.map(t => t.id === id ? { ...t, reminderSnoozedUntil: Date.now() + minutes * 60 * 1000 } : t)
    })),

    setRepeatInterval: (id, interval) => set((s: TodoSlice) => ({
      todos: s.todos.map(t => t.id === id ? { ...t, repeatInterval: interval, updatedAt: Date.now() } : t)
    })),

    setQimenEvent: (id, qimenEvent) => set((s: TodoSlice) => ({
      todos: s.todos.map(t => t.id === id ? { ...t, qimenEvent } : t)
    })),

    setSubject: (id, subject) => set((s: TodoSlice) => ({
      todos: s.todos.map(t => t.id === id ? { ...t, subject, updatedAt: Date.now() } : t)
    })),

    moveTodo: (fromIndex, toIndex) => set((s: TodoSlice) => {
      const active = s.todos.filter(t => !t.done)
        .sort((a, b) => (a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt))
      if (fromIndex < 0 || fromIndex >= active.length || toIndex < 0 || toIndex >= active.length) return {}
      if (fromIndex === toIndex) return {}
      const target = active[fromIndex]
      const rest = active.filter(t => t.id !== target.id)
      const clamped = Math.max(0, Math.min(toIndex, rest.length))
      let newSortOrder: number
      if (clamped <= 0) {
        newSortOrder = (rest[0].sortOrder ?? rest[0].createdAt) - 1000
      } else if (clamped >= rest.length) {
        newSortOrder = (rest[rest.length - 1].sortOrder ?? rest[rest.length - 1].createdAt) + 1000
      } else {
        const before = rest[clamped - 1]
        const after = rest[clamped]
        newSortOrder = ((before.sortOrder ?? before.createdAt) + (after.sortOrder ?? after.createdAt)) / 2
      }
      return { todos: s.todos.map(t => t.id === target.id ? { ...t, sortOrder: newSortOrder } : t) }
    }),

    moveTodoToPosition: (id, insertIdx) => set((s: TodoSlice) => {
      const active = s.todos.filter(t => !t.done)
        .sort((a, b) => (a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt))
      const others = active.filter(t => t.id !== id)
      if (others.length === 0) return {}
      const clamped = Math.max(0, Math.min(insertIdx, others.length))
      let newSortOrder: number
      if (clamped <= 0) {
        newSortOrder = (others[0].sortOrder ?? others[0].createdAt) - 1000
      } else if (clamped >= others.length) {
        newSortOrder = (others[others.length - 1].sortOrder ?? others[others.length - 1].createdAt) + 1000
      } else {
        const before = others[clamped - 1]
        const after = others[clamped]
        newSortOrder = ((before.sortOrder ?? before.createdAt) + (after.sortOrder ?? after.createdAt)) / 2
      }
      return { todos: s.todos.map(t => t.id === id ? { ...t, sortOrder: newSortOrder } : t) }
    }),

    moveTodoToCategory: (id, category) => set((s: TodoSlice) => {
      const childIds = getDescendantIds(s.todos, id)
      return {
        todos: s.todos.map(t =>
          (t.id === id && !t.parentId) || childIds.includes(t.id) ? { ...t, category, updatedAt: Date.now() } : t
        )
      }
    }),

    reorderTodoInCategory: (todoId, targetCategory, targetCardId) => set((s: TodoSlice) => {
      const colTodos = s.todos.filter(t => {
        if (t.id === todoId) return false
        const tCat = t.category || ''
        return tCat === targetCategory
      }).sort((a, b) => (a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt))
      const insertIdx = targetCardId
        ? colTodos.findIndex(t => t.id === targetCardId)
        : colTodos.length
      const clamped = Math.max(0, Math.min(insertIdx, colTodos.length))
      let newSortOrder: number
      if (clamped <= 0) {
        newSortOrder = colTodos.length > 0
          ? (colTodos[0].sortOrder ?? colTodos[0].createdAt) - 1000
          : Date.now()
      } else if (clamped >= colTodos.length) {
        newSortOrder = (colTodos[colTodos.length - 1].sortOrder ?? colTodos[colTodos.length - 1].createdAt) + 1000
      } else {
        const before = colTodos[clamped - 1]
        const after = colTodos[clamped]
        newSortOrder = ((before.sortOrder ?? before.createdAt) + (after.sortOrder ?? after.createdAt)) / 2
      }
      const newCategory = targetCategory || undefined
      return {
        todos: s.todos.map(t => t.id === todoId
          ? { ...t, category: newCategory, sortOrder: newSortOrder, updatedAt: Date.now() }
          : t)
      }
    }),

    addCategory: (c) => set((s: TodoSlice) => ({ categories: [...s.categories, c] })),
    removeCategory: (id) => set((s: TodoSlice) => ({
      categories: s.categories.filter(c => c.id !== id),
      todos: s.todos.map(t => t.category === id ? { ...t, category: undefined } : t),
    })),
    renameCategory: (id, label) => set((s: TodoSlice) => ({
      categories: s.categories.map(c => c.id === id ? { ...c, label } : c),
    })),

    clearDoneTodos: () => {
      const doneTodos = get().todos.filter((t: TodoItem) => t.done)
      const allDoneIds = new Set<string>()
      for (const t of doneTodos) {
        allDoneIds.add(t.id)
        getDescendantIds(get().todos, t.id).forEach(id => allDoneIds.add(id))
      }
      for (const delId of allDoneIds) {
        const todo = get().todos.find((t: TodoItem) => t.id === delId)
        if (todo) _trashHandler({ id: delId, type: 'todo', data: todo, deletedAt: Date.now() })
      }
      set((s: TodoSlice) => ({ todos: s.todos.filter(t => !allDoneIds.has(t.id)) }))
    },
  }
)
