import { useState, useRef, useCallback } from 'react'
import { useStore } from '@desk-notes/shared'
import { Badge, IconMore, IconCheck, showToast } from '../../ui'
import type { TodoItem as TodoItemType } from '@desk-notes/shared'
import { haptic } from '../../../lib/haptic'

interface TodoItemProps {
  item: TodoItemType
  onClick: (item: TodoItemType) => void
}

const priorityMap: Record<number, 'high' | 'mid' | 'low'> = { 0: 'high', 1: 'mid', 2: 'low' }

export default function TodoItem({ item, onClick }: TodoItemProps) {
  const toggleTodo = useStore(s => s.toggleTodo)
  const deleteTodo = useStore(s => s.deleteTodo)
  const addTodo = useStore(s => s.addTodo)
  const todos = useStore(s => s.todos)
  const [offsetX, setOffsetX] = useState(0)
  const startX = useRef(0)
  const isSwiping = useRef(false)
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    startX.current = e.touches[0].clientX
    isSwiping.current = false
    setOffsetX(0)
    longPressTimer.current = setTimeout(() => {
      isSwiping.current = true
      haptic.medium()
      onClick(item)
    }, 500)
  }, [item, onClick])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    const dx = e.touches[0].clientX - startX.current
    if (Math.abs(dx) > 10) {
      isSwiping.current = true
      if (longPressTimer.current) {
        clearTimeout(longPressTimer.current)
        longPressTimer.current = null
      }
    }
    setOffsetX(Math.max(-80, Math.min(80, dx)))
  }, [])

  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current)
      longPressTimer.current = null
    }
    const dx = e.changedTouches[0].clientX - startX.current
    if (Math.abs(dx) > 40) {
      isSwiping.current = true
      if (dx > 0) {
        haptic.medium()
        toggleTodo(item.id)
      } else {
        haptic.heavy()
        showToast({
          message: '已删除',
          action: { label: '撤销', onClick: () => addTodo(item.text, item.category) },
          duration: 3500,
        })
        deleteTodo(item.id)
      }
    } else if (!isSwiping.current) {
      onClick(item)
    }
    setOffsetX(0)
  }, [item, toggleTodo, deleteTodo, onClick, addTodo])

  const priority = item.priority !== undefined ? priorityMap[item.priority] : undefined
  const isOverdue = !item.done && item.dueDate && item.dueDate < Date.now()
  const subtasks = todos.filter(t => t.parentId === item.id)
  const subtaskDone = subtasks.filter(t => t.done).length

  return (
    <div
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 12,
          padding: '12px 20px',
          transform: `translateX(${offsetX}px)`,
          transition: isSwiping.current ? 'none' : 'transform 0.15s ease',
          background: offsetX > 0
            ? 'rgba(52, 199, 89, 0.06)'
            : offsetX < 0
              ? 'rgba(255, 59, 48, 0.06)'
              : 'transparent',
          userSelect: 'none',
          minHeight: 52,
        }}
      >
        <button
          onClick={(e) => { e.stopPropagation(); haptic.light(); toggleTodo(item.id) }}
          onTouchStart={e => e.stopPropagation()}
          onTouchEnd={e => e.stopPropagation()}
          aria-label={item.done ? '标记未完成' : '标记完成'}
          style={{
            width: 24,
            height: 24,
            borderRadius: '50%',
            border: `2px solid ${item.done ? 'var(--tone-smooth)' : 'var(--border-default)'}`,
            background: item.done ? 'var(--tone-smooth)' : 'transparent',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            flexShrink: 0,
            padding: 0,
            marginTop: 2,
            transition: 'all 0.15s ease',
            WebkitTapHighlightColor: 'transparent',
          }}
        >
          {item.done && <IconCheck size={14} />}
        </button>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 'var(--text-body)',
            lineHeight: 1.45,
            textDecoration: item.done ? 'line-through' : 'none',
            color: item.done ? 'var(--text-tertiary)' : 'var(--text-primary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.text}</span>
            {subtasks.length > 0 && (
              <span style={{
                fontSize: 'var(--text-caption-2)',
                color: subtaskDone === subtasks.length ? 'var(--tone-smooth)' : 'var(--text-disabled)',
                fontWeight: 500,
                flexShrink: 0,
              }}>
                {subtaskDone}/{subtasks.length}
              </span>
            )}
          </div>
          {(priority || item.dueDate || item.qimenEvent) && (
            <div style={{
              display: 'flex',
              gap: 8,
              marginTop: 3,
              alignItems: 'center',
              flexWrap: 'wrap',
            }}>
              {priority && <Badge priority={priority} dotOnly />}
              {item.dueDate && (
                <span style={{
                  fontSize: 'var(--text-caption-2)',
                  color: isOverdue ? 'var(--danger)' : 'var(--text-tertiary)',
                  fontWeight: isOverdue ? 500 : 400,
                }}>
                  {new Date(item.dueDate).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}
                </span>
              )}
              {item.qimenEvent && (
                <span style={{
                  fontSize: 'var(--text-caption-2)',
                  color: 'var(--text-tertiary)',
                  background: 'var(--bg-tertiary)',
                  padding: '1px 6px',
                  borderRadius: 'var(--radius-sm)',
                }}>
                  参考
                </span>
              )}
            </div>
          )}
        </div>

        <button
          onClick={(e) => { e.stopPropagation(); onClick(item) }}
          onTouchStart={e => e.stopPropagation()}
          onTouchEnd={e => e.stopPropagation()}
          aria-label="更多操作"
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            flexShrink: 0,
            width: 32,
            height: 44,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            WebkitTapHighlightColor: 'transparent',
          }}
        >
          <IconMore size={18} />
        </button>
    </div>
  )
}
