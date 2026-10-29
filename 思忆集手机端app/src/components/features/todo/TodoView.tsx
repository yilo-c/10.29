import { useState, useMemo, useCallback, useRef } from 'react'
import { useStore } from '@desk-notes/shared'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Chip, IconPlus, IconSearch, IconX, IconTodo, IconChevronDown } from '../../ui'
import TodoItem from './TodoItem'
import NewTodoSheet from './NewTodoSheet'
import TodoActionSheet from './TodoActionSheet'
import ReminderBar from './ReminderBar'
import DateHeader from './DateHeader'
import AlmanacStrip from './AlmanacStrip'
import TodoBriefingCard from './TodoBriefingCard'
import EmptyState from './EmptyState'
import MorningBriefingCard from './MorningBriefingCard'
import { useReminderScheduler } from '../../../hooks/useReminderScheduler'
import type { TodoItem as TodoItemType } from '@desk-notes/shared'
import { haptic, touchScale } from '../../../lib/haptic'

type SortMode = 'created' | 'priority' | 'dueDate' | 'alpha'
const SORT_OPTIONS: { value: SortMode; label: string }[] = [
  { value: 'created', label: '创建时间' },
  { value: 'priority', label: '优先级' },
  { value: 'dueDate', label: '截止日期' },
  { value: 'alpha', label: '名称' },
]

function isToday(ts: number): boolean {
  const d = new Date()
  const start = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  return ts >= start && ts < start + 86400000
}

type TodoFilter = 'all' | 'today'
type StatusFilter = 'all' | 'active' | 'completed' | 'overdue'

