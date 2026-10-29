import React, { useState, useRef, useEffect, useMemo, useCallback, Suspense } from 'react'
import Icon from '../Common/Icon'

import { motion, AnimatePresence } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { shallow } from 'zustand/shallow'
import { uid } from '../../utils/helpers'
import { TodoItem } from '../../types'
import ContextMenu, { MenuItem } from '../Common/ContextMenu'
const CalendarViewLazy = React.lazy(() => import('./CalendarView'))
import { useTranslation } from '../../i18n'
import { cyclePriority, formatDueDate, tsToDateInputValue, isOverdue, isDueSoon, getSortFn } from './todoListUtils'
import SubtaskAddInput from './SubtaskAddInput'
import InlineAddInput from './InlineAddInput'
import { suggestQimenEvent } from '../../utils/astrology/qimenSuggest'
import { getAIProvider } from '../../utils/ai'
import { getLocalAIConfig } from '../../utils/aiConfig'
import QimenQuickDialog from './QimenQuickDialog'
import PomodoroTimer from './PomodoroTimer'
import { undoableDeleteTodo, undoableToggleTodo, undoableClearDone, getTodoContextMenuItems } from './todoActions'
import { buildQimenAnalysisPrompt } from './QimenAnalysisPrompt'
import SaveIndicator from '../Common/SaveIndicator'
import KanbanBoard from './KanbanBoard'
import TodoToolBar from './TodoToolBar'
import TodoStatsBar from './TodoStatsBar'
import { swallowError } from '../../utils/swallowError'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const PRIORITY_COLORS: Record<number, string> = { 0: '#f87171', 1: '#fbbf24', 2: '#64748b' }

interface TodoListProps {
  /** C1：打开右栏详情面板（TodoModule 传入） */
  onOpenDetail?: (id: string) => void
}

