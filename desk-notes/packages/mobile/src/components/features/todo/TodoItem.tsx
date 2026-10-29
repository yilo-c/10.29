import { useState, useRef, useCallback } from 'react'
import { useStore, type TodoItem as TodoItemType } from '@desk-notes/shared'
import { Badge } from '../../ui'

interface TodoItemProps {
  item: TodoItemType
  onLongPress: (item: TodoItemType) => void
  onClick: (item: TodoItemType) => void
}

const priorityMap: Record<number, 'high' | 'mid' | 'low'> = {
  0: 'high',
  1: 'mid',
  2: 'low',
}

const priorityLabelMap: Record<number, string> = {
  0: '高',
  1: '中',
  2: '低',
}

export default function TodoItem({ item, onLongPress, onClick }: TodoItemProps) {
  const toggleTodo = useStore(s => s.toggleTodo)
  const deleteTodo = useStore(s => s.deleteTodo)
  const addTodo = useStore(s => s.addTodo)
  const [offsetX, setOffsetX] = useState(0)
  const startX = useRef(0)
  const isSwiping = useRef(false)
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [showUndo, setShowUndo] = useState(false)
  const [deletedText, setDeletedText] = useState('')
  const [deletedCategory, setDeletedCategory] = useState<string | undefined>()

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    startX.current = e.touches[0].clientX
    isSwiping.current = false
    setOffsetX(0)

    longPressTimer.current = setTimeout(() => {
      isSwiping.current = true
      onLongPress(item)
    }, 500)
  }, [item, onLongPress])

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

  const handleTouchEnd = useCallback(() => {
    if (longPressTimer.current) {
      clearTimeout(longPressTimer.current)
      longPressTimer.current = null
    }

    if (Math.abs(offsetX) > 40) {
      isSwiping.current = true
      if (offsetX > 0) {
        toggleTodo(item.id)
      } else {
        setDeletedText(item.text)
        setDeletedCategory(item.category)
        deleteTodo(item.id)
        setShowUndo(true)
        setTimeout(() => setShowUndo(false), 3000)
      }
    } else if (!isSwiping.current) {
      onClick(item)
    }
    setOffsetX(0)
  }, [offsetX, item, toggleTodo, deleteTodo, onClick])

  const priority = item.priority !== undefined ? priorityMap[item.priority] : undefined

  return (
    <>
      <div
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-3)',
          padding: '12px var(--space-4)',
          borderBottom: '1px solid var(--border-subtle)',
          transform: `translateX(${offsetX}px)`,
          transition: isSwiping.current ? 'none' : 'transform var(--duration-fast) var(--ease-out)',
          background: offsetX > 0
            ? 'rgba(111, 179, 137, 0.08)'
            : offsetX < 0
              ? 'rgba(229, 115, 115, 0.08)'
              : 'transparent',
          cursor: 'default',
          userSelect: 'none',
          minHeight: 72,
        }}
      >
        {/* Checkbox */}
        <button
          onClick={(e) => { e.stopPropagation(); toggleTodo(item.id) }}
          style={{
            width: 22,
            height: 22,
            borderRadius: '50%',
            border: `2px solid ${item.done ? 'var(--tone-smooth)' : 'var(--text-disabled)'}`,
            background: item.done ? 'var(--tone-smooth)' : 'transparent',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            flexShrink: 0,
            padding: 0,
            transition: 'all var(--duration-fast) var(--ease-out)',
            marginTop: 2,
          }}
        >
          {item.done && (
            <svg viewBox="0 0 24 24" width="14" height="14" fill="#0a0a12">
              <path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z" />
            </svg>
          )}
        </button>

        {/* Text + meta */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 'var(--text-body)',
              lineHeight: 1.4,
              textDecoration: item.done ? 'line-through' : 'none',
              color: item.done ? 'var(--text-tertiary)' : 'var(--text-primary)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              fontWeight: 500,
            }}
          >
            {item.text}
          </div>
          <div style={{ display: 'flex', gap: 6, marginTop: 4, alignItems: 'center', flexWrap: 'wrap' }}>
            {priority && (
              <Badge priority={priority} label={priorityLabelMap[item.priority!]} />
            )}
            {item.dueDate && (
              <span style={{ fontSize: 'var(--text-tiny)', color: 'var(--text-tertiary)', fontWeight: 500 }}>
                {new Date(item.dueDate).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })}
              </span>
            )}
            {item.qimenEvent && (
              <span style={{ fontSize: 'var(--text-tiny)', color: 'var(--gold-muted)' }}>
                参考
              </span>
            )}
          </div>
        </div>

        {/* More button */}
        <button
          onClick={(e) => { e.stopPropagation(); onLongPress(item) }}
          aria-label="更多操作"
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-tertiary)',
            fontSize: 16,
            cursor: 'pointer',
            flexShrink: 0,
            width: 32,
            height: 44,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          ⋯
        </button>
      </div>

      {/* Undo toast */}
      {showUndo && (
        <div
          style={{
            position: 'fixed',
            bottom: 'calc(100px + var(--safe-bottom))',
            left: '50%',
            transform: 'translateX(-50%)',
            background: 'var(--bg-secondary)',
            border: '1px solid var(--border-card)',
            borderRadius: 'var(--radius-md)',
            padding: '10px 16px',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            zIndex: 100,
            fontSize: 'var(--text-small)',
            color: 'var(--text-secondary)',
            animation: 'fadeIn var(--duration-fast) var(--ease-out)',
            boxShadow: 'var(--elevation-3)',
          }}
        >
          <span>已删除</span>
          <button
            onClick={() => {
              addTodo(deletedText, deletedCategory)
              setShowUndo(false)
            }}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--gold-primary)',
              fontWeight: 600,
              cursor: 'pointer',
              fontSize: 'var(--text-small)',
              padding: '4px 8px',
            }}
          >
            撤销
          </button>
        </div>
      )}

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateX(-50%) translateY(10px); }
          to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
      `}</style>
    </>
  )
}
