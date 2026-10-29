import { useState, useMemo, useRef, useCallback, useEffect } from 'react'
import { useStore, uid } from '@desk-notes/shared'
import { haptic, touchScale } from '../../../lib/haptic'
import { Chip, IconSearch, IconPlus, IconNote, IconFolder, showToast } from '../../ui'
import type { FloatingNote, Folder } from '@desk-notes/shared'
import { tokenManager } from '../../../lib/tokenManager'
import { relayQuickCapture, pushReadingPosition } from '../../../lib/readingClient'
import EmptyState from '../todo/EmptyState'
import FolderManageSheet from './FolderManageSheet'
import KnowledgeView from '../knowledge/KnowledgeView'
import NoteCard from './NoteCard'
import NotesSearchBar from './NotesSearchBar'
import NoteActionSheets from './NoteActionSheets'
import NoteEditorSheet from './NoteEditorSheet'
import InboxSheet from '../inbox/InboxSheet'
import GlobalSearch from '../search/GlobalSearch'
import { unprocessedCount } from '../../../lib/inboxManager'
import { swallowError } from '../../../lib/swallowError'

function collectDescendantFolderIds(parentId: string | null, allFolders: Folder[]): Set<string> {
  const ids = new Set<string>()
  const children = allFolders.filter(f => (f.parentId ?? null) === parentId)
  for (const child of children) {
    ids.add(child.id)
    for (const subId of collectDescendantFolderIds(child.id, allFolders)) ids.add(subId)
  }
  return ids
}

