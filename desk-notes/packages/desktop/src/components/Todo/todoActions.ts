/**
 * 待办操作工具函数 — 撤销感知动作 + 上下文菜单
 */
import { useStore } from '../../store/useStore'
import { getDescendantIds } from '../../store/helpers'
import { undoManager } from '../../store/undoManager'
import type { TodoItem } from '../../types'
import type { MenuItem } from '../Common/ContextMenu'

/**
 * 撤销感知的删除待办：记录删除前的状态到撤销栈
 */
export function undoableDeleteTodo(
  id: string,
  t: (key: string) => string,
  deleteTodo: (id: string) => void,
) {
  const allIds = [id, ...getDescendantIds(useStore.getState().todos, id)]
  const todosToRestore = useStore.getState().todos.filter(t => allIds.includes(t.id))
  if (todosToRestore.length > 0) {
    undoManager.push(t('undoLabels.deleteTodo'), () => {
      useStore.setState(s => ({ todos: [...s.todos, ...todosToRestore] }))
    })
  }
  deleteTodo(id)
}

/**
 * 撤销感知的切换待办状态
 */
export function undoableToggleTodo(
  id: string,
  t: (key: string) => string,
  toggleTodo: (id: string) => void,
) {
  const todo = useStore.getState().todos.find(t => t.id === id)
  if (todo) {
    const prevDone = todo.done
    undoManager.push(prevDone ? t('undoLabels.markUndone') : t('undoLabels.markDone'), () => {
      useStore.setState(s => ({
        todos: s.todos.map(t => t.id === id ? { ...t, done: prevDone } : t),
      }))
    })
  }
  toggleTodo(id)
}

/**
 * 撤销感知的清除已完成待办
 */
export function undoableClearDone(
  t: (key: string) => string,
  clearDoneTodos: () => void,
) {
  const state = useStore.getState()
  const doneTodos = state.todos.filter(t => t.done)
  if (doneTodos.length === 0) return
  const allDoneIds = new Set<string>()
  for (const t of doneTodos) {
    allDoneIds.add(t.id)
    getDescendantIds(state.todos, t.id).forEach(id => allDoneIds.add(id))
  }
  const todosToRestore = state.todos.filter(t => allDoneIds.has(t.id))
  if (todosToRestore.length > 0) {
    undoManager.push(t('undoLabels.clearDone'), () => {
      useStore.setState(s => ({ todos: [...s.todos, ...todosToRestore] }))
    })
  }
  clearDoneTodos()
}

/**
 * 生成待办右键菜单项
 */
export function getTodoContextMenuItems(
  ctxMenu: { todoId?: string } | null,
  filtered: TodoItem[],
  t: (key: string) => string,
  setRepeatInterval: (id: string, interval: number | undefined) => void,
  onDelete: (id: string) => void,
  onQimenAnalysis?: (id: string) => void,
  onEdit?: (id: string) => void,
): MenuItem[] {
  const id = ctxMenu?.todoId
  if (!id) return []
  const todo = filtered.find(t => t.id === id)
  const currentRepeat = todo?.repeatInterval
  const editItem: MenuItem[] = onEdit ? [
    {
      label: t('common.edit'),
      icon: 'fa-pen',
      onClick: () => onEdit(id),
    },
  ] : []
  const base: MenuItem[] = [
    {
      label: t('todo.copyText'),
      icon: 'fa-copy',
      onClick: () => {
        const t = filtered.find(t => t.id === id)
        if (t) navigator.clipboard.writeText(t.text)
      },
    },
  ]
  const repeatOpts: MenuItem[] = [
    { label: `${currentRepeat ? '○ ' : '● '}${t('todo.repeat.none')}`, icon: 'fa-rotate', onClick: () => setRepeatInterval(id, undefined) },
    { label: `${currentRepeat === 1 ? '● ' : '○ '}${t('todo.repeat.daily')}`, icon: 'fa-rotate', onClick: () => setRepeatInterval(id, 1) },
    { label: `${currentRepeat === 7 ? '● ' : '○ '}${t('todo.repeat.weekly')}`, icon: 'fa-rotate', onClick: () => setRepeatInterval(id, 7) },
    { label: `${currentRepeat === 30 ? '● ' : '○ '}${t('todo.repeat.monthly')}`, icon: 'fa-rotate', onClick: () => setRepeatInterval(id, 30) },
  ]
  const qimenItem: MenuItem[] = onQimenAnalysis ? [
    {
      label: '奇门遁甲分析',
      icon: 'fa-compass',
      onClick: () => onQimenAnalysis(id),
    },
  ] : []

  return [
    ...editItem,
    ...base,
    ...qimenItem,
    ...repeatOpts,
    {
      label: t('common.delete'),
      icon: 'fa-trash-can',
      danger: true,
      onClick: () => onDelete(id),
    },
  ]
}
