import { useStore } from '../../store/useStore'
import { undoManager } from '../../store/undoManager'
import { uid } from '../../utils/helpers'
import type { FloatingNote as FN } from '../../types'
import { errorStore } from '../../store/errorStore'

export function extractTodosFromContent(
  content: string | undefined,
  todos: FN['todos'],
  noteId: string,
  updateFloatingNote: (id: string, data: Partial<FN>) => void,
) {
  const plainText = (content || '').replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/[^\S\n]+/g, ' ')
  const pattern = /[-*]\s*\[([ x])\]\s*(.+?)(?=\n|$)/g
  const matches: { text: string; done: boolean }[] = []
  let match
  while ((match = pattern.exec(plainText)) !== null) {
    matches.push({ text: match[2].trim(), done: match[1] === 'x' })
  }
  if (matches.length === 0) {
    errorStore.info('未检测到待办列表格式（- [ ] 事项）')
    return
  }
  const existingIds = new Set((todos || []).map(t => t.text.trim()))
  const newTodos = matches.filter(m => !existingIds.has(m.text)).map((m, i) => ({
    id: uid(), text: m.text, done: m.done, createdAt: Date.now() + i,
  }))
  if (newTodos.length === 0) {
    errorStore.info('待办已全部提取')
    return
  }
  updateFloatingNote(noteId, { todos: [...(todos || []), ...newTodos] })
}

export function addNoteTodo(
  locked: boolean | undefined,
  todoText: string,
  todos: FN['todos'],
  noteId: string,
  updateFloatingNote: (id: string, data: Partial<FN>) => void,
  setTodoText: (v: string) => void,
) {
  if (locked) return
  const v = todoText.trim()
  if (!v) return
  const newTodos = [...(todos || []), { id: uid(), text: v, done: false, createdAt: Date.now() }]
  updateFloatingNote(noteId, { todos: newTodos })
  setTodoText('')
}

export function toggleNoteTodo(
  todoId: string,
  locked: boolean | undefined,
  todos: FN['todos'],
  noteId: string,
  updateFloatingNote: (id: string, data: Partial<FN>) => void,
  t: (key: string, params?: Record<string, string | number>) => string,
) {
  if (locked) return
  const todo = (todos || []).find(t => t.id === todoId)
  if (!todo) return
  const prevDone = todo.done
  undoManager.push(prevDone ? t('undoLabels.markUndone') : t('undoLabels.markDone'), () => {
    useStore.setState(s => ({
      floatingNotes: s.floatingNotes.map(n =>
        n.id === noteId ? {
          ...n,
          todos: (n.todos || []).map(t => t.id === todoId ? { ...t, done: prevDone } : t)
        } : n
      )
    }))
  })
  const updatedTodos = (todos || []).map(t => t.id === todoId ? { ...t, done: !t.done } : t)
  updateFloatingNote(noteId, { todos: updatedTodos })
}

export function deleteNoteTodo(
  todoId: string,
  locked: boolean | undefined,
  todos: FN['todos'],
  noteId: string,
  updateFloatingNote: (id: string, data: Partial<FN>) => void,
  t: (key: string, params?: Record<string, string | number>) => string,
) {
  if (locked) return
  const todo = (todos || []).find(t => t.id === todoId)
  if (!todo) return
  const snapshot = [...(todos || [])]
  undoManager.push(t('undoLabels.deleteSubtask'), () => {
    useStore.setState(s => ({
      floatingNotes: s.floatingNotes.map(n =>
        n.id === noteId ? { ...n, todos: snapshot } : n
      )
    }))
  })
  const updatedTodos = (todos || []).filter(t => t.id !== todoId)
  updateFloatingNote(noteId, { todos: updatedTodos })
}
