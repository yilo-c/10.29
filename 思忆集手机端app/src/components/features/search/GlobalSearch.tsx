import { useState, useMemo, useRef, useEffect } from 'react'
import { useStore } from '@desk-notes/shared'
import { IconSearch, IconX, IconTodo, IconNote } from '../../ui'
import Fuse from 'fuse.js'
import type { TodoItem as TodoItemType, FloatingNote } from '@desk-notes/shared'

interface GlobalSearchProps {
  open: boolean
  onClose: () => void
  onSelectTodo?: (todo: TodoItemType) => void
  onSelectNote?: (note: FloatingNote) => void
}

interface SearchItem {
  type: 'todo' | 'note'
  id: string
  title: string
  subtitle: string
  done?: boolean
  todo?: TodoItemType
  note?: FloatingNote
}

export default function GlobalSearch({ open, onClose, onSelectTodo, onSelectNote }: GlobalSearchProps) {
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const todos = useStore(s => s.todos)
  const notes = useStore(s => s.floatingNotes)

  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 100)
      setQuery('')
    }
  }, [open])

  const results = useMemo(() => {
    if (!query.trim()) return { todos: [] as TodoItemType[], notes: [] as FloatingNote[] }

    const index = new Fuse<SearchItem>([], {
      keys: ['title', 'subtitle'],
      threshold: 0.35,
      minMatchCharLength: 1,
    })

    const todoItems: SearchItem[] = todos.map(t => ({
      type: 'todo', id: t.id,
      title: t.text,
      subtitle: t.done ? '已完成' : t.category || '',
      done: t.done,
      todo: t,
    }))
    const noteItems: SearchItem[] = notes.map(n => ({
      type: 'note', id: n.id,
      title: n.title || '无标题',
      subtitle: (n.content || '').replace(/[#*`_>-]+/g, '').trim().slice(0, 80),
      note: n,
    }))

    index.setCollection([...todoItems, ...noteItems])
    const matched = index.search(query)

    const matchedTodos: TodoItemType[] = []
    const matchedNotes: FloatingNote[] = []
    for (const r of matched) {
      if (r.item.type === 'todo' && r.item.todo && matchedTodos.length < 10) {
        matchedTodos.push(r.item.todo)
      } else if (r.item.type === 'note' && r.item.note && matchedNotes.length < 8) {
        matchedNotes.push(r.item.note)
      }
    }

    return { todos: matchedTodos, notes: matchedNotes }
  }, [query, todos, notes])

  const hasResults = results.todos.length > 0 || results.notes.length > 0

  if (!open) return null

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 150,
      background: 'var(--bg-primary)',
      display: 'flex', flexDirection: 'column',
      animation: 'fadeSlideIn 0.15s var(--ease-out)',
    }}>
      {/* Search bar */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        padding: '12px 16px',
        paddingTop: 'calc(12px + env(safe-area-inset-top, 0px))',
        borderBottom: '0.5px solid var(--border-subtle)',
        flexShrink: 0,
      }}>
        <span style={{ color: 'var(--text-disabled)', display: 'flex' }}>
          <IconSearch size={18} />
        </span>
        <input
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="搜索待办、笔记..."
          style={{
            flex: 1,
            border: 'none',
            background: 'transparent',
            color: 'var(--text-primary)',
            fontSize: 'var(--text-body)',
            outline: 'none',
            padding: '8px 0',
          }}
        />
        {query && (
          <button
            onClick={() => setQuery('')}
            aria-label="清除搜索"
            style={{
              background: 'none', border: 'none',
              color: 'var(--text-disabled)', cursor: 'pointer',
              display: 'flex', padding: 4,
            }}
          >
            <IconX size={16} />
          </button>
        )}
        <button
          onClick={onClose}
          style={{
            background: 'none', border: 'none',
            color: 'var(--accent)', cursor: 'pointer',
            fontSize: 'var(--text-footnote)', fontWeight: 500,
            padding: '4px 8px',
          }}
        >
          取消
        </button>
      </div>

      {/* Results */}
      <div style={{
        flex: 1, overflowY: 'auto',
        padding: '8px 0',
      }}>
        {!query.trim() && (
          <div style={{
            padding: '40px 20px',
            textAlign: 'center',
            color: 'var(--text-disabled)',
            fontSize: 'var(--text-footnote)',
          }}>
            输入关键词搜索待办和笔记
          </div>
        )}

        {query.trim() && !hasResults && (
          <div style={{
            padding: '40px 20px',
            textAlign: 'center',
            color: 'var(--text-tertiary)',
            fontSize: 'var(--text-footnote)',
          }}>
            未找到与「{query}」相关的内容
          </div>
        )}

        {results.todos.length > 0 && (
          <>
            <GroupHeader title="待办" count={results.todos.length} />
            {results.todos.map(todo => (
              <SearchResultRow
                key={todo.id}
                icon={<IconTodo size={16} />}
                title={todo.text}
                subtitle={todo.done ? '已完成' : todo.category || ''}
                done={todo.done}
                onClick={() => {
                  onSelectTodo?.(todo)
                  onClose()
                }}
              />
            ))}
          </>
        )}

        {results.notes.length > 0 && (
          <>
            <GroupHeader title="笔记" count={results.notes.length} />
            {results.notes.map(note => (
              <SearchResultRow
                key={note.id}
                icon={<IconNote size={16} />}
                title={note.title || '无标题'}
                subtitle={(note.content || '').replace(/[#*`_>-]+/g, '').trim().slice(0, 60)}
                onClick={() => {
                  onSelectNote?.(note)
                  onClose()
                }}
              />
            ))}
          </>
        )}

        <div style={{ height: 40 }} />
      </div>
    </div>
  )
}

function GroupHeader({ title, count }: { title: string; count: number }) {
  return (
    <div style={{
      padding: '8px 20px 6px',
      fontSize: 'var(--text-caption-1)',
      fontWeight: 600,
      color: 'var(--text-tertiary)',
      display: 'flex', alignItems: 'center', gap: 6,
    }}>
      {title}
      <span style={{ fontWeight: 400, color: 'var(--text-disabled)' }}>
        {count}
      </span>
    </div>
  )
}

function SearchResultRow({
  icon, title, subtitle, done, onClick,
}: {
  icon: React.ReactNode
  title: string
  subtitle: string
  done?: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 12,
        width: '100%', padding: '10px 20px',
        border: 'none', background: 'none',
        cursor: 'pointer', textAlign: 'left',
        opacity: done ? 0.5 : 1,
        transition: 'background 0.1s ease',
        WebkitTapHighlightColor: 'transparent',
      }}
    >
      <span style={{
        color: done ? 'var(--tone-smooth)' : 'var(--text-disabled)',
        flexShrink: 0,
      }}>
        {icon}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 'var(--text-footnote)',
          fontWeight: 500,
          color: 'var(--text-primary)',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
          whiteSpace: 'nowrap',
          textDecoration: done ? 'line-through' : 'none',
        }}>
          {title}
        </div>
        {subtitle && (
          <div style={{
            fontSize: 'var(--text-caption-2)',
            color: 'var(--text-disabled)',
            marginTop: 2,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {subtitle}
          </div>
        )}
      </div>
    </button>
  )
}
