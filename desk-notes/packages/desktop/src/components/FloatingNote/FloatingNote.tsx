import React, { useRef, useState, useEffect, useCallback, useMemo, Suspense } from 'react'
import Icon from '../Common/Icon'

import { motion } from 'framer-motion'
import { FloatingNote as FN } from '../../types'
import { useStore } from '../../store/useStore'
import ContextMenu from '../Common/ContextMenu'
import { onContentEdit, flushPendingEdit } from '../../store/contentEditTracker'
import type { EditorAction } from './AIChatPanel'
import { useTranslation } from '../../i18n'
import { errorStore } from '../../store/errorStore'
import { updateWikiLinksOnRename, resolveWikiLink, buildBacklinkIndex } from '../../utils/wikiLinks'
import FloatingNoteHeader from './FloatingNoteHeader'
import FloatingNoteContent from './FloatingNoteContent'
import FloatingNoteOverlays from './FloatingNoteOverlays'
import { useNoteVisualStyle } from './useNoteVisualStyle'
import { getContextMenuItems } from './floatingNoteContextMenu'
import { extractTodosFromContent, addNoteTodo, toggleNoteTodo, deleteNoteTodo } from './floatingNoteTodos'
import { handleMouseDown, handleResizeStart } from './floatingNoteDrag'
import { useNoteWiki } from './useNoteWiki'
import { handleViewModeChangeLogic } from './floatingNoteEditor'
import { notePropsEqual } from './FloatingNote.utils'

const AIChatPanel = React.lazy(() => import('./AIChatPanel'))
const VersionHistoryPanelLazy = React.lazy(() => import('./VersionHistoryPanel'))
const ei = window.electronAPI as ElectronAPI | undefined

const isElectron = !!ei

interface Props { note: FN; standalone?: boolean; matched?: boolean; searchKeyword?: string }

