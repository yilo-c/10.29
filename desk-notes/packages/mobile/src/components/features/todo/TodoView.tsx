import { useState, useMemo, useEffect } from 'react'
import { useStore, type TodoItem as TodoItemType } from '@desk-notes/shared'
import { Chip } from '../../ui'
import DateHeader from './DateHeader'
import AlmanacStrip from './AlmanacStrip'
import TodoItem from './TodoItem'
import NewTodoSheet from './NewTodoSheet'
import TodoActionSheet from './TodoActionSheet'
import PomodoroBar from './PomodoroBar'

/** Check if a timestamp falls within today */
function isToday(ts: number): boolean {
  const d = new Date()
  const start = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const end = start + 86400000
  return ts >= start && ts < end
}

type TodoFilter = 'all' | 'today'

export default function TodoView() {
  const [showNew, setShowNew] = useState(false)
  const [actionItem, setActionItem] = useState<TodoItemType | null>(null)
  const [pomodoroItem, setPomodoroItem] = useState<{ id: string; text: string } | null>(null)
  const [todoFilter, setTodoFilter] = useState<TodoFilter>('today')

  const todos = useStore(s => s.todos)
  const categories = useStore(s => s.categories)
  const activeCat = useStore(s => s.activeCat)
  const setCategory = useStore(s => s.setCategory)

  const todayCount = useMemo(() =>
    todos.filter(t => !t.done && t.dueDate && isToday(t.dueDate)).length,
    [todos],
  )

  const filtered = useMemo(() => {
    const catFiltered = activeCat === 'all'
      ? todos
      : todos.filter(t => (t.category || '') === activeCat)

    if (todoFilter === 'today') {
      return catFiltered.filter(t => t.dueDate && isToday(t.dueDate))
    }
    return catFiltered
  }, [todos, activeCat, todoFilter])

  // Auto-switch from "today" to "all" when there are no today items
  useEffect(() => {
    if (todoFilter === 'today' && todayCount === 0) {
      setTodoFilter('all')
    }
  }, [todoFilter, todayCount])

  const pendingTodos = useMemo(() => filtered.filter(t => !t.done), [filtered])
  const doneTodos = useMemo(() => filtered.filter(t => t.done), [filtered])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <DateHeader />
      <AlmanacStrip />

      {/* Category + filter chips */}
      <div
        style={{
          display: 'flex',
          gap: 6,
          padding: 'var(--space-3) var(--space-4)',
          overflowX: 'auto',
          flexShrink: 0,
          WebkitOverflowScrolling: 'touch',
          scrollbarWidth: 'none',
        }}
      >
        <Chip
          type="filter"
          selected={todoFilter === 'today'}
          label="今日"
          count={todayCount}
          onClick={() => setTodoFilter('today')}
        />
        <Chip
          type="filter"
          selected={activeCat === 'all' && todoFilter === 'all'}
          label="全部"
          count={todos.filter(t => !t.done).length}
          onClick={() => { setCategory('all'); setTodoFilter('all') }}
        />
        {categories.map(cat => (
          <Chip
            key={cat.id}
            type="filter"
            selected={activeCat === cat.id && todoFilter === 'all'}
            label={cat.label}
            count={todos.filter(t => !t.done && (t.category || '') === cat.id).length}
            onClick={() => { setCategory(cat.id); setTodoFilter('all') }}
          />
        ))}
      </div>

      {/* Todo list */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          WebkitOverflowScrolling: 'touch',
        }}
      >
        {pendingTodos.length === 0 && doneTodos.length === 0 && (
          <div
            style={{
              textAlign: 'center',
              color: 'var(--text-tertiary)',
              marginTop: 80,
              fontSize: 'var(--text-small)',
            }}
          >
            今天还没有待办
          </div>
        )}

        {pendingTodos.map(todo => (
          <TodoItem
            key={todo.id}
            item={todo}
            onClick={setActionItem}
            onLongPress={setActionItem}
          />
        ))}

        {doneTodos.length > 0 && (
          <div
            style={{
              padding: '16px var(--space-4) 8px',
              fontSize: 'var(--text-small)',
              color: 'var(--text-tertiary)',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <span style={{ flex: 1, height: 1, background: 'var(--border-subtle)' }} />
            已完成
            <span style={{ flex: 1, height: 1, background: 'var(--border-subtle)' }} />
          </div>
        )}

        {doneTodos.map(todo => (
          <TodoItem
            key={todo.id}
            item={todo}
            onClick={setActionItem}
            onLongPress={setActionItem}
          />
        ))}

        <div style={{ height: 80 }} />
      </div>

      {/* Pomodoro bar */}
      {pomodoroItem && (
        <PomodoroBar
          todoId={pomodoroItem.id}
          todoText={pomodoroItem.text}
          onClose={() => setPomodoroItem(null)}
        />
      )}

      {/* FAB */}
      <button
        onClick={() => setShowNew(true)}
        aria-label="新建待办"
        style={{
          position: 'fixed',
          bottom: 'calc(72px + var(--safe-bottom))',
          right: 20,
          width: 52,
          height: 52,
          borderRadius: '50%',
          background: 'var(--gold-primary)',
          color: '#0a0a12',
          border: 'none',
          fontSize: 26,
          cursor: 'pointer',
          boxShadow: '0 4px 16px rgba(201, 167, 92, 0.3)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 10,
          transition: 'transform var(--duration-fast) var(--ease-out)',
        }}
        onMouseDown={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(0.92)' }}
        onMouseUp={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)' }}
        onMouseLeave={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)' }}
      >
        +
      </button>

      {/* Sheets */}
      <NewTodoSheet
        open={showNew}
        onClose={() => setShowNew(false)}
        category={activeCat !== 'all' ? activeCat : undefined}
      />
      <TodoActionSheet
        item={actionItem}
        open={!!actionItem}
        onClose={() => setActionItem(null)}
        onStartPomodoro={(id, text) => {
          setActionItem(null)
          setPomodoroItem({ id, text })
        }}
      />
    </div>
  )
}