export default function NotesView() {
  const notes = useStore(s => s.floatingNotes)
  const folders = useStore(s => s.folders)
  const activeFolderId = useStore(s => s.activeFolderId)
  const setActiveFolder = useStore(s => s.setActiveFolder)
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const updateFloatingNote = useStore(s => s.updateFloatingNote)
  const removeFloatingNote = useStore(s => s.removeFloatingNote)
  const addFolder = useStore(s => s.addFolder)
  const renameFolder = useStore(s => s.renameFolder)
  const removeFolder = useStore(s => s.removeFolder)
  const moveNoteToFolder = useStore(s => s.moveNoteToFolder)
  const addTodo = useStore(s => s.addTodo)

  const [editingNote, setEditingNote] = useState<FloatingNote | null>(null)
  const [editorOpen, setEditorOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [showFolderManage, setShowFolderManage] = useState(false)
  const [actionNoteId, setActionNoteId] = useState<string | null>(null)
  const [showInbox, setShowInbox] = useState(false)
  const [showMemorySearch, setShowMemorySearch] = useState(false)
  const [quickCapture, setQuickCapture] = useState(false)
  const [pressing, setPressing] = useState(false)
  const longPressTimer = useRef<ReturnType<typeof setTimeout>>()
  const deletedNoteRef = useRef<FloatingNote[]>([])

  const [inboxCount, setInboxCount] = useState(() => unprocessedCount())
  const [showTrash, setShowTrash] = useState(false)
  const [showKnowledge, setShowKnowledge] = useState(false)
  const [trashNotes, setTrashNotes] = useState<any[]>([])
  const pullRef = useRef<HTMLDivElement>(null)
  const [pullY, setPullY] = useState(0)
  const pullStart = useRef(0)
  const isPulling = useRef(false)

  // ── Reading position tracking (most visible note) ──
  const visibleNotes = useRef<Set<string>>(new Set())
  const readingTimer = useRef<ReturnType<typeof setTimeout>>()
  const [lastVisibleNote, setLastVisibleNote] = useState<string | null>(null)

  // Report reading position when the most visible note changes
  useEffect(() => {
    if (!lastVisibleNote || !tokenManager.isPaired) return
    if (readingTimer.current) clearTimeout(readingTimer.current)
    readingTimer.current = setTimeout(() => {
      pushReadingPosition(lastVisibleNote, 0, 0)
    }, 2000)
    return () => { if (readingTimer.current) clearTimeout(readingTimer.current) }
  }, [lastVisibleNote])

  // Observe note visibility via IntersectionObserver
  const noteRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const setNoteRef = useCallback((noteId: string, el: HTMLDivElement | null) => {
    if (el) noteRefs.current.set(noteId, el)
    else noteRefs.current.delete(noteId)
  }, [])

  const onPullStart = useCallback((e: React.TouchEvent) => {
    if (pullRef.current && pullRef.current.scrollTop <= 0) {
      pullStart.current = e.touches[0].clientY
      isPulling.current = true
    }
  }, [])

  const onPullMove = useCallback((e: React.TouchEvent) => {
    if (!isPulling.current) return
    const dy = e.touches[0].clientY - pullStart.current
    if (dy > 0) setPullY(Math.min(dy * 0.4, 60))
  }, [])

const onPullEnd = useCallback(() => {
    if (pullY > 50) {
      navigator.vibrate?.(15)
      // Rehydrate from localStorage to pick up any changes from sync or other tabs
      useStore.persist.rehydrate()
    }
    setPullY(0)
    isPulling.current = false
  }, [pullY])

  const filtered = useMemo(() => {
    let list = [...notes]
    if (activeFolderId && activeFolderId !== 'all') {
      // 包含当前文件夹及其所有子文件夹的笔记
      const folderIds = new Set<string>([activeFolderId])
      for (const id of collectDescendantFolderIds(activeFolderId, folders)) {
        folderIds.add(id)
      }
      list = list.filter(n => folderIds.has(n.folderId ?? 'default'))
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      list = list.filter(n =>
        (n.title || '').toLowerCase().includes(q) ||
        (n.content || '').toLowerCase().includes(q),
      )
    }
    return list.sort((a, b) => {
      if (a.pinned && !b.pinned) return -1
      if (!a.pinned && b.pinned) return 1
      return (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0)
    })
  }, [notes, activeFolderId, searchQuery, folders])

  // Observe note visibility via stable IntersectionObserver
  const observerRef = useRef<IntersectionObserver | null>(null)
  useEffect(() => {
    observerRef.current = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = entry.target.getAttribute('data-note-id')
          if (!id) continue
          if (entry.isIntersecting) {
            visibleNotes.current.add(id)
          } else {
            visibleNotes.current.delete(id)
          }
        }
        let bestId: string | null = null
        let bestRatio = 0
        for (const entry of entries) {
          const id = entry.target.getAttribute('data-note-id')
          if (!id || !entry.isIntersecting) continue
          if (entry.intersectionRatio > bestRatio) {
            bestRatio = entry.intersectionRatio
            bestId = id
          }
        }
        if (bestId) setLastVisibleNote(bestId)
      },
      { threshold: [0, 0.25, 0.5, 0.75, 1] },
    )
    return () => observerRef.current?.disconnect()
  }, [])

  // Reconnect observer when filtered list changes
  useEffect(() => {
    const obs = observerRef.current
    if (!obs) return
    obs.disconnect()
    for (const el of noteRefs.current.values()) obs.observe(el)
  }, [filtered])

  const grouped = useMemo(() => {
    if (searchQuery.trim()) return [{ label: '', notes: filtered }]
    const groups: { label: string; notes: typeof filtered }[] = []
    const now = new Date()
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
    const yesterdayStart = todayStart - 86400000
    const dayOfWeek = now.getDay()
    const weekStart = todayStart + (dayOfWeek === 0 ? -6 : 1 - dayOfWeek) * 86400000

    const today: typeof filtered = []
    const yesterday: typeof filtered = []
    const thisWeek: typeof filtered = []
    const earlier: typeof filtered = []
    for (const note of filtered) {
      const ts = note.updatedAt || note.createdAt || 0
      if (ts >= todayStart) today.push(note)
      else if (ts >= yesterdayStart) yesterday.push(note)
      else if (ts >= weekStart) thisWeek.push(note)
      else earlier.push(note)
    }
    if (today.length) groups.push({ label: '今天', notes: today })
    if (yesterday.length) groups.push({ label: '昨天', notes: yesterday })
    if (thisWeek.length) groups.push({ label: '本周', notes: thisWeek })
    if (earlier.length) groups.push({ label: '更早', notes: earlier })
    return groups
  }, [filtered, searchQuery])

  const noteCounts = useMemo(() => {
    // 基础计数：每个文件夹的直接笔记
    const directCount = new Map<string, number>()
    for (const n of notes) {
      const folderId = n.folderId ?? 'default'
      directCount.set(folderId, (directCount.get(folderId) || 0) + 1)
    }
    // 递归计数：每个文件夹 = 直接笔记数 + 所有子文件夹笔记数
    const totalCount = new Map<string, number>()
    const getTotal = (folderId: string): number => {
      if (totalCount.has(folderId)) return totalCount.get(folderId)!
      let total = directCount.get(folderId) || 0
      const children = folders.filter(f => (f.parentId ?? null) === folderId)
      for (const c of children) {
        total += getTotal(c.id)
      }
      totalCount.set(folderId, total)
      return total
    }
    for (const f of folders) getTotal(f.id)
    return totalCount
  }, [notes, folders])

  // 递归获取所有子孙文件夹ID（删除/移动时需要）
  const getAllDescendantIds = useCallback((parentId: string | null, allFolders: Folder[]): Set<string> => {
    const ids = new Set<string>()
    const children = allFolders.filter(f => (f.parentId ?? null) === parentId)
    for (const c of children) {
      ids.add(c.id)
      for (const subId of getAllDescendantIds(c.id, allFolders)) ids.add(subId)
    }
    return ids
  }, [])

  // 获取当前选中文件夹的直接子文件夹
  const currentChildFolders = useMemo(() => {
    const parentId = activeFolderId && activeFolderId !== 'all' ? activeFolderId : null
    return folders.filter(f => f.id !== 'default' && (f.parentId ?? null) === parentId)
  }, [folders, activeFolderId])

  // 获取当前选中文件夹的父文件夹（用于返回上一级）
  const parentFolder = useMemo(() => {
    if (!activeFolderId || activeFolderId === 'all') return null
    return folders.find(f => f.id === activeFolderId)?.parentId ?? null
  }, [activeFolderId, folders])

  const otherFolders = folders.filter(f => f.id !== 'default')

  const handleDeleteNote = useCallback((id: string) => {
    const note = notes.find(n => n.id === id)
    if (!note) return
    // Soft-delete: store in trash before removing
    try {
      const trash = JSON.parse(localStorage.getItem('siyi-note-trash') || '[]')
      trash.unshift({ ...note, trashedAt: Date.now() })
      localStorage.setItem('siyi-note-trash', JSON.stringify(trash.slice(0, 50)))
    } catch (e) { console.warn('[notes] trash save failed', e) }
    removeFloatingNote(id)

    // Push to undo stack (most recent first)
    deletedNoteRef.current = [note, ...deletedNoteRef.current].slice(0, 10)
    const pending = deletedNoteRef.current.length

    const doUndo = () => {
      const restored = deletedNoteRef.current.shift()
      if (!restored) return
      addFloatingNote(restored)
      // Remove from trash
      try {
        const trash = JSON.parse(localStorage.getItem('siyi-note-trash') || '[]')
        localStorage.setItem('siyi-note-trash', JSON.stringify(trash.filter((n: any) => n.id !== restored.id)))
      } catch (e) { console.warn('[notes] trash restore failed', e) }
      // If more deletions remain, show toast for the next one
      if (deletedNoteRef.current.length > 0) {
        showToast({
          message: `笔记已删除 (${deletedNoteRef.current.length})`,
          action: { label: '撤销', onClick: doUndo },
        })
      }
    }

    showToast({
      message: pending > 1 ? `笔记已删除 (${pending})` : '笔记已删除',
      action: { label: '撤销', onClick: doUndo },
    })
  }, [notes, removeFloatingNote, addFloatingNote])

  const handleShare = useCallback(async (note: FloatingNote) => {
    const text = `${note.title || ''}\n\n${note.content || ''}`
    if (navigator.share) { try { await navigator.share({ title: note.title || '思忆便签', text }) } catch (e) { /* ignore */ swallowError('NotesView:288', e) } }
    else { await navigator.clipboard.writeText(text) }
    setActionNoteId(null)
  }, [])

  const handleMoveNote = useCallback((noteId: string, folderId: string | null) => {
    moveNoteToFolder(noteId, folderId)
    setActionNoteId(null)
  }, [moveNoteToFolder])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '16px 20px 0', flexShrink: 0,
      }}>
        <div style={{ fontSize: 'var(--text-large-title)', fontWeight: 700, letterSpacing: -0.5 }}>笔记</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          {/* 导入按钮 */}
          <button
            onClick={() => document.getElementById('import-note-file')?.click()}
            aria-label="导入笔记"
            style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', padding: '6px 10px', borderRadius: 'var(--radius-sm)', WebkitTapHighlightColor: 'transparent', fontSize: 'var(--text-caption-1)', fontWeight: 500 }}
          >
            导入
          </button>
          <input
            id="import-note-file"
            type="file"
            accept=".md,.txt,.json"
            style={{ display: 'none' }}
            onChange={async (e) => {
              const file = e.target.files?.[0]
              if (!file) return
              try {
                const text = await file.text()
                addFloatingNote({
                  id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
                  title: file.name.replace(/\.(md|txt|json)$/i, ''),
                  content: text,
                  type: 'text',
                  x: 0, y: 0, zIndex: 1, width: 320, height: 200,
                  createdAt: Date.now(), updatedAt: Date.now(),
                  folderId: activeFolderId && activeFolderId !== 'all' ? activeFolderId : undefined,
                  editMode: 'markdown',
                })
                showToast({ message: `已导入: ${file.name}` })
              } catch {
                showToast({ message: '导入失败' })
              }
              e.target.value = ''
            }}
          />
          <button
            onClick={() => { setShowInbox(true); setInboxCount(unprocessedCount()) }}
            aria-label="收件箱"
            style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', padding: '6px 10px', borderRadius: 'var(--radius-sm)', position: 'relative', WebkitTapHighlightColor: 'transparent' }}
          >
            <IconFolder size={16} />
            {inboxCount > 0 && (
              <span style={{ position: 'absolute', top: 2, right: 4, minWidth: 16, height: 16, borderRadius: 8, background: 'var(--accent)', color: '#FFF', fontSize: 10, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 4px' }}>
                {inboxCount > 9 ? '9+' : inboxCount}
              </span>
            )}
          </button>
          <button onClick={() => setShowMemorySearch(true)} aria-label="搜索" style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', padding: '6px 10px', borderRadius: 'var(--radius-sm)', WebkitTapHighlightColor: 'transparent' }}>
            <IconSearch size={16} />
          </button>
          <button onClick={() => setShowKnowledge(true)} aria-label="知识"
            style={{ background: "none", border: "none", color: "var(--text-tertiary)", cursor: "pointer", padding: "6px 10px", borderRadius: "var(--radius-sm)", WebkitTapHighlightColor: "transparent", fontSize: 'var(--text-caption-1)', fontWeight: 500 }}
          >
            知识
          </button>
          <button onClick={() => setShowTrash(true)} aria-label="回收站" style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', padding: '6px 10px', borderRadius: 'var(--radius-sm)', WebkitTapHighlightColor: 'transparent', fontSize: 'var(--text-caption-1)', fontWeight: 500 }}>
            回收站
          </button>
          <button onClick={() => setShowFolderManage(true)} style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 'var(--text-footnote)', fontWeight: 500, display: 'flex', alignItems: 'center', gap: 4, padding: '6px 10px', borderRadius: 'var(--radius-sm)', WebkitTapHighlightColor: 'transparent' }}>
            <IconFolder size={16} /> 管理
          </button>
        </div>
      </div>

      {/* Search bar */}
      <NotesSearchBar value={searchQuery} onChange={setSearchQuery} onClear={() => setSearchQuery('')} />

      {/* Folder chips */}
      {otherFolders.length === 0 && (
        <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', padding: '8px 20px 4px', display: 'flex', gap: 6 }}>
          <span>点击右上角"管理"创建文件夹</span>
        </div>
      )}
      <div style={{ display: 'flex', gap: 6, padding: '10px 20px 4px', overflowX: 'auto', flexShrink: 0, scrollbarWidth: 'none' }}>
        {/* 返回上一级按钮 */}
        {parentFolder !== null && (
          <Chip
            type="filter"
            label="← 上级"
            onClick={() => setActiveFolder(parentFolder || null)}
          />
        )}
        {/* 根目录的 "全部" 按钮 */}
        {(!activeFolderId || activeFolderId === 'all') && (
          <Chip
            type="filter"
            selected={!activeFolderId || activeFolderId === 'all'}
            label="全部"
            count={notes.length || undefined}
            onClick={() => setActiveFolder(null)}
          />
        )}
        {/* 当前选中文件夹的直接子文件夹 */}
        {currentChildFolders.map(f => (
          <Chip
            key={f.id}
            type="filter"
            selected={activeFolderId === f.id}
            label={f.name}
            count={noteCounts.get(f.id) || undefined}
            onClick={() => setActiveFolder(f.id)}
          />
        ))}
      </div>

      {/* Note list */}
      <div
        ref={pullRef}
        onTouchStart={onPullStart}
        onTouchMove={onPullMove}
        onTouchEnd={onPullEnd}
        style={{
          flex: 1, overflowY: 'auto', padding: '12px 20px',
          transform: pullY > 0 ? `translateY(${pullY}px)` : undefined,
          transition: pullY > 0 ? 'none' : 'transform 0.25s ease',
          WebkitOverflowScrolling: 'touch',
        }}
      >
        {filtered.length === 0 && (
          <EmptyState
            icon={<IconNote size={48} />}
            title={searchQuery ? '没有找到相关内容' : '暂无笔记'}
            description={searchQuery ? '换个关键词试试' : '点击右下角的按钮添加第一条笔记'}
          />
        )}

        {grouped.map(group => (
          <div key={group.label || 'search'}>
            {group.label && (
              <div style={{ fontSize: 'var(--text-footnote)', fontWeight: 600, color: 'var(--text-secondary)', padding: '16px 0 8px' }}>
                {group.label}
              </div>
            )}
            {group.notes.map(note => (
              <div key={note.id} data-note-id={note.id} ref={el => setNoteRef(note.id, el)}>
                <NoteCard
                  note={note}
                  onEdit={(note) => { setEditingNote(note); setEditorOpen(true) }}
                  onAction={() => setActionNoteId(note.id)}
                  onDelete={() => handleDeleteNote(note.id)}
                />
              </div>
            ))}
          </div>
        ))}
        <div style={{ height: 100 }} />
      </div>

      {/* FAB */}
      <button
        onClick={() => { haptic.medium(); setEditingNote(null); setQuickCapture(false); setEditorOpen(true) }}
        onTouchStart={e => {
          setPressing(true); touchScale.onStart(e)
          longPressTimer.current = setTimeout(() => { haptic.medium(); setQuickCapture(true); setEditingNote(null); setEditorOpen(true) }, 500)
        }}
        onTouchEnd={e => { setPressing(false); touchScale.onEnd(e); if (longPressTimer.current) clearTimeout(longPressTimer.current) }}
        onMouseDown={e => { setPressing(true); touchScale.onStart(e); longPressTimer.current = setTimeout(() => { haptic.medium(); setQuickCapture(true); setEditingNote(null); setEditorOpen(true) }, 500) }}
        onMouseUp={e => { setPressing(false); touchScale.onEnd(e); if (longPressTimer.current) clearTimeout(longPressTimer.current) }}
        onMouseLeave={() => { setPressing(false); if (longPressTimer.current) clearTimeout(longPressTimer.current) }}
        aria-label="新建笔记（长按快速捕获）"
        style={{
          position: 'fixed', bottom: 'calc(96px + env(safe-area-inset-bottom, 0px))', right: 20,
          width: 56, height: 56, borderRadius: '50%', background: 'var(--accent)', color: '#FFFFFF',
          border: 'none', cursor: 'pointer', boxShadow: 'var(--shadow-fab)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 10,
          WebkitTapHighlightColor: 'transparent',
        }}
      >
        <IconPlus size={24} />
        {pressing && <div style={{
          position: 'absolute', inset: -3, borderRadius: '50%',
          border: '3px solid transparent', borderTopColor: 'rgba(255,255,255,0.7)',
          animation: 'spin 0.5s linear', pointerEvents: 'none',
        }} />}
      </button>

      <FolderManageSheet
        open={showFolderManage}
        onClose={() => setShowFolderManage(false)}
        folders={folders}
        noteCounts={noteCounts}
        onAddFolder={addFolder}
        onRenameFolder={renameFolder}
        onRemoveFolder={removeFolder}
      />

      <NoteActionSheets
        actionNoteId={actionNoteId}
        onClose={() => setActionNoteId(null)}
        otherFolders={otherFolders}
        noteCounts={noteCounts}
        onDelete={handleDeleteNote}
        onShare={handleShare}
        onMoveNote={handleMoveNote}
      />

      <InboxSheet
        open={showInbox}
        onClose={() => setShowInbox(false)}
        onCreateNote={(text) => {
          addFloatingNote({ id: uid(), title: text.slice(0, 50), content: text, type: 'text', x: 0, y: 0, zIndex: 1, width: 320, height: 200, createdAt: Date.now(), updatedAt: Date.now(), tags: ['inbox'], editMode: 'markdown' })
          setInboxCount(unprocessedCount())
        }}
        onCreateTask={(text) => { addTodo(text); setInboxCount(unprocessedCount()) }}
      />

      <GlobalSearch
        open={showMemorySearch}
        onClose={() => setShowMemorySearch(false)}
        onSelectTodo={() => { showToast({ message: '已切换至待办' }) }}
        onSelectNote={(note) => { setEditingNote(note); setEditorOpen(true) }}
      />

      <NoteEditorSheet
        note={editingNote}
        open={editorOpen}
        quickCapture={quickCapture}
        onClose={() => { setEditingNote(null); setEditorOpen(false); setQuickCapture(false) }}
        onUpdate={(id, data) => updateFloatingNote(id, data)}
        onAddNote={({ title, content, isQuickCapture }) => {
          addFloatingNote({ id: uid(), title: title || '无标题', content, type: 'text', x: 0, y: 0, zIndex: 1, width: 320, height: 200, createdAt: Date.now(), updatedAt: Date.now(), tags: isQuickCapture ? ['mobile_quick_capture'] : [], editMode: 'markdown', folderId: activeFolderId && activeFolderId !== 'all' ? activeFolderId : undefined })
          // Relay quick capture to desktop inbox when paired
          if (isQuickCapture && tokenManager.isPaired && content) {
            relayQuickCapture(content, title)
          }
        }}
      />

      {/* 知识图谱 */}
      {showKnowledge && (
        <KnowledgeView onClose={() => setShowKnowledge(false)} />
      )}

      {/* 回收站 Sheet */}
      {showTrash && (
        <TrashSheet notes={trashNotes} onClose={() => { setShowTrash(false); setTrashNotes([]) }} />
      )}
    </div>
  )
}

