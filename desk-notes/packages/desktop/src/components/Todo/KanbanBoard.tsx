import React, { useState, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import type { TodoItem } from '../../types'
import { swallowError } from '../../utils/swallowError'

/* ─── helpers ─── */

const PRIORITY_CONFIG: Record<number, { color: string; bg: string }> = {
  0: { color: '#f87171', bg: 'rgba(248,113,113,0.15)' },
  1: { color: '#fbbf24', bg: 'rgba(251,191,36,0.15)' },
  2: { color: '#64748b', bg: 'rgba(100,116,139,0.15)' },
}

function isOverdue(dueDate?: number): boolean {
  return !!dueDate && dueDate < Date.now()
}

function formatDate(ts?: number): string {
  if (!ts) return ''
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

/* ─── KanbanCard ─── */

interface KanbanCardProps {
  todo: TodoItem
  columnId: string
  onDragStart: (id: string, fromCol: string) => void
}

const KanbanCard: React.FC<KanbanCardProps> = ({ todo, columnId, onDragStart }) => {
  const toggleTodo = useStore(s => s.toggleTodo)
  const overdue = !todo.done && isOverdue(todo.dueDate)
  const pc = todo.priority !== undefined ? PRIORITY_CONFIG[todo.priority] : null

  return (
    <div
      draggable
      onDragStart={(e) => { e.dataTransfer.setData('text/plain', JSON.stringify({ id: todo.id, fromCol: columnId })); onDragStart(todo.id, columnId) }}
      className="group rounded-lg px-2.5 py-2 cursor-grab active:cursor-grabbing transition-shadow"
      style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.06)' }}
    >
      <div className="flex items-start gap-2">
        {/* Checkbox */}
        <button
          onClick={() => toggleTodo(todo.id)}
          className={`mt-0.5 w-3.5 h-3.5 rounded border flex-shrink-0 flex items-center justify-center text-[7px] transition-colors ${
            todo.done ? 'bg-green-500/30 border-green-500/40 text-green-400' : 'border-white/20 hover:border-white/40'
          }`}
        >
          {todo.done && <Icon icon="fa-solid fa-check" />}
        </button>

        {/* Content */}
        <div className="flex-1 min-w-0">
          <span className={`text-xs leading-snug ${todo.done ? 'line-through text-text-muted' : 'text-white/85'}`}>
            {todo.text}
          </span>

          {/* Meta row */}
          <div className="flex items-center gap-1.5 mt-1 flex-wrap">
            {pc && (
              <span className="text-[8px] px-1 rounded" style={{ color: pc.color, background: pc.bg }}>
                P{todo.priority}
              </span>
            )}
            {todo.dueDate && (
              <span className={`text-[8px] ${overdue ? 'text-red-400' : 'text-text-muted'}`}>
                <Icon icon="fa-regular fa-calendar mr-0.5" />
                {formatDate(todo.dueDate)}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

/* ─── KanbanColumn ─── */

interface KanbanColumnProps {
  columnId: string
  label: string
  color: string
  todos: TodoItem[]
  icon: string
  onDragStart: (id: string, fromCol: string) => void
  onDrop: (todoId: string, toCol: string) => void
}

const KanbanColumn: React.FC<KanbanColumnProps> = ({ columnId, label, color, todos, icon, onDragStart, onDrop }) => {
  const [dragOver, setDragOver] = useState(false)

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(true)
  }, [])

  const handleDragLeave = useCallback(() => setDragOver(false), [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    try {
      const data = JSON.parse(e.dataTransfer.getData('text/plain'))
      if (data.fromCol !== columnId) onDrop(data.id, columnId)
    } catch (e) { /* ignore */ swallowError('KanbanBoard:109', e) }
  }, [columnId, onDrop])

  return (
    <div
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className="flex-shrink-0 w-56 flex flex-col rounded-xl transition-colors"
      style={{
        background: dragOver ? 'rgba(255,255,255,0.06)' : 'rgba(255,255,255,0.02)',
        border: `1px solid ${dragOver ? 'rgba(255,255,255,0.15)' : 'rgba(255,255,255,0.04)'}`,
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
        <Icon icon={`${icon} text-xs`} style={{ color }} />
        <span className="text-xs font-medium text-text-secondary flex-1 truncate">{label}</span>
        <span className="text-2xs text-text-muted tabular-nums">{todos.length}</span>
      </div>

      {/* Cards */}
      <div className="flex-1 overflow-y-auto px-2 py-2 space-y-1.5 min-h-[60px]">
        {todos.length === 0 && (
          <div className="text-2xs text-text-dim text-center py-4">空</div>
        )}
        {todos.map(todo => (
          <KanbanCard key={todo.id} todo={todo} columnId={columnId} onDragStart={onDragStart} />
        ))}
      </div>
    </div>
  )
}

/* ─── KanbanBoard (main) ─── */

const KanbanBoard: React.FC = () => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const categories = useStore(s => s.categories)
  const moveTodoToCategory = useStore(s => s.moveTodoToCategory)
  const searchQuery = useStore(s => s.searchQuery)

  const handleDrop = useCallback((todoId: string, toCol: string) => {
    moveTodoToCategory(todoId, toCol)
  }, [moveTodoToCategory])

  // Group todos by category
  const byCategory = new Map<string, TodoItem[]>()

  const filtered = searchQuery.trim()
    ? todos.filter(t => (t.text || '').toLowerCase().includes(searchQuery.toLowerCase()))
    : todos

  for (const todo of filtered) {
    const cat = todo.category || '__uncategorized__'
    if (!byCategory.has(cat)) byCategory.set(cat, [])
    byCategory.get(cat)!.push(todo)
  }

  return (
    <div className="flex-1 flex gap-3 px-3 pb-3 overflow-x-auto overflow-y-hidden min-h-0">
      {categories.map(cat => {
        const colTodos = byCategory.get(cat.id) || []
        return (
          <KanbanColumn
            key={cat.id}
            columnId={cat.id}
            label={cat.label}
            color={cat.color}
            icon={cat.icon || 'fa-solid fa-layer-group'}
            todos={colTodos}
            onDragStart={() => {}}
            onDrop={handleDrop}
          />
        )
      })}

      {/* Uncategorized column */}
      <KanbanColumn
        columnId="__uncategorized__"
        label={t('todo.all') || '其他'}
        color="rgba(255,255,255,0.3)"
        icon="fa-solid fa-inbox"
        todos={byCategory.get('__uncategorized__') || []}
        onDragStart={() => {}}
        onDrop={handleDrop}
      />
    </div>
  )
}

export default KanbanBoard
