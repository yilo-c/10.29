import { useState, useMemo, useRef } from 'react'
import { useStore, uid, type FloatingNote } from '@desk-notes/shared'
import { Sheet, Chip } from '../../ui'

export default function NotesView() {
  const notes = useStore(s => s.floatingNotes)
  const folders = useStore(s => s.folders)
  const activeFolderId = useStore(s => s.activeFolderId)
  const setActiveFolder = useStore(s => s.setActiveFolder)
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const updateFloatingNote = useStore(s => s.updateFloatingNote)
  const removeFloatingNote = useStore(s => s.removeFloatingNote)

  const [editNoteId, setEditNoteId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editContent, setEditContent] = useState('')
  const [showNew, setShowNew] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newContent, setNewContent] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [pendingDelete, setPendingDelete] = useState<FloatingNote | null>(null)
  const deleteTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const filtered = useMemo(() => {
    // 待撤销删除的笔记先从 UI 移除，超时后才真正删除
    let list = pendingDelete ? notes.filter(n => n.id !== pendingDelete.id) : [...notes]

    // Filter by folder
    if (activeFolderId && activeFolderId !== 'all') {
      list = list.filter(n => (n.folderId ?? 'default') === activeFolderId)
    }

    // Search
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      list = list.filter(n =>
        (n.title || '').toLowerCase().includes(q) ||
        (n.content || '').toLowerCase().includes(q)
      )
    }

    return list.sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0))
  }, [notes, activeFolderId, searchQuery, pendingDelete])

  const handleDelete = (note: FloatingNote) => {
    if (deleteTimer.current) {
      clearTimeout(deleteTimer.current)
      // 连续删除时，先提交上一条已到期/未撤销的删除
      if (pendingDelete) removeFloatingNote(pendingDelete.id)
    }
    setPendingDelete(note)
    deleteTimer.current = setTimeout(() => {
      removeFloatingNote(note.id)
      setPendingDelete(null)
      deleteTimer.current = null
    }, 3000)
  }

  const handleUndoDelete = () => {
    if (deleteTimer.current) {
      clearTimeout(deleteTimer.current)
      deleteTimer.current = null
    }
    // 超时前真正删除尚未执行，笔记仍在 store 中，放回 UI 即可
    setPendingDelete(null)
  }

  const handleNew = () => {
    if (!newTitle.trim() && !newContent.trim()) {
      setShowNew(false)
      return
    }
    const id = uid()
    addFloatingNote({
      id,
      title: newTitle || '无标题',
      content: newContent,
      type: 'text',
      x: 0, y: 0, zIndex: 1,
      width: 320, height: 200,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      tags: [],
      folderId: activeFolderId && activeFolderId !== 'all' ? activeFolderId : undefined,
    })
    setNewTitle('')
    setNewContent('')
    setShowNew(false)
  }

  const handleSave = () => {
    if (editNoteId) {
      updateFloatingNote(editNoteId, {
        title: editTitle || '无标题',
        content: editContent,
        updatedAt: Date.now(),
      })
      setEditNoteId(null)
      setEditTitle('')
      setEditContent('')
    }
  }

  const handleStartEdit = (note: FloatingNote) => {
    setEditNoteId(note.id)
    setEditTitle(note.title || '')
    setEditContent(note.content || '')
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{ padding: 'var(--space-4) var(--space-4) 0', flexShrink: 0 }}>
        <div style={{ fontSize: 'var(--text-h1)', fontWeight: 600 }}>笔记</div>
      </div>

      {/* Search */}
      <div style={{ padding: 'var(--space-2) var(--space-4) 0', flexShrink: 0 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border-card)',
          borderRadius: 'var(--radius-sm)',
          padding: '0 12px',
          minHeight: 38,
        }}>
          <span style={{ color: 'var(--text-disabled)', fontSize: 15 }}>🔍</span>
          <input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder="搜索笔记..."
            style={{
              flex: 1, border: 'none', background: 'transparent',
              color: 'var(--text-primary)', fontSize: 'var(--text-small)',
              outline: 'none', padding: '8px 0',
            }}
          />
          {searchQuery && (
            <button
              onClick={() => setSearchQuery('')}
              style={{
                background: 'none', border: 'none',
                color: 'var(--text-disabled)', cursor: 'pointer',
                fontSize: 14, padding: 4,
              }}
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {/* Folder chips */}
      <div style={{
        display: 'flex', gap: 6, padding: 'var(--space-2) var(--space-4)',
        overflowX: 'auto', flexShrink: 0,
        scrollbarWidth: 'none',
      }}>
        <Chip
          type="filter"
          selected={activeFolderId === null || activeFolderId === 'all'}
          label="全部"
          count={notes.length}
          onClick={() => setActiveFolder(null)}
        />
        {folders.map(f => (
          <Chip
            key={f.id}
            type="filter"
            selected={activeFolderId === f.id}
            label={f.name}
            count={notes.filter(n => (n.folderId ?? 'default') === f.id).length}
            onClick={() => setActiveFolder(f.id)}
          />
        ))}
      </div>

      {/* Note list */}
      <div style={{
        flex: 1, overflowY: 'auto', padding: '0 var(--space-4)',
        WebkitOverflowScrolling: 'touch',
      }}>
        {filtered.length === 0 && (
          <div style={{
            textAlign: 'center', color: 'var(--text-tertiary)',
            marginTop: 60, fontSize: 'var(--text-small)',
          }}>
            {searchQuery ? '没有找到相关内容' : (
              <>
                <div>还没有笔记</div>
                <button onClick={() => setShowNew(true)}
                  style={{
                    marginTop: 16, padding: '10px 20px',
                    borderRadius: 'var(--radius-sm)', border: 'none',
                    background: 'var(--gold-primary)', color: '#0a0a12',
                    fontSize: 'var(--text-body)', fontWeight: 600, cursor: 'pointer',
                  }}>
                  ✎ 写第一条笔记
                </button>
              </>
            )}
          </div>
        )}

        {filtered.map(note => (
          <div key={note.id} style={{
            background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)',
            marginBottom: 'var(--space-3)', border: '1px solid var(--border-card)',
            overflow: 'hidden',
          }}>
            {editNoteId === note.id ? (
              /* Edit mode */
              <div style={{ padding: 'var(--space-4)' }}>
                <input
                  value={editTitle}
                  onChange={e => setEditTitle(e.target.value)}
                  placeholder="标题"
                  style={{
                    width: '100%', padding: '8px 0', border: 'none',
                    borderBottom: '1px solid var(--border-subtle)',
                    background: 'transparent', color: 'var(--text-primary)',
                    fontSize: 'var(--text-body)', fontWeight: 600, outline: 'none',
                    marginBottom: 12,
                  }}
                />
                <textarea
                  value={editContent}
                  onChange={e => setEditContent(e.target.value)}
                  placeholder="内容"
                  rows={6}
                  style={{
                    width: '100%', padding: 0, border: 'none',
                    background: 'transparent', color: 'var(--text-secondary)',
                    fontSize: 'var(--text-body)', lineHeight: 1.6,
                    resize: 'none', outline: 'none', fontFamily: 'inherit',
                  }}
                />
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 12 }}>
                  <button onClick={() => setEditNoteId(null)}
                    style={{ padding: '6px 14px', borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-card)', background: 'transparent',
                      color: 'var(--text-secondary)', fontSize: 'var(--text-small)', cursor: 'pointer' }}>
                    取消
                  </button>
                  <button onClick={handleSave}
                    style={{ padding: '6px 14px', borderRadius: 'var(--radius-sm)',
                      border: 'none', background: 'var(--gold-primary)', color: '#0a0a12',
                      fontSize: 'var(--text-small)', fontWeight: 600, cursor: 'pointer' }}>
                    保存
                  </button>
                </div>
              </div>
            ) : (
              /* View mode */
              <div onClick={() => handleStartEdit(note)} style={{ padding: 'var(--space-4)', cursor: 'pointer' }}>
                <div style={{ fontSize: 'var(--text-body)', fontWeight: 600, marginBottom: 4 }}>
                  {note.title || '无标题'}
                </div>
                <div style={{
                  fontSize: 'var(--text-small)', color: 'var(--text-tertiary)',
                  lineHeight: 1.5, overflow: 'hidden', textOverflow: 'ellipsis',
                  display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                }}>
                  {(note.content || '').split('\n').slice(0, 2).join(' ')}
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }}>
                  <span style={{ fontSize: 'var(--text-tiny)', color: 'var(--text-disabled)' }}>
                    {note.updatedAt
                      ? new Date(note.updatedAt).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
                      : note.createdAt
                        ? new Date(note.createdAt).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
                        : ''}
                  </span>
                  <button onClick={(e) => { e.stopPropagation(); handleDelete(note) }}
                    aria-label="删除笔记"
                    style={{ background: 'none', border: 'none', color: 'var(--text-disabled)',
                      fontSize: 14, cursor: 'pointer',
                      width: 44, height: 44, minWidth: 44,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      margin: '-8px -8px -8px 0' }}>
                    ✕
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}

        <div style={{ height: 80 }} />
      </div>

      {/* 删除撤销条 */}
      {pendingDelete && (
        <div style={{
          position: 'fixed', bottom: 'calc(140px + var(--safe-bottom))',
          left: '50%', transform: 'translateX(-50%)',
          background: 'var(--bg-secondary)', border: '1px solid var(--border-card)',
          borderRadius: 'var(--radius-md)', padding: '10px 16px',
          display: 'flex', alignItems: 'center', gap: 12, zIndex: 100,
          fontSize: 'var(--text-small)', color: 'var(--text-secondary)',
          boxShadow: 'var(--elevation-3)', maxWidth: '80vw',
        }}>
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            已删除「{pendingDelete.title || '无标题'}」
          </span>
          <button onClick={handleUndoDelete}
            style={{ background: 'none', border: 'none', color: 'var(--gold-primary)',
              fontWeight: 600, cursor: 'pointer', fontSize: 'var(--text-small)', padding: '4px 8px' }}>
            撤销
          </button>
        </div>
      )}

      {/* FAB */}
      <button onClick={() => setShowNew(true)} aria-label="新建笔记"
        style={{
          position: 'fixed', bottom: 'calc(72px + var(--safe-bottom))', right: 20,
          width: 52, height: 52, borderRadius: '50%',
          background: 'var(--gold-primary)', color: '#0a0a12',
          border: 'none', fontSize: 26, cursor: 'pointer',
          boxShadow: '0 4px 16px rgba(201, 167, 92, 0.3)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10,
        }}
      >
        +
      </button>

      {/* New note sheet */}
      <Sheet open={showNew} onClose={() => setShowNew(false)} title="新建笔记">
        <input
          value={newTitle} onChange={e => setNewTitle(e.target.value)}
          placeholder="标题" autoFocus
          style={{
            width: '100%', padding: '10px 14px', borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border-card)', background: 'var(--bg-tertiary)',
            color: 'var(--text-primary)', fontSize: 'var(--text-body)', outline: 'none',
            marginBottom: 12,
          }}
        />
        <textarea
          value={newContent} onChange={e => setNewContent(e.target.value)}
          placeholder="内容" rows={8}
          style={{
            width: '100%', padding: '10px 14px', borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border-card)', background: 'var(--bg-tertiary)',
            color: 'var(--text-primary)', fontSize: 'var(--text-body)',
            lineHeight: 1.6, resize: 'none', outline: 'none', fontFamily: 'inherit',
            marginBottom: 16,
          }}
        />
        <div style={{ display: 'flex', gap: 12 }}>
          <button onClick={() => setShowNew(false)}
            style={{ flex: 1, padding: '10px', borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-card)', background: 'transparent',
              color: 'var(--text-secondary)', fontSize: 'var(--text-body)', cursor: 'pointer' }}>
            取消
          </button>
          <button onClick={handleNew}
            style={{ flex: 1, padding: '10px', borderRadius: 'var(--radius-sm)',
              border: 'none', background: 'var(--gold-primary)', color: '#0a0a12',
              fontSize: 'var(--text-body)', fontWeight: 600, cursor: 'pointer' }}>
            保存
          </button>
        </div>
      </Sheet>
    </div>
  )
}