function TrashSheet({ notes, onClose }: { notes: any[]; onClose: () => void }) {
  const [list, setList] = useState(notes)

  const handleRestore = async (noteId: string) => {
    try {
      const { callRpc } = await import('../../../lib/rpcClient')
      await callRpc('notes.restore', { note_id: noteId })
      setList(prev => prev.filter(n => n.id !== noteId))
    } catch (e) { console.warn('[trash] restore failed', e) }
  }

  const handlePermanentDelete = async (noteId: string) => {
    try {
      const { callRpc } = await import('../../../lib/rpcClient')
      await callRpc('notes.permanent_delete', { note_id: noteId })
      setList(prev => prev.filter(n => n.id !== noteId))
    } catch (e) { console.warn('[trash] delete failed', e) }
  }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 3000, background: 'var(--bg-primary)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px' }}>
        <span style={{ fontSize: 'var(--text-headline)', fontWeight: 600 }}>回收站</span>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: 'var(--text-body)' }}>关闭</button>
      </div>
      <div style={{ padding: '0 20px', overflow: 'auto', maxHeight: 'calc(100% - 60px)' }}>
        {list.length === 0 ? (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-disabled)', fontSize: 'var(--text-footnote)' }}>回收站为空</div>
        ) : (
          list.map((note: any) => (
            <div key={note.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 0', borderBottom: '0.5px solid var(--border-subtle)' }}>
              <div style={{ flex: 1, fontSize: 'var(--text-footnote)', color: 'var(--text-primary)' }}>
                {note.title || '无标题'}
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <button onClick={() => handleRestore(note.id)} style={{ padding: '4px 10px', borderRadius: 'var(--radius-sm)', border: 'none', background: 'var(--accent-surface)', color: 'var(--accent)', cursor: 'pointer', fontSize: 'var(--text-caption-1)' }}>恢复</button>
                <button onClick={() => handlePermanentDelete(note.id)} style={{ padding: '4px 10px', borderRadius: 'var(--radius-sm)', border: 'none', background: 'var(--bg-tertiary)', color: 'var(--text-tertiary)', cursor: 'pointer', fontSize: 'var(--text-caption-1)' }}>删除</button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