const TodoList: React.FC<TodoListProps> = ({ onOpenDetail }) => {
  const { t } = useTranslation()
  const {
    toggleTodo, deleteTodo, addFloatingNote, nextZ,
    searchQuery, setSearchQuery, clearDoneTodos, addingTodo,
    setPriority, setDueDate, setRepeatInterval, moveTodoToPosition, sortMode, setSortMode,
    viewMode, setViewMode, floatingNotes, focusNote, updateFloatingNote,
    filterStatus, setFilterStatus, filterPriority, toggleFilterPriority,
    setQimenNavigationTarget, setQimenEvent, aiEnabled,
    batchDeleteTodos, batchToggleTodos, batchSetPriority, editTodo,
    } = useStore(s => ({
      toggleTodo: s.toggleTodo, deleteTodo: s.deleteTodo, addFloatingNote: s.addFloatingNote, nextZ: s.nextZ,
      searchQuery: s.searchQuery, setSearchQuery: s.setSearchQuery, clearDoneTodos: s.clearDoneTodos, addingTodo: s.addingTodo,
      setPriority: s.setPriority, setDueDate: s.setDueDate, setRepeatInterval: s.setRepeatInterval, moveTodoToPosition: s.moveTodoToPosition,
      sortMode: s.sortMode, setSortMode: s.setSortMode,
      viewMode: s.viewMode, setViewMode: s.setViewMode, floatingNotes: s.floatingNotes, focusNote: s.focusNote, updateFloatingNote: s.updateFloatingNote,
      filterStatus: s.filterStatus, setFilterStatus: s.setFilterStatus, filterPriority: s.filterPriority, toggleFilterPriority: s.toggleFilterPriority,
      setQimenNavigationTarget: s.setQimenNavigationTarget, setQimenEvent: s.setQimenEvent, aiEnabled: s.aiEnabled,
      batchDeleteTodos: s.batchDeleteTodos, batchToggleTodos: s.batchToggleTodos, batchSetPriority: s.batchSetPriority,
      editTodo: s.editTodo,
    }), shallow)
  const [dragTodo, setDragTodo] = useState<{ text: string; x: number; y: number } | null>(null)
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number; todoId: string } | null>(null)
  const [qimenDialogItem, setQimenDialogItem] = useState<TodoItem | null>(null)
  const [dragFromId, setDragFromId] = useState<string | null>(null)
  const [dragInsertIdx, setDragInsertIdx] = useState<number | null>(null)
  const [datePickerId, setDatePickerId] = useState<string | null>(null)
  const dateInputRef = useRef<HTMLInputElement>(null)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const cleanupDragRef = useRef<(() => void) | null>(null)
  const [batchMode, setBatchMode] = useState(false)
  const [selectedTodoIds, setSelectedTodoIds] = useState<Set<string>>(new Set())
  const [sortAsc, setSortAsc] = useState(true)
  // ── Inline editing ──
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editText, setEditText] = useState('')

  const startEditTodo = useCallback((id: string) => {
    const todo = useStore.getState().todos.find(t => t.id === id)
    if (!todo) return
    setEditingId(id)
    setEditText(todo.text)
  }, [])

  const commitEditTodo = useCallback(() => {
    if (editingId) {
      const trimmed = editText.trim()
      if (trimmed) editTodo(editingId, trimmed)
    }
    setEditingId(null)
  }, [editingId, editText, editTodo])
  const todos = useStore(s => s.todos)
  const activeCat = useStore(s => s.activeCat)
  const filterSubject = useStore(s => s.filterSubject)
  const locale = useStore(s => s.locale)

  const filtered = useMemo(() => {
    let list = activeCat === 'all' ? todos : todos.filter(t => t.category === activeCat)
    // Text search
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase()
      list = list.filter(t => t.text.toLowerCase().includes(q))
    }
    // Subject filter
    if (filterSubject) {
      list = list.filter(t => t.subject === filterSubject)
    }
    // Status filter
    if (filterStatus === 'active') {
      list = list.filter(t => !t.done)
    } else if (filterStatus === 'completed') {
      list = list.filter(t => t.done)
    } else if (filterStatus === 'overdue') {
      list = list.filter(t => !t.done && !!t.dueDate && isOverdue(t.dueDate))
    }
    // Priority filter
    if (filterPriority.length > 0) {
      list = list.filter(t => t.priority !== undefined && filterPriority.includes(t.priority))
    }
    return list
  }, [todos, activeCat, searchQuery, filterSubject, filterStatus, filterPriority])

  // Build children map from filtered items
  const childrenMap = useMemo(() => {
    const map = new Map<string, TodoItem[]>()
    for (const t of filtered) {
      if (t.parentId) {
        if (!map.has(t.parentId)) map.set(t.parentId, [])
        map.get(t.parentId)!.push(t)
      }
    }
    for (const [, children] of map) {
      children.sort((a, b) => (a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt))
    }
    return map
  }, [filtered])

  // Root items: no parentId, or orphan (parent not in filtered)
  const rootTodos = useMemo(
    () => filtered.filter(t => !t.parentId || !filtered.some(f => f.id === t.parentId)),
    [filtered]
  )

  const rootActiveTodos = useMemo(
    () => rootTodos.filter(t => !t.done).sort(getSortFn(sortMode, sortAsc)),
    [rootTodos, sortMode, sortAsc]
  )
  const rootDoneTodos = useMemo(
    () => rootTodos.filter(t => t.done).sort((a, b) => (b.completedAt ?? b.createdAt) - (a.completedAt ?? a.createdAt)),
    [rootTodos]
  )

  // Search floating notes too
  const matchedNotes = useMemo(() => {
    if (!searchQuery.trim()) return []
    const q = searchQuery.trim().toLowerCase()
    return floatingNotes.filter(n => {
      const title = (n.title || '').toLowerCase()
      const content = (n.content || '').toLowerCase()
      return title.includes(q) || content.includes(q)
    })
  }, [floatingNotes, searchQuery])

  // Minimized floating todo notes (closed floating notes that appear in main list)
  const minimizedTodoNotes = useMemo(() => {
    return floatingNotes.filter(n => n.type === 'todo' && !n.floated && !n.archived)
  }, [floatingNotes])

  // Open date picker when it becomes active
  useEffect(() => {
    if (datePickerId && dateInputRef.current) {
      dateInputRef.current.showPicker?.()
    }
  }, [datePickerId])

  // Cleanup drag listeners on unmount
  useEffect(() => {
    return () => { cleanupDragRef.current?.() }
  }, [])

  // Stats
  const totalCount = filtered.length
  const activeCount = rootActiveTodos.length
  const doneCount = rootDoneTodos.length
  const subtaskCount = useMemo(
    () => { let c = 0; for (const t of rootTodos) { const f = (id: string) => { const kids = childrenMap.get(id); if (kids) { c += kids.length; kids.forEach(k => f(k.id)) } }; f(t.id) }; return c },
    [rootTodos, childrenMap]
  )
  const overdueCount = useMemo(
    () => todos.filter(t => !t.done && t.dueDate && isOverdue(t.dueDate)).length,
    [todos]
  )

  // --- Undo-aware action wrappers ---

  const handleUndoableDelete = useCallback((id: string) => {
    undoableDeleteTodo(id, t, deleteTodo)
  }, [deleteTodo, t])

  const handleUndoableToggle = useCallback((id: string) => {
    undoableToggleTodo(id, t, toggleTodo)
  }, [toggleTodo, t])

  const handleUndoableClearDone = useCallback(() => {
    undoableClearDone(t, clearDoneTodos)
  }, [clearDoneTodos, t])

  // --- Improved HTML5 Drag & Drop with insertion point ---
  const handleDragStart = useCallback((e: React.DragEvent, t: TodoItem) => {
    e.dataTransfer.setData('text/plain', t.id)
    e.dataTransfer.effectAllowed = 'move'
    setDragFromId(t.id)
  }, [])

  const handleListDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    if (!dragFromId) return
    const items = document.querySelectorAll('[data-drag-item]')
    let idx = rootActiveTodos.length
    for (let i = 0; i < items.length; i++) {
      const rect = items[i].getBoundingClientRect()
      if (e.clientY < rect.top + rect.height / 2) {
        idx = i
        break
      }
    }
    setDragInsertIdx(idx)
  }, [dragFromId, rootActiveTodos.length])

  const handleListDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    const id = e.dataTransfer.getData('text/plain')
    if (id && dragInsertIdx !== null && dragFromId) {
      moveTodoToPosition(id, dragInsertIdx)
    }
    setDragFromId(null)
    setDragInsertIdx(null)
  }, [dragInsertIdx, dragFromId, moveTodoToPosition])

  const handleDragEnd = useCallback(() => {
    setDragFromId(null)
    setDragInsertIdx(null)
  }, [])

  // --- Priority cycle ---
  const handlePriorityClick = useCallback((e: React.MouseEvent, id: string, current: 0 | 1 | 2 | undefined) => {
    e.stopPropagation()
    setPriority(id, cyclePriority(current))
  }, [setPriority])

  // --- Due date picker ---
  const handleDueDateClick = useCallback((e: React.MouseEvent, id: string) => {
    e.stopPropagation()
    setDatePickerId(id)
  }, [])

  const handleDateChange = useCallback((e: React.ChangeEvent<HTMLInputElement>, id: string) => {
    const val = e.target.value
    if (val) {
      setDueDate(id, new Date(val).getTime())
    } else {
      setDueDate(id, undefined)
    }
    setDatePickerId(null)
  }, [setDueDate])

  // --- Existing drag-to-create-note ---
  const handleTodoMouseDown = (e: React.MouseEvent, todo: typeof filtered[0]) => {
    if (batchMode) return
    if ((e.target as HTMLElement).closest('button') || (e.target as HTMLElement).closest('[data-drag-handle]')) return
    const startX = e.clientX
    const startY = e.clientY
    let moved = false

    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX
      const dy = ev.clientY - startY
      if (Math.sqrt(dx * dx + dy * dy) > 8 && !moved) {
        moved = true
        setDragTodo({ text: todo.text, x: ev.clientX, y: ev.clientY })
      }
      if (moved) {
        setDragTodo(g => g ? { ...g, x: ev.clientX, y: ev.clientY } : null)
      }
    }

    const onUp = (ev: MouseEvent) => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      cleanupDragRef.current = null
      setDragTodo(null)

      const dx = ev.clientX - startX
      const dy = ev.clientY - startY
      if (Math.sqrt(dx * dx + dy * dy) > 15) {
        const id = uid()
        addFloatingNote({
          id, type: 'todo', title: todo.text,
          content: todo.text,
          todos: [{ id: uid(), text: todo.text, done: todo.done, createdAt: todo.createdAt, priority: todo.priority, dueDate: todo.dueDate }],
          x: ev.clientX - 130, y: ev.clientY - 30,
          width: Math.max(220, Math.min(360, todo.text.length * 8)),
          height: 140, zIndex: nextZ, floated: true,
        })
        if (isElectron) {
          const note = useStore.getState().floatingNotes.find(n => n.id === id)
          ei.createFloatingWindow({ id, screenX: ev.clientX - 130, screenY: ev.clientY - 30, width: 260, height: 160, noteData: note })
        }
      }
    }

    cleanupDragRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const handleTodoContextMenu = (e: React.MouseEvent, todoId: string) => {
    e.preventDefault()
    e.stopPropagation()
    setCtxMenu({ x: e.clientX, y: e.clientY, todoId })
  }

  const getCtxMenuItems = useCallback((): MenuItem[] => {
    return getTodoContextMenuItems(ctxMenu, filtered, t, setRepeatInterval, handleUndoableDelete,
      (todoId) => {
        const item = filtered.find(t => t.id === todoId)
        if (item) setQimenDialogItem(item)
      },
      startEditTodo)
  }, [ctxMenu, filtered, t, setRepeatInterval, handleUndoableDelete, startEditTodo])

  // --- Qimen AI analysis callback ---
  const handleAIAnalyze = useCallback(async (text: string) => {
    if (!useStore.getState().aiEnabled) return null
    try {
      const provider = getAIProvider(getLocalAIConfig())
      const prompt = buildQimenAnalysisPrompt(text)
      const result = await provider.processText('continue' as any, prompt, () => {})
      const cleaned = result.replace(/```(?:json)?\s*([\s\S]*?)```/g, '$1').trim()
      const parsed = JSON.parse(cleaned)
      if (parsed && parsed.domain) return { domain: parsed.domain, scenario: parsed.scenario }
    } catch (e) {
      // AI parsing failed, fall through
      swallowError('TodoList:312', e)
    }
    return null
  }, [])

  // --- Render a single todo item (recursive for subtasks) ---
  const renderTodoItem = (item: TodoItem, idx: number, isDone: boolean, depth = 0) => {
    const dueOverdue = item.dueDate && !item.done && isOverdue(item.dueDate)
    const dueSoon = item.dueDate && !item.done && isDueSoon(item.dueDate)
    const pc = item.priority !== undefined ? PRIORITY_COLORS[item.priority] : undefined
    const children = childrenMap.get(item.id)
    const hasChildren = children && children.length > 0
    const isExpanded = expandedIds.has(item.id)

    // Recursive subtask count
    let totalSubtaskCount = 0
    if (hasChildren) {
      const countKids = (items: TodoItem[]): number => {
        let c = items.length
        for (const ci of items) { const g = childrenMap.get(ci.id); if (g) c += countKids(g) }
        return c
      }
      totalSubtaskCount = countKids(children!)
    }

    return (
      <React.Fragment key={item.id}>
        <motion.div
          layout
          initial={{ opacity: 0, x: isDone ? 0 : -8 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 8 }}
          transition={{ duration: 0.2 }}
          data-drag-item={(!isDone && depth === 0) ? true : undefined}
          data-testid="todo-item"
          data-todo-id={item.id}
          className={`group px-3 py-2.5 rounded-lg hover:bg-white/[0.03] transition-colors cursor-default ${isDone ? 'opacity-60' : ''} ${dragFromId && dragInsertIdx !== null && dragInsertIdx === idx && !isDone && depth === 0 ? 'border-t border-fluent-blue/40' : ''}`}
          style={{ paddingLeft: `${8 + depth * 20}px` }}
          onMouseDown={e => handleTodoMouseDown(e, item)}
          onClick={batchMode ? () => setSelectedTodoIds(prev => { const next = new Set(prev); if (next.has(item.id)) next.delete(item.id); else next.add(item.id); return next }) : undefined}
          onContextMenu={e => handleTodoContextMenu(e, item.id)}
        >
          <div className="flex items-center gap-2">
            {/* Expand/collapse chevron */}
            {hasChildren ? (
              <span
                onClick={() => {
                  setExpandedIds(prev => {
                    const next = new Set(prev)
                    if (next.has(item.id)) next.delete(item.id); else next.add(item.id)
                    return next
                  })
                  // Track expanded todo as context
                  if (!expandedIds.has(item.id)) {
                    useStore.getState().setCurrentContext('todo', null, item.id)
                  }
                }}
                className="flex-shrink-0 w-4 flex items-center justify-center text-text-dim hover:text-text-secondary transition-colors text-2xs cursor-pointer select-none"
              >
                <Icon icon={`fa-solid fa-chevron-right transition-transform ${isExpanded ? 'rotate-90' : ''}`} />
              </span>
            ) : (
              <span className="flex-shrink-0 w-4" />
            )}

            {/* Drag handle (root level only) */}
            {!isDone && depth === 0 && (
              <span
                data-drag-handle
                draggable
                onDragStart={e => handleDragStart(e, item)}
                onDragEnd={handleDragEnd}
                className="flex-shrink-0 w-5 flex items-center justify-center self-stretch text-text-dim group-hover:text-text-muted hover:text-text-secondary transition-colors text-xs cursor-grab active:cursor-grabbing select-none"
              >
                <Icon icon="fa-solid fa-grip-lines" />
              </span>
            )}

            {/* Batch selection checkbox */}
            {batchMode && (
              <span
                onClick={(e) => { e.stopPropagation(); setSelectedTodoIds(prev => { const next = new Set(prev); if (next.has(item.id)) next.delete(item.id); else next.add(item.id); return next }) }}
                className="flex-shrink-0 w-5 h-5 rounded border flex items-center justify-center cursor-pointer transition-colors"
                style={{ borderColor: selectedTodoIds.has(item.id) ? 'var(--fluent-blue)' : 'var(--text-dim)' }}
              >
                {selectedTodoIds.has(item.id) && (
                  <Icon icon="fa-solid fa-check text-[9px]" style={{ color: 'var(--fluent-blue)' }} />
                )}
              </span>
            )}

            {/* Priority dot */}
            <span
              data-testid="todo-priority"
              onClick={e => handlePriorityClick(e, item.id, item.priority)}
              className="flex-shrink-0 w-3 h-3 rounded-full cursor-pointer transition-all hover:scale-125"
              style={{
                background: pc ?? 'transparent',
                border: pc ? 'none' : '1px solid var(--text-dim)',
              }}
              title={t('todo.priority.title')}
            />

            {/* Checkbox (hidden for events) */}
            {!item.isEvent ? (
              <motion.button
                whileTap={{ scale: 0.8 }}
                onClick={() => handleUndoableToggle(item.id)}
                className="w-5 h-5 rounded border flex items-center justify-center flex-shrink-0 transition-colors"
                style={{
                  borderColor: pc ? `${pc}88` : 'var(--text-dim)',
                }}
              >
                {isDone && (
                  <svg className="w-3 h-3 text-text-secondary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                )}
              </motion.button>
            ) : (
              <span className="w-4 h-4 flex-shrink-0 flex items-center justify-center">
                <span className="w-1 h-3 rounded-full bg-purple-500/70" />
              </span>
            )}

            {/* Text (double-click to edit inline) */}
            {editingId === item.id ? (
              <input
                autoFocus
                value={editText}
                onChange={e => setEditText(e.target.value)}
                onBlur={commitEditTodo}
                onKeyDown={e => {
                  if (e.key === 'Enter') { e.preventDefault(); commitEditTodo() }
                  else if (e.key === 'Escape') { e.preventDefault(); setEditingId(null) }
                }}
                onMouseDown={e => e.stopPropagation()}
                onClick={e => e.stopPropagation()}
                className="flex-1 min-w-0 text-base leading-relaxed bg-white/[0.06] border border-fluent-blue/40 rounded px-1.5 py-0.5 outline-none text-text-primary"
              />
            ) : (
              <span
                onDoubleClick={e => { e.stopPropagation(); if (!batchMode) startEditTodo(item.id) }}
                title={t('common.edit')}
                className={`flex-1 text-base leading-relaxed min-w-0 truncate cursor-text ${isDone ? 'text-text-secondary line-through' : 'text-text-primary'}`}
              >
                {item.text}
              </span>
            )}

            {/* Due date badge */}
            {item.dueDate && (
              <span
                onClick={e => handleDueDateClick(e, item.id)}
                className={`flex-shrink-0 text-xs px-1.5 py-0.5 rounded cursor-pointer transition-colors hover:bg-white/[0.06] ${dueOverdue ? 'text-red-400' : dueSoon ? 'text-yellow-400' : 'text-text-secondary'}`}
              >
                {formatDueDate(item.dueDate)}
              </span>
            )}

            {/* No due date badge */}
            {!item.dueDate && !isDone && (
              <span
                onClick={e => handleDueDateClick(e, item.id)}
                className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded text-text-dim hover:text-text-muted hover:bg-white/[0.06] cursor-pointer transition-colors"
              >
                <Icon icon="fa-regular fa-calendar" />
              </span>
            )}

            {/* Repeat interval badge */}
            {item.repeatInterval && (
              <span className="flex-shrink-0 text-2xs text-fluent-blue/60" title={
                item.repeatInterval === 1 ? t('todo.repeat.daily') :
                item.repeatInterval === 7 ? t('todo.repeat.weekly') :
                item.repeatInterval === 30 ? t('todo.repeat.monthly') :
                `${t('todo.repeat.title')} ${item.repeatInterval}d`
              }>
                <Icon icon="fa-solid fa-rotate" />
              </span>
            )}

            {/* C1: detail panel button */}
            {onOpenDetail && (
              <button
                data-testid="todo-detail-btn"
                onClick={e => { e.stopPropagation(); onOpenDetail(item.id) }}
                className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-text-dim hover:text-text-secondary opacity-0 group-hover:opacity-100 transition-all"
                title={t('todoListBatch.detail')}
              >
                <Icon icon="fa-solid fa-circle-info text-xs" />
              </button>
            )}

            {/* Pomodoro timer (hidden for events) */}
            {!item.isEvent && <PomodoroTimer todoId={item.id} />}

            {/* Event tag */}
            {item.isEvent && (
              <span className="flex-shrink-0 text-2xs px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400">
                {t('todoListBatch.eventTag')}
              </span>
            )}

            {/* Time */}
            <span className="text-xs text-white/45 flex-shrink-0 hidden sm:inline">
              {new Date(item.createdAt).toLocaleDateString(locale === 'en' ? 'en-US' : 'zh-CN')} {new Date(item.createdAt).toLocaleTimeString(locale === 'en' ? 'en-US' : 'zh-CN', { hour: '2-digit', minute: '2-digit' })}
            </span>

            {/* Delete */}
            <button
              data-testid="todo-delete-btn"
              onClick={() => handleUndoableDelete(item.id)}
              className="opacity-0 group-hover:opacity-100 text-text-dim hover:text-red-400/60 transition-all text-xs px-1 flex-shrink-0"
            >
              <Icon icon="fa-solid fa-xmark" />
            </button>
          </div>

          {/* Hidden datetime-local input for picker (supports date + time) */}
          {datePickerId === item.id && (
            <input
              ref={dateInputRef}
              type="datetime-local"
              className="absolute opacity-0 pointer-events-none w-0 h-0"
              value={item.dueDate ? tsToDateInputValue(item.dueDate) : ''}
              onChange={e => handleDateChange(e, item.id)}
              onBlur={() => setDatePickerId(null)}
            />
          )}
        </motion.div>

        {/* Expanded children */}
        {hasChildren && isExpanded && (
          <>
            {children!.map(child => renderTodoItem(child, idx, child.done, depth + 1))}
            <SubtaskAddInput parentId={item.id} depth={depth} />
          </>
        )}

        {/* Collapsed subtask count badge */}
        {hasChildren && !isExpanded && (
          <div style={{ paddingLeft: `${28 + depth * 20}px` }} className="flex items-center gap-1 pb-1">
            <span className="text-2xs text-text-dim">
              <Icon icon="fa-solid fa-list-ul mr-1" />
              {t('todo.subtaskCount', { count: totalSubtaskCount })}
            </span>
          </div>
        )}
      </React.Fragment>
    )
  }

  return (
    <>
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <TodoToolBar
        searchQuery={searchQuery}
        setSearchQuery={setSearchQuery}
        viewMode={viewMode}
        setViewMode={setViewMode}
        sortMode={sortMode}
        setSortMode={setSortMode}
        filterStatus={filterStatus}
        setFilterStatus={setFilterStatus}
        filterPriority={filterPriority}
        toggleFilterPriority={toggleFilterPriority}
        t={t}
        batchMode={batchMode}
        onToggleBatch={() => {
          if (batchMode) setSelectedTodoIds(new Set())
          setBatchMode(!batchMode)
        }}
        sortAsc={sortAsc}
        onToggleSortDir={() => setSortAsc(!sortAsc)}
      />

      {/* Matched floating notes */}
      {searchQuery.trim() && matchedNotes.length > 0 && (
        <div className="px-3 pb-1">
          <div className="flex items-center gap-1.5 mb-1">
            <Icon icon="fa-regular fa-note-sticky text-2xs text-text-muted" />
            <span className="text-2xs text-text-muted">{t('todo.relatedNotes')} ({matchedNotes.length})</span>
          </div>
          <div className="flex flex-wrap gap-1">
            {matchedNotes.map(n => (
              <button
                key={n.id}
                onClick={() => focusNote(n.id)}
                className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs text-text-secondary hover:text-white bg-white/[0.04] hover:bg-white/[0.06] transition-all truncate max-w-[180px]"
                title={n.title}
              >
                <Icon icon="fa-regular fa-note-sticky text-[8px] text-text-muted flex-shrink-0" />
                <span className="truncate">{n.title}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Inline add input */}
      {addingTodo && viewMode === 'list' && <InlineAddInput />}

      {/* Content: Kanban board, Calendar, or List */}
      {viewMode === 'kanban' ? (
        <KanbanBoard />
      ) : viewMode === 'calendar' ? (
        <div className="flex-1 overflow-y-auto">
          <Suspense fallback={<div className="h-8" />}>
            <CalendarViewLazy />
          </Suspense>
        </div>
      ) : (
      <div className="flex-1 overflow-y-auto px-1.5"
        onDragOver={handleListDragOver}
        onDrop={handleListDrop}
      >
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-text-dim">
            {searchQuery.trim() ? (
              <>
                <Icon icon="fa-solid fa-magnifying-glass text-lg mb-2" />
                <span className="text-xs">{t('todo.noMatch')}</span>
              </>
            ) : (
              <>
                <Icon icon="fa-regular fa-pen-to-square text-lg mb-3" />
                <span className="text-xs">{t('todo.noTodos')}</span>
              </>
            )}
          </div>
        ) : (
        <>
        {/* Active root items */}
        <AnimatePresence mode="popLayout">
          {rootActiveTodos.map((t, idx) => {
            // Insertion line before this item (root level only)
            const showLine = dragFromId && dragInsertIdx === idx && dragFromId !== t.id
            return (
              <React.Fragment key={t.id}>
                {showLine && <div className="h-0.5 rounded-full bg-fluent-blue/50 mx-2 mb-0.5" />}
                {renderTodoItem(t, idx, false)}
              </React.Fragment>
            )
          })}
        </AnimatePresence>
        {/* Insertion line at end of root active list */}
        {dragFromId && dragInsertIdx === rootActiveTodos.length && rootActiveTodos.length > 0 && (
          <div className="h-0.5 rounded-full bg-fluent-blue/50 mx-2 mt-0.5" />
        )}

        {/* Separator + done items */}
        {rootDoneTodos.length > 0 && (
          <>
            <div className="flex items-center gap-2 mx-3 my-1.5">
              <div className="flex-1 h-px bg-white/[0.04]" />
              <span className="text-2xs text-text-dim">{t('todo.itemsCompleted', { count: rootDoneTodos.length })}</span>
              <button onClick={handleUndoableClearDone}
                className="text-2xs text-text-dim hover:text-red-400/50 transition-colors">
                {t('todo.clear')}
              </button>
              <div className="flex-1 h-px bg-white/[0.04]" />
            </div>
            <AnimatePresence>
              {rootDoneTodos.map((t, idx) => renderTodoItem(t, idx, true))}
            </AnimatePresence>
          </>
        )}

        {/* Minimized floating todo notes */}
        {minimizedTodoNotes.length > 0 && (
          <>
            <div className="flex items-center gap-2 mx-3 my-1.5">
              <div className="flex-1 h-px bg-white/[0.04]" />
              <span className="text-2xs text-text-dim">{t('todo.floatingItems')}</span>
              <div className="flex-1 h-px bg-white/[0.04]" />
            </div>
            {minimizedTodoNotes.map(fn => (
              <div key={fn.id}
                onClick={() => { focusNote(fn.id); updateFloatingNote(fn.id, { floated: true }) }}
                className="flex items-center gap-2 px-3 py-2 mx-1 rounded-lg cursor-pointer transition-all hover:bg-white/[0.03]"
                style={{ color: 'var(--text-secondary)' }}>
                <Icon icon="fa-solid fa-note-sticky text-2xs text-text-dim" />
                <span className="flex-1 text-xs truncate">{fn.title || t('note.newNote')}</span>
                <span className="text-[8px] text-text-dim">{t('todo.clickToRestore')}</span>
              </div>
            ))}
          </>
        )}
        </>
      )}
      </div>
      )}
    </div>

      {/* Batch action bar */}
      {batchMode ? (
        <div className="flex items-center gap-2 px-3 py-2 border-t border-white/[0.04] bg-white/[0.02]">
          <span className="text-xs text-text-secondary min-w-[60px]">
            {t('todoListBatch.selectedFmt', { count: selectedTodoIds.size })}
          </span>
          <div className="flex-1" />
          <button
            onClick={() => {
              const ids = Array.from(selectedTodoIds)
              batchToggleTodos(ids, true)
              setSelectedTodoIds(new Set())
            }}
            disabled={selectedTodoIds.size === 0}
            className="text-2xs px-2 py-1 rounded-lg transition-colors disabled:opacity-30 text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]"
          >
            <Icon icon="fa-solid fa-check mr-1" />{t('todoListBatch.complete')}
          </button>
          <button
            onClick={() => {
              const ids = Array.from(selectedTodoIds)
              batchToggleTodos(ids, false)
              setSelectedTodoIds(new Set())
            }}
            disabled={selectedTodoIds.size === 0}
            className="text-2xs px-2 py-1 rounded-lg transition-colors disabled:opacity-30 text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]"
          >
            <Icon icon="fa-solid fa-rotate-left mr-1" />{t('todoListBatch.reopen')}
          </button>
          <button
            onClick={() => {
              const ids = Array.from(selectedTodoIds)
              batchSetPriority(ids, 0)
              setSelectedTodoIds(new Set())
            }}
            disabled={selectedTodoIds.size === 0}
            className="text-2xs px-2 py-1 rounded-lg transition-colors disabled:opacity-30 text-red-400/60 hover:text-red-400 hover:bg-white/[0.04]"
          >
            P0
          </button>
          <button
            onClick={() => {
              const ids = Array.from(selectedTodoIds)
              batchSetPriority(ids, 1)
              setSelectedTodoIds(new Set())
            }}
            disabled={selectedTodoIds.size === 0}
            className="text-2xs px-2 py-1 rounded-lg transition-colors disabled:opacity-30 text-yellow-400/60 hover:text-yellow-400 hover:bg-white/[0.04]"
          >
            P1
          </button>
          <button
            onClick={() => {
              const ids = Array.from(selectedTodoIds)
              batchSetPriority(ids, undefined)
              setSelectedTodoIds(new Set())
            }}
            disabled={selectedTodoIds.size === 0}
            className="text-2xs px-2 py-1 rounded-lg transition-colors disabled:opacity-30 text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]"
          >
            <Icon icon="fa-solid fa-circle-minus mr-1" />{t('todoListBatch.clearPriority')}
          </button>
          <button
            onClick={() => {
              if (!confirm(t('todoListBatch.confirmDeleteFmt', { count: selectedTodoIds.size }))) return
              batchDeleteTodos(Array.from(selectedTodoIds))
              setSelectedTodoIds(new Set())
            }}
            disabled={selectedTodoIds.size === 0}
            className="text-2xs px-2 py-1 rounded-lg transition-colors disabled:opacity-30 text-red-400/60 hover:text-red-400 hover:bg-white/[0.04]"
          >
            <Icon icon="fa-solid fa-trash-can mr-1" />{t('todoListBatch.delete')}
          </button>
        </div>
      ) : (
      <div className="flex items-center gap-2 px-3 py-2 border-t border-white/[0.04] bg-white/[0.02]">
        <SaveIndicator />
        <TodoStatsBar
          totalCount={totalCount}
          subtaskCount={subtaskCount}
          activeCount={activeCount}
          doneCount={doneCount}
          overdueCount={overdueCount}
          onClearDone={handleUndoableClearDone}
          t={t}
        />
      </div>
      )}
      {ctxMenu && (
        <ContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          items={getCtxMenuItems()}
          onClose={() => setCtxMenu(null)}
        />
      )}

      {/* Drag ghost */}
      {dragTodo && (
        <div
          className="fixed pointer-events-none z-[9999] text-xs px-2.5 py-1.5 rounded-lg shadow-2xl border border-white/[0.08] text-text-primary"
          style={{ left: dragTodo.x - 20, top: dragTodo.y - 16, maxWidth: 220, background: 'var(--panel-bg-solid)' }}
        >
          {dragTodo.text}
        </div>
      )}

      {/* Qimen Quick Dialog */}
      {qimenDialogItem && (
        <QimenQuickDialog
          todoText={qimenDialogItem.text}
          todoId={qimenDialogItem.id}
          existingEvent={qimenDialogItem.qimenEvent}
          onNavigateToCalendar={() => {
            if (qimenDialogItem.qimenEvent) {
              setQimenNavigationTarget({ todoId: qimenDialogItem.id })
            } else {
              const suggested = suggestQimenEvent(qimenDialogItem.text)
              setQimenNavigationTarget({
                domain: suggested?.domain || 'career',
                scenario: suggested?.scenario,
                text: qimenDialogItem.text,
              })
            }
            setQimenDialogItem(null)
            setViewMode('calendar')
          }}
          onSave={(event) => {
            setQimenEvent(qimenDialogItem.id, event)
            setQimenDialogItem(null)
          }}
          onClose={() => setQimenDialogItem(null)}
          aiEnabled={aiEnabled}
          onAIAnalyze={handleAIAnalyze}
          t={t}
        />
      )}
    </>
  )
}

export default React.memo(TodoList)