const FloatingNote: React.FC<Props> = ({ note, standalone, matched }) => {
  const focusNote = useStore(s => s.focusNote)
  const removeFloatingNote = useStore(s => s.removeFloatingNote)
  const updateFloatingNote = useStore(s => s.updateFloatingNote)
  const moveNote = useStore(s => s.moveNote)
  const backgroundMode = useStore(s => s.backgroundMode)
  const { t } = useTranslation()
  const [pos, setPos] = useState({ x: note.x, y: note.y })
  const [dragging, setDragging] = useState(false)

  // Sync local pos when note.x / note.y change externally
  useEffect(() => {
    setPos({ x: note.x, y: note.y })
  }, [note.x, note.y])
  const [todoText, setTodoText] = useState('')
  const noteOpacity = note.opacity ?? 1

  // editorRef 供浮窗头部 / 覆盖层 / 右键菜单共享（MD 编辑器接管正文后不再有 contentEditable DOM）
  const editorRef = useRef<HTMLDivElement>(null)
  const timeoutIdsRef = useRef<ReturnType<typeof setTimeout>[]>([])
  const [showFormatBar, setShowFormatBar] = useState(true)
  const [showBacklinks, setShowBacklinks] = useState(false)
  const [showChatPanel, setShowChatPanel] = useState(false)

  // Fullscreen state & handler
  const [fullscreen, setFullscreen] = useState(false)
  const noteRef = useRef<HTMLDivElement>(null)

  // View mode（edit/preview/split 为 MD 视图态，非格式态；保留）
  const [viewMode, setViewMode] = useState<'edit' | 'preview' | 'split'>(note.contentViewMode || 'edit')
  const [mdContent, setMdContent] = useState('')

  // content 恒为 Markdown 真源（U0 治本：读即迁移机件已移除，见 NoteEditor 同注）。
  useEffect(() => {
    setMdContent(note.content || '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note.id])

  const handleFullscreenToggle = useCallback(() => {
    if (standalone && isElectron) {
      // Electron standalone: fullscreen the BrowserWindow via IPC
      ei.fullscreen()
    } else if (noteRef.current) {
      // Browser mode: use Fullscreen API on the note element
      if (!document.fullscreenElement) {
        noteRef.current.requestFullscreen?.()
      } else {
        document.exitFullscreen?.()
      }
    }
  }, [standalone])

  // Sync fullscreen state from Fullscreen API events
  useEffect(() => {
    const handler = () => setFullscreen(!!document.fullscreenElement)
    document.addEventListener('fullscreenchange', handler)
    return () => document.removeEventListener('fullscreenchange', handler)
  }, [])

  // Context menu state
  const [ctxMenu, setCtxMenu] = useState<{ x: number; y: number } | null>(null)
  const [ctxImage, setCtxImage] = useState<string | null>(null) // image src when ctxMenu is on an image
  const [resizingFN, setResizingFN] = useState(false)
  const [resizeWidthFN, setResizeWidthFN] = useState(100)
  const resizeTargetRefFN = useRef<HTMLImageElement | null>(null)
  const [lightboxSrcFN, setLightboxSrcFN] = useState<string | null>(null)
  // Image click selection for drag resize
  const selectedImgRefFN = useRef<HTMLImageElement | null>(null)
  const dragStateRefFN = useRef<{ startX: number; startW: number } | null>(null)
  const [selectedImgFN] = useState<HTMLImageElement | null>(null)
  const [showVersionHistory, setShowVersionHistory] = useState(false)
  const [showFolderPicker, setShowFolderPicker] = useState(false)

  // ESC 关闭文件夹选择浮层（2026-10-16 E2E sweep 缺陷修复）
  useEffect(() => {
    if (!showFolderPicker) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowFolderPicker(false)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [showFolderPicker])
  const folders = useStore(s => s.folders)
  const moveNoteToFolder = useStore(s => s.moveNoteToFolder)

  const nd = isElectron && standalone ? ({ WebkitAppRegion: 'no-drag' } as unknown as React.CSSProperties) : undefined

  // Restore alwaysOnTop when Electron standalone mounts
  useEffect(() => {
    if (!standalone || !isElectron) return
    if (note.pinned) ei.alwaysOnTop(true)
  }, [standalone, note.pinned])

  // Lock: disable window resizing + pass mouse events through content area
  // in standalone mode. main.cjs polls cursor position to keep title bar
  // (top 44px) interactive —?unlock/close/minimize buttons still work.
  useEffect(() => {
    if (!standalone || !isElectron) return
    ei.setResizable(!note.locked)
    ei.ignoreMouseEvents({ ignore: !!note.locked })
  }, [standalone, note.locked, isElectron])

  // Fullscreen escape handler
  useEffect(() => {
    if (!fullscreen) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreen(false)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [fullscreen])

  // Cleanup edit tracker, drag listeners, and all tracked timeouts on unmount
  const cleanupDragRef = useRef<(() => void) | null>(null)
  useEffect(() => {
    return () => {
      flushPendingEdit()
      cleanupDragRef.current?.()
      for (const t of timeoutIdsRef.current) clearTimeout(t)
      timeoutIdsRef.current = []
    }
  }, [])

  const { navigateToRef, getCachedFuse } = useNoteWiki()

  const onMouseDown = (e: React.MouseEvent) => {
    handleMouseDown(e, note, standalone, pos, focusNote, moveNote, setDragging, setPos, cleanupDragRef)
  }

  const onResizeStart = (e: React.MouseEvent) => {
    handleResizeStart(e, note, standalone, updateFloatingNote, (id) => useStore.getState().floatingNotes.find(n => n.id === id), cleanupDragRef)
  }

  const handleApplyAction = useCallback((action: EditorAction) => {
    // Markdown 直存真源：AI 面板应用结果写回 content（replace_full 覆盖，其余追加）
    const clean = typeof action.text === 'string' ? action.text.split('\0').join('') : ''
    const cur = mdContent || note.content || ''
    let next: string
    if (action.action === 'replace_full') next = clean
    else if (action.action === 'prepend') next = clean + cur
    else next = cur + clean
    setMdContent(next)
    onContentEdit(note.id)
    updateFloatingNote(note.id, { content: next })
  }, [note.id, note.content, mdContent, updateFloatingNote])

  const handleViewModeChange = useCallback((mode: 'edit' | 'preview' | 'split') => {
    handleViewModeChangeLogic(mode, setViewMode, note.id, updateFloatingNote)
  }, [note.id, updateFloatingNote])

  const handleRevertVersion = useCallback((version: import('../../utils/noteHistory').VersionEntry) => {
    updateFloatingNote(note.id, {
      title: version.title,
      content: version.content,
      todos: version.todos as FN['todos'],
    })
    setShowVersionHistory(false)
  }, [note.id, updateFloatingNote])

  const handleTitleSave = useCallback((newTitle: string) => {
    const oldTitle = note.title
    if (newTitle === oldTitle) return
    updateFloatingNote(note.id, { title: newTitle })
    // Propagate rename to all wiki links
    const allNotes = useStore.getState().floatingNotes.map(n => ({
      id: n.id, title: n.title, content: n.content || '', todos: n.todos,
    }))
    const patches = updateWikiLinksOnRename(oldTitle, newTitle, allNotes)
    for (const p of patches) {
      updateFloatingNote(p.noteId, { content: p.content, todos: p.todos as FN['todos'] })
    }
    if (patches.length > 0) {
      errorStore.success(t('note.refsUpdated', { count: patches.length }))
    }
  }, [note.title, note.id, updateFloatingNote])


  // Compute backlinks for this note
  const allNotesData = useStore(s => s.floatingNotes)
  const backlinks = useMemo(() => {
    const notesWithContent = allNotesData.map(n => ({
      id: n.id, title: n.title, content: n.content || '', todos: n.todos,
    }))
    const index = buildBacklinkIndex(notesWithContent)
    const sourceIds = index.get(note.id) || []
    return sourceIds.map(id => allNotesData.find(n => n.id === id)).filter(Boolean) as typeof allNotesData
  }, [allNotesData, note.id])

  // ── Image click selection + drag resize（作用于预览/覆盖层，正文已 MD 化后为惰性）──
  const handleDragResizeStartFN = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const img = selectedImgRefFN.current
    if (!img || note.locked) return
    dragStateRefFN.current = { startX: e.clientX, startW: img.width }

    const onMove = (ev: MouseEvent) => {
      const ds = dragStateRefFN.current
      if (!ds) return
      const delta = ev.clientX - ds.startX
      img.style.width = `${Math.max(50, ds.startW + delta)}px`
    }
    const onUp = () => {
      dragStateRefFN.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [note.locked])

  // -- Context menu (synthetic, for right-clicks on outer elements) --
  const handleContextMenu = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setCtxMenu({ x: e.clientX, y: e.clientY })
    setCtxImage(null)
  }

  const getContextMenuItemsMemo = useCallback(
    () => getContextMenuItems({
      ctxImage, t, note, editorRef, resizeTargetRefFN,
      setResizingFN, setResizeWidthFN, updateFloatingNote, removeFloatingNote,
      onMoveToFolder: () => setShowFolderPicker(true),
      folders,
    }),
    [ctxImage, t, note, editorRef, resizeTargetRefFN, setResizingFN, setResizeWidthFN, updateFloatingNote, removeFloatingNote, folders],
  )

  // -- Todo handlers --

  const onExtractTodos = useCallback(
    () => extractTodosFromContent(note.content, note.todos, note.id, updateFloatingNote),
    [note.content, note.todos, note.id, updateFloatingNote],
  )

  const onAddNoteTodo = useCallback(
    () => addNoteTodo(note.locked, todoText, note.todos, note.id, updateFloatingNote, setTodoText),
    [note.locked, todoText, note.todos, note.id, updateFloatingNote],
  )

  const onToggleNoteTodo = useCallback(
    (todoId: string) => toggleNoteTodo(todoId, note.locked, note.todos, note.id, updateFloatingNote, t),
    [note.locked, note.todos, note.id, updateFloatingNote, t],
  )

  const onDeleteNoteTodo = useCallback(
    (todoId: string) => deleteNoteTodo(todoId, note.locked, note.todos, note.id, updateFloatingNote, t),
    [note.locked, note.todos, note.id, updateFloatingNote, t],
  )

  // Render todo text with URL/wiki link detection
  const renderTodoText = (text: string) => {
    if (!text) return text || ''
    const parts = text.split(/(https?:\/\/[^\s]+|便签:\/\/[a-zA-Z0-9_-]+|\[\[[^\]|]+?(?:\|[^\]]+)?\]\])/g)
    if (parts.length === 1) return text

    const allNotes = useStore.getState().floatingNotes
    const noteStubs = allNotes.map(n => ({ id: n.id, title: n.title }))
    const fuse = getCachedFuse()

    return parts.map((part, i) => {
      if (/^https?:\/\//.test(part)) {
        return <a key={i} href={part} target="_blank" rel="noopener noreferrer"
          className="text-fluent-blue underline" title={t('note.openLink')}>{part}</a>
      }
      if (/^便签:\/\//.test(part)) {
        const refId = part.replace('便签://', '')
        return <a key={i} href={`#ref-${refId}`}
          className="text-purple-400 underline" title={t('note.jumpToRef')}
          onClick={(e) => { e.preventDefault(); navigateToRef(refId) }}>{part}</a>
      }
      // [[wiki link]]
      if (/^\[\[/.test(part)) {
        const inner = part.slice(2, -2)
        const pipeIdx = inner.indexOf('|')
        const linkTitle = (pipeIdx >= 0 ? inner.slice(0, pipeIdx) : inner).trim()
        const displayText = (pipeIdx >= 0 ? inner.slice(pipeIdx + 1) : inner).trim()
        const resolved = resolveWikiLink(linkTitle, noteStubs, fuse)
        if (resolved) {
          return <a key={i} href={`#ref-${resolved.id}`}
            className="text-purple-400 underline" title={t('note.jumpToRef')}
            onClick={(e) => { e.preventDefault(); navigateToRef(resolved.id) }}>{displayText}</a>
        }
        return <span key={i} className="text-red-400 underline decoration-dashed" title={t('note.linkNotFound', { title: linkTitle })}>{displayText}</span>
      }
      return part
    })
  }

  // -- Visual constants --

  const visualStyle = useNoteVisualStyle(backgroundMode, note.color, note.pinned, note.locked, noteOpacity)

  const { bgColor, backdropFilter, noteBorder, noteBoxShadow } = visualStyle

  const highlightStyle = matched
    ? { boxShadow: '0 0 20px rgba(96,165,250,0.15), 0 0 0 1px rgba(96,165,250,0.15)' }
    : undefined

  // Archived styling
  const isArchived = note.archived

  const handleRemoveNote = useCallback(() => {
    if (note.locked) return
    // Minimize instead of delete — note stays in floatingNotes with floated: false
    // so it appears in the main window's notes/todo list
    updateFloatingNote(note.id, { floated: false })
  }, [note, updateFloatingNote])

  // -- Fullscreen style --

  const fsStyle = fullscreen
    ? {
        position: 'fixed' as const,
        left: 0, top: 0,
        width: '100vw', height: '100dvh',
        zIndex: 99998,
      }
    : undefined

  return (
    <>
      {/* Fullscreen overlay */}
      {fullscreen && (
        <div className="fixed inset-0 z-[99997] bg-black/50 backdrop-blur-sm" onClick={() => setFullscreen(false)} />
      )}

      <motion.div
        initial={{ opacity: 0, scale: standalone ? 1 : 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: standalone ? 1 : 0.9 }}
        transition={{ duration: 0.2 }}
        className={standalone
          ? 'fixed inset-0 w-screen h-screen overflow-hidden flex flex-col'
          : 'absolute rounded-xl overflow-hidden flex flex-col'
        }
        data-note-id={note.id}
        ref={noteRef}
        style={{
          ...fsStyle,
          zIndex: standalone ? undefined : note.zIndex,
          left: standalone ? undefined : pos.x,
          top: standalone ? undefined : pos.y,
          width: standalone ? '100%' : note.width,
          height: standalone ? '100vh' : note.height,
          background: bgColor,
          backdropFilter,
          border: standalone ? 'none' : noteBorder,
          boxShadow: standalone ? 'none' : (highlightStyle?.boxShadow || noteBoxShadow),
          cursor: standalone ? 'default' : (note.locked ? 'default' : dragging ? 'grabbing' : 'default'),
          opacity: isArchived ? 0.6 : 1,
          pointerEvents: 'auto',
        }}
        onContextMenu={handleContextMenu}
      >
        <FloatingNoteHeader
          note={note}
          standalone={standalone}
          fullscreen={fullscreen}
          noteOpacity={noteOpacity}
          handleMouseDown={onMouseDown}
          handleFullscreenToggle={handleFullscreenToggle}
          handleTitleSave={handleTitleSave}
          onRemove={handleRemoveNote}
          editorRef={editorRef}
          timeoutIdsRef={timeoutIdsRef}
        />

        <FloatingNoteContent
          note={note}
          isArchived={isArchived}
          nd={nd}
          viewMode={viewMode}
          mdContent={mdContent}
          showFormatBar={showFormatBar}
          backlinks={backlinks}
          showBacklinks={showBacklinks}
          todoText={todoText}
          onViewModeChange={handleViewModeChange}
          onShowFormatBarChange={setShowFormatBar}
          onMdContentChange={setMdContent}
          onShowBacklinksChange={setShowBacklinks}
          onShowChatPanelChange={setShowChatPanel}
          onTodoTextChange={setTodoText}
          onExtractTodos={onExtractTodos}
          onAddTodo={onAddNoteTodo}
          onToggleTodo={onToggleNoteTodo}
          onDeleteTodo={onDeleteNoteTodo}
          onRenderTodoText={renderTodoText}
        />

        {/* Resize handle - hidden when archived */}
        {!standalone && !note.locked && !note.collapsed && !isArchived && (
          <div onMouseDown={onResizeStart}
            className="absolute bottom-0 right-0 w-5 h-5 cursor-se-resize flex-shrink-0 z-10 flex items-end justify-end">
            <svg className="w-3 h-3 text-white/[0.12] mb-0.5 mr-0.5" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth={1.5}>
              <path d="M10 0v10H0" />
            </svg>
          </div>
        )}

      </motion.div>

      {/* Context menu —?outside motion.div to avoid overflow-hidden clipping */}
      {ctxMenu && (
        <ContextMenu
          x={ctxMenu.x}
          y={ctxMenu.y}
          items={getContextMenuItemsMemo()}
          onClose={() => setCtxMenu(null)}
        />
      )}

      <FloatingNoteOverlays
        noteId={note.id}
        resizingFN={resizingFN}
        resizeWidthFN={resizeWidthFN}
        resizeTargetRefFN={resizeTargetRefFN}
        selectedImgFN={selectedImgFN}
        lightboxSrcFN={lightboxSrcFN}
        editorRef={editorRef}
        onSetResizingFN={setResizingFN}
        onSetResizeWidthFN={setResizeWidthFN}
        onSetLightboxSrcFN={setLightboxSrcFN}
        onDragResizeStart={handleDragResizeStartFN}
        t={t}
      />

      {/* Version history panel */}
      {showVersionHistory && (
        <Suspense fallback={null}><VersionHistoryPanelLazy
          note={note}
          onClose={() => setShowVersionHistory(false)}
          onRevert={handleRevertVersion}
        /></Suspense>
      )}

      {/* Folder picker overlay */}
      {showFolderPicker && (
        <div
          className="fixed inset-0 z-[10002] flex items-center justify-center"
          role="dialog"
          aria-modal="true"
          aria-label="选择文件夹"
          onClick={() => setShowFolderPicker(false)}
        >
          <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" />
          <div
            className="relative w-56 max-h-64 overflow-y-auto rounded-xl border shadow-2xl p-1.5"
            style={{ background: 'var(--panel-bg-solid)', borderColor: 'var(--panel-border-accent)' }}
            onClick={e => e.stopPropagation()}
          >
            <div className="text-xs font-medium px-2 py-1.5" style={{ color: 'var(--text-secondary)' }}>
              {t('folder.moveToFolder')}
            </div>
            <button
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs transition-colors hover:bg-white/[0.06] text-left"
              style={{ color: 'var(--text-primary)' }}
              onClick={() => {
                moveNoteToFolder(note.id, 'default')
                setShowFolderPicker(false)
              }}
            >
              <Icon icon="fa-regular fa-folder text-xs" />
              {t('folder.allNotes')}
            </button>
            {folders.filter(f => f.id !== 'default').map(f => (
              <button
                key={f.id}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs transition-colors hover:bg-white/[0.06] text-left"
                style={{ color: 'var(--text-primary)' }}
                onClick={() => {
                  moveNoteToFolder(note.id, f.id)
                  setShowFolderPicker(false)
                }}
              >
                <Icon icon="fa-regular fa-folder text-xs" />
                {f.name}
              </button>
            ))}
            <button
              className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs transition-colors hover:bg-white/[0.06] text-left"
              style={{ color: 'var(--text-muted)' }}
              onClick={() => {
                moveNoteToFolder(note.id, null)
                setShowFolderPicker(false)
              }}
            >
              <Icon icon="fa-regular fa-folder-minus text-xs" />
              {t('folder.noFolder')}
            </button>
          </div>
        </div>
      )}

      {/* AI Chat panel —?overlays inside the note content area */}
      {showChatPanel && (
        <Suspense fallback={null}><AIChatPanel
          noteId={note.id}
          noteContent={note.content || ''}
          noteTitle={note.title}
          initialX={note.x + 8}
          initialY={note.y + 38}
          editorSelectedText={''}
          onApplyAction={handleApplyAction}
          onClose={() => setShowChatPanel(false)}
        /></Suspense>
      )}
    </>
  )
}

export default React.memo(FloatingNote, notePropsEqual)