export default function TodoView() {
  const [showNew, setShowNew] = useState(false)
  const [actionItem, setActionItem] = useState<TodoItemType | null>(null)
  const [todoFilter, setTodoFilter] = useState<TodoFilter>('all')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [searchQuery, setSearchQuery] = useState('')

  const todos = useStore(s => s.todos)
  const categories = useStore(s => s.categories)
  const activeCat = useStore(s => s.activeCat)
  const setCategory = useStore(s => s.setCategory)
  const toggleTodo = useStore(s => s.toggleTodo)
  const deleteTodo = useStore(s => s.deleteTodo)
  const { activeReminder, snooze, dismiss } = useReminderScheduler()

  const [batchMode, setBatchMode] = useState(false)
  const [selected, setSelected] = useState(new Set<string>())
  const [sortMode, setSortMode] = useState<SortMode>('created')
  const [showSortPicker, setShowSortPicker] = useState(false)
  const [showSearch, setShowSearch] = useState(false)

  const clearDoneTodos = useStore(s => s.clearDoneTodos)

  const todayCount = useMemo(() =>
    todos.filter(t => !t.done && t.dueDate && isToday(t.dueDate)).length,
    [todos],
  )

  const filtered = useMemo(() => {
    let list = activeCat === 'all'
      ? todos
      : todos.filter(t => (t.category || '') === activeCat)

    if (todoFilter === 'today') {
      list = list.filter(t => t.dueDate && isToday(t.dueDate))
    }

    if (statusFilter === 'active') {
      list = list.filter(t => !t.done)
    } else if (statusFilter === 'completed') {
      list = list.filter(t => t.done)
    } else if (statusFilter === 'overdue') {
      const now = Date.now()
      list = list.filter(t => !t.done && t.dueDate && t.dueDate < now && !isToday(t.dueDate))
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      list = list.filter(t => (t.text || '').toLowerCase().includes(q))
    }

    // Sort
    const sorted = [...list].sort((a, b) => {
      switch (sortMode) {
        case 'priority': return (a.priority ?? 2) - (b.priority ?? 2)
        case 'dueDate': return (a.dueDate ?? Infinity) - (b.dueDate ?? Infinity)
        case 'alpha': return a.text.localeCompare(b.text)
        default: return (b.sortOrder ?? b.createdAt) - (a.sortOrder ?? a.createdAt)
      }
    })
    return sorted
  }, [todos, activeCat, todoFilter, statusFilter, searchQuery, sortMode])

  const pendingTodos = useMemo(() => filtered.filter(t => !t.done), [filtered])
  const doneTodos = useMemo(() => filtered.filter(t => t.done), [filtered])

  const overdueCount = useMemo(() =>
    todos.filter(t => {
      if (t.done) return false
      if (!t.dueDate) return false
      const now = Date.now()
      return t.dueDate < now && !isToday(t.dueDate)
    }).length,
    [todos],
  )

  // Build flat virtual rows: items + optional "已完成" header
  const rows = useMemo(() => {
    const r: ({ type: 'item'; todo: TodoItemType } | { type: 'header'; count: number })[] = []
    for (const t of pendingTodos) r.push({ type: 'item' as const, todo: t })
    if (doneTodos.length > 0) {
      r.push({ type: 'header' as const, count: doneTodos.length })
      for (const t of doneTodos) r.push({ type: 'item' as const, todo: t })
    }
    return r
  }, [pendingTodos, doneTodos])

  const pullRef = useRef<HTMLDivElement>(null)

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: useCallback(() => pullRef.current, []),
    estimateSize: useCallback((i: number) => rows[i]?.type === 'header' ? 36 : 52, [rows]),
    overscan: 5,
  })

  const [pullY, setPullY] = useState(0)
  const pullStart = useRef(0)
  const isPulling = useRef(false)

  const onPullStart = useCallback((e: React.TouchEvent) => {
    if (pullRef.current && pullRef.current.scrollTop <= 0) {
      pullStart.current = e.touches[0].clientY
      isPulling.current = true
    }
  }, [])

  const onPullMove = useCallback((e: React.TouchEvent) => {
    if (!isPulling.current) return
    const dy = e.touches[0].clientY - pullStart.current
    if (dy > 0) {
      setPullY(Math.min(dy * 0.4, 60))
    }
  }, [])

  const onPullEnd = useCallback(() => {
    if (pullY > 50) {
      navigator.vibrate?.(15)
    }
    setPullY(0)
    isPulling.current = false
  }, [pullY])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <DateHeader onSearchToggle={() => setShowSearch(v => !v)} showSearch={showSearch} />
      <MorningBriefingCard />
      <TodoBriefingCard onQuickAdd={() => setShowNew(true)} />
      <AlmanacStrip />
      {showSearch && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '4px 20px 4px', flexShrink: 0,
          animation: 'fadeInUp 0.15s ease',
        }}>
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', gap: 6,
            background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)',
            padding: '0 10px', minHeight: 36,
          }}>
            <span style={{ color: 'var(--text-disabled)', display: 'flex' }}>
              <IconSearch size={15} />
            </span>
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="搜索待办..."
              autoFocus
              style={{
                flex: 1, border: 'none', background: 'transparent',
                color: 'var(--text-primary)',
                fontSize: 'var(--text-footnote)', outline: 'none',
                padding: '6px 0',
              }}
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery('')}
                aria-label="清除搜索"
                style={{
                  background: 'none', border: 'none',
                  color: 'var(--text-disabled)', cursor: 'pointer',
                  display: 'flex', padding: 2,
                }}
              >
                <IconX size={13} />
              </button>
            )}
          </div>
        </div>
      )}

      <div style={{
        display: 'flex',
        gap: 6,
        padding: '8px 20px 8px',
        overflowX: 'auto',
        flexShrink: 0,
        scrollbarWidth: 'none',
      }}>
        <Chip
          type="filter"
          selected={todoFilter === 'today'}
          label="今日"
          count={todayCount || undefined}
          onClick={() => setTodoFilter('today')}
        />
        <Chip
          type="filter"
          selected={activeCat === 'all' && todoFilter === 'all'}
          label="全部"
          count={todos.filter(t => !t.done).length || undefined}
          onClick={() => { setCategory('all'); setTodoFilter('all') }}
        />
        {categories.map(cat => (
          <Chip
            key={cat.id}
            type="filter"
            selected={activeCat === cat.id && todoFilter === 'all'}
            label={cat.label}
            onClick={() => { setCategory(cat.id); setTodoFilter('all') }}
          />
        ))}
      </div>

      {/* Status filter chips */}
      <div style={{
        display: 'flex', gap: 6,
        padding: '0 20px 8px',
        overflowX: 'auto', flexShrink: 0,
        scrollbarWidth: 'none',
      }}>
        {([
          { value: 'active' as StatusFilter, label: '待完成', count: todos.filter(t => !t.done).length },
          { value: 'completed' as StatusFilter, label: '已完成', count: todos.filter(t => t.done).length },
          { value: 'overdue' as StatusFilter, label: '逾期', count: overdueCount },
        ]).map(sf => (
          <Chip
            key={sf.value}
            type="filter"
            selected={statusFilter === sf.value}
            label={sf.label}
            count={sf.count || undefined}
            onClick={() => setStatusFilter(sf.value)}
          />
        ))}
        {pendingTodos.length > 0 && (
          <button
            onClick={() => { setBatchMode(!batchMode); setSelected(new Set()) }}
            style={{
              marginLeft: 'auto', padding: '4px 10px',
              background: batchMode ? 'var(--accent-surface)' : 'none',
              border: `1px solid ${batchMode ? 'var(--accent)' : 'var(--border-default)'}`,
              borderRadius: 'var(--radius-sm)',
              color: batchMode ? 'var(--accent)' : 'var(--text-tertiary)',
              fontSize: 'var(--text-caption-1)', fontWeight: 500,
              cursor: 'pointer', whiteSpace: 'nowrap',
            }}
          >
            {batchMode ? '取消' : '选择'}
          </button>
        )}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setShowSortPicker(v => !v)}
            style={{
              padding: '4px 10px',
              background: 'none',
              border: '1px solid var(--border-default)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--text-tertiary)',
              fontSize: 'var(--text-caption-1)', fontWeight: 500,
              cursor: 'pointer', whiteSpace: 'nowrap',
              display: 'flex', alignItems: 'center', gap: 4,
            }}
          >
            {SORT_OPTIONS.find(o => o.value === sortMode)?.label || '排序'}
            <IconChevronDown size={12} />
          </button>
          {showSortPicker && (
            <>
              <div
                onClick={() => setShowSortPicker(false)}
                style={{ position: 'fixed', inset: 0, zIndex: 9 }}
              />
              <div style={{
                position: 'absolute', top: '100%', right: 0, zIndex: 10,
                marginTop: 4,
                background: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-md)',
                boxShadow: '0 4px 16px rgba(0,0,0,0.15)',
                overflow: 'hidden', minWidth: 130,
              }}>
                {SORT_OPTIONS.map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => { setSortMode(opt.value); setShowSortPicker(false) }}
                    style={{
                      display: 'block', width: '100%',
                      padding: '10px 14px', border: 'none',
                      background: sortMode === opt.value ? 'var(--accent-surface)' : 'none',
                      color: sortMode === opt.value ? 'var(--accent)' : 'var(--text-primary)',
                      fontSize: 'var(--text-footnote)', fontWeight: sortMode === opt.value ? 600 : 400,
                      cursor: 'pointer', textAlign: 'left',
                    }}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      <div
        ref={pullRef}
        onTouchStart={onPullStart}
        onTouchMove={onPullMove}
        onTouchEnd={onPullEnd}
        style={{
          flex: 1,
          overflowY: 'auto',
          WebkitOverflowScrolling: 'touch',
          transform: pullY > 0 ? `translateY(${pullY}px)` : undefined,
          transition: pullY > 0 ? 'none' : 'transform 0.2s ease',
        }}
      >
        {rows.length === 0 && (
          <EmptyState
            icon={<IconTodo size={48} />}
            title={statusFilter === 'overdue' ? '没有逾期待办' : statusFilter !== 'all' ? '没有匹配的待办' : '暂无待办'}
            description={statusFilter === 'all' ? '点击右下角的按钮添加你的第一个待办事项' : '尝试切换筛选条件'}
          />
        )}

        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualizer.getVirtualItems().map(v => {
            const row = rows[v.index]
            if (!row) return null
            if (row.type === 'header') {
              return (
                <div
                  key={`h-${v.index}`}
                  style={{
                    position: 'absolute',
                    top: 0, left: 0, width: '100%',
                    height: v.size,
                    transform: `translateY(${v.start}px)`,
                    padding: '20px 20px 8px',
                    fontSize: 'var(--text-caption-1)',
                    color: 'var(--text-tertiary)',
                    fontWeight: 500,
                  }}
                >
                  已完成 ({row.count})
                </div>
              )
            }
            return (
              <div
                key={row.todo.id}
                onClick={batchMode ? () => {
                  const next = new Set(selected)
                  if (next.has(row.todo.id)) next.delete(row.todo.id)
                  else next.add(row.todo.id)
                  setSelected(next)
                } : undefined}
                style={{
                  position: 'absolute',
                  top: 0, left: 0, width: '100%',
                  height: v.size,
                  transform: `translateY(${v.start}px)`,
                  cursor: batchMode ? 'pointer' : undefined,
                }}
              >
                {batchMode && (
                  <div style={{
                    position: 'absolute', left: 12, top: 0, bottom: 0,
                    display: 'flex', alignItems: 'center', zIndex: 2,
                  }}>
                    <div style={{
                      width: 22, height: 22, borderRadius: '50%',
                      border: `2px solid ${selected.has(row.todo.id) ? 'var(--accent)' : 'var(--border-default)'}`,
                      background: selected.has(row.todo.id) ? 'var(--accent)' : 'transparent',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      transition: 'all 0.15s ease',
                    }}>
                      {selected.has(row.todo.id) && (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M20 6L9 17l-5-5" />
                        </svg>
                      )}
                    </div>
                  </div>
                )}
                <div style={{ paddingLeft: batchMode ? 44 : 0 }}>
                  <TodoItem
                    item={row.todo}
                    onClick={batchMode ? () => {} : setActionItem}
                  />
                </div>
              </div>
            )
          })}
        </div>

        <div style={{ height: 100 }} />
      </div>

      {!batchMode && doneTodos.length > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '10px 16px',
          paddingBottom: 'calc(10px + env(safe-area-inset-bottom, 0px))',
          background: 'var(--bg-secondary)',
          borderTop: '0.5px solid var(--border-subtle)',
          flexShrink: 0,
        }}>
          <span style={{
            fontSize: 'var(--text-footnote)',
            color: 'var(--text-tertiary)',
            flex: 1,
          }}>
            已完成 {doneTodos.length} 项
          </span>
          <button
            onClick={() => {
              if (window.confirm(`确定清除所有已完成项（${doneTodos.length} 项）？`)) {
                clearDoneTodos()
              }
            }}
            style={{
              padding: '8px 16px', borderRadius: 'var(--radius-sm)',
              border: 'none', background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
              fontSize: 'var(--text-footnote)', fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            清除已完成
          </button>
        </div>
      )}

      {batchMode && selected.size > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '10px 16px',
          paddingBottom: 'calc(10px + env(safe-area-inset-bottom, 0px))',
          background: 'var(--bg-secondary)',
          borderTop: '0.5px solid var(--border-subtle)',
          flexShrink: 0,
        }}>
          <span style={{
            fontSize: 'var(--text-footnote)',
            color: 'var(--text-tertiary)',
            flex: 1,
          }}>
            已选 {selected.size} 项
          </span>
          <button
            onClick={() => {
              selected.forEach(id => toggleTodo(id))
              setSelected(new Set())
              setBatchMode(false)
            }}
            style={{
              padding: '8px 16px', borderRadius: 'var(--radius-sm)',
              border: 'none', background: 'var(--accent)', color: '#fff',
              fontSize: 'var(--text-footnote)', fontWeight: 600,
              cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg> 完成
          </button>
          <button
            onClick={() => {
              if (window.confirm(`确定删除选中的 ${selected.size} 项？`)) {
                selected.forEach(id => deleteTodo(id))
                setSelected(new Set())
                setBatchMode(false)
              }
            }}
            style={{
              padding: '8px 16px', borderRadius: 'var(--radius-sm)',
              border: 'none', background: 'var(--danger)', color: '#fff',
              fontSize: 'var(--text-footnote)', fontWeight: 600,
              cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg> 删除
          </button>
        </div>
      )}

      {activeReminder && (
        <ReminderBar
          todoId={activeReminder.todo.id}
          todoText={activeReminder.todo.text}
          reminderTime={activeReminder.todo.reminderTime || 0}
          onSnooze={snooze}
          onDismiss={dismiss}
        />
      )}

      <button
        onClick={() => { haptic.medium(); setShowNew(true) }}
        aria-label="新建待办"
        onTouchStart={touchScale.onStart}
        onTouchEnd={touchScale.onEnd}
        onMouseDown={touchScale.onStart}
        onMouseUp={touchScale.onEnd}
        style={{
          position: 'fixed',
          bottom: 'calc(96px + env(safe-area-inset-bottom, 0px))',
          right: 20,
          width: 56,
          height: 56,
          borderRadius: '50%',
          background: 'var(--accent)',
          color: '#FFFFFF',
          border: 'none',
          cursor: 'pointer',
          boxShadow: 'var(--shadow-fab)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 10,
          WebkitTapHighlightColor: 'transparent',
        }}
      >
        <IconPlus size={24} />
      </button>

      <NewTodoSheet
        open={showNew}
        onClose={() => setShowNew(false)}
        category={activeCat !== 'all' ? activeCat : undefined}
      />
      <TodoActionSheet
        item={actionItem}
        open={!!actionItem}
        onClose={() => setActionItem(null)}
      />
    </div>
  )
}
