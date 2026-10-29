import React, { useRef, useState, useEffect, useCallback, useMemo, Suspense } from 'react'
import Icon from './Icon'

import { useStore, flushNotePersistence } from '../../store/useStore'
import { onContentEdit, flushPendingEdit } from '../../store/contentEditTracker'
import { stripHtml } from '../../utils/helpers'
import { getAIProvider, getWsClient } from '../../utils/ai'
import { DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL, AI_PATTERN_FAIL_PREFIX, type AIConfig } from '@desk-notes/shared'
import type { ContextStore } from '@uiw/react-md-editor'
import { useTranslation } from '../../i18n'
import FormatToolbar from '../FloatingNote/FormatToolbar'
import RelatedMemories from '../Modules/RelatedMemories'
import { errorStore } from '../../store/errorStore'
import { swallowError } from '../../utils/swallowError'

function getActiveAIProviderConfig(): AIConfig {
  const s = useStore.getState()
  const profile = s.activeProfileId
    ? s.aiProfiles.find(p => p.id === s.activeProfileId)
    : (s.aiProfiles.length > 0 ? s.aiProfiles[0] : null)
  // 后端可用时优先走 WebSocket（让云端 AI / 路由器决定调哪个 provider）；
  // 否则回退本地 Ollama / 用户配置的 profile。
  let backendConnected = false
  try {
    backendConnected = getWsClient().status === 'connected'
  } catch (e) { /* SSR / cold start */ swallowError('NoteEditor:20', e) }
  if (backendConnected) {
    return {
      enabled: true,
      apiUrl: profile?.apiUrl ?? DEFAULT_LOCAL_API_URL,
      model: profile?.model ?? DEFAULT_LOCAL_MODEL,
      protocol: profile?.protocol ?? 'ollama',
      autoTagging: s.aiAutoTagging ?? false,
      strategy: 'ws',
    }
  }
  if (profile) {
    return { enabled: s.aiEnabled, apiUrl: profile.apiUrl, model: profile.model, protocol: profile.protocol, autoTagging: s.aiAutoTagging ?? false }
  }
  return { enabled: s.aiEnabled, apiUrl: DEFAULT_LOCAL_API_URL, model: DEFAULT_LOCAL_MODEL, protocol: 'ollama', autoTagging: s.aiAutoTagging ?? false }
}

const MdPreview = React.lazy(() => import('../FloatingNote/MdPreview'))
const MDEditor = React.lazy(() => import('@uiw/react-md-editor'))

const NoteEditor: React.FC = () => {
  const { t } = useTranslation()
  const editingNoteId = useStore(s => s.editingNoteId)
  const note = useStore(s => editingNoteId ? s.floatingNotes.find(n => n.id === editingNoteId) : undefined)
  const updateFloatingNote = useStore(s => s.updateFloatingNote)
  const setEditingNoteId = useStore(s => s.setEditingNoteId)
  const removeFloatingNote = useStore(s => s.removeFloatingNote)

  const [viewMode, setViewMode] = useState<'edit' | 'preview' | 'split'>('edit')
  const [mdContent, setMdContent] = useState('')
  // V1-4：退出编辑器时的"保存中"闸门，避免保存 Promise 未落定就销毁组件
  const [exiting, setExiting] = useState(false)
  const exitingRef = useRef(false)
  // V1-4：乐观锁冲突（后端版本 ≠ 本地基线时挂起写入，等用户裁决）
  const noteConflict = useStore(s => s.noteConflict)
  const resolveNoteConflict = useStore(s => s.resolveNoteConflict)

  const [debouncedText, setDebouncedText] = useState('')
  const aiEnabled = useStore(s => s.aiEnabled)
  const [organizing, setOrganizing] = useState(false)
  const [organizeResult, setOrganizeResult] = useState<string | null>(null)
  const organizeAbortRef = useRef<AbortController | null>(null)

  // 整理结果弹窗 ESC 关闭（2026-10-16：modal 家族一致性——此前 ESC 无效，
  // 弹窗挡住编辑页全部交互，即用户报告的"笔记编辑页面无法使用"主诉之一）
  useEffect(() => {
    if (organizeResult === null) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOrganizeResult(null)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [organizeResult])

  // AI 整理失败的前缀（后端 _organize AI 异常时返回"整理失败：…"字符串）——
  // 此种结果**禁止写回笔记**（确认替换禁用），防错误文案覆盖原文（数据破坏）
  const organizeFailed = typeof organizeResult === 'string' && organizeResult.startsWith(AI_PATTERN_FAIL_PREFIX)
  // Typora 迁入提示：一次性可关闭（localStorage 记忆）
  const [showTyporaTip, setShowTyporaTip] = useState(() => {
    try { return localStorage.getItem('siyi-typora-tip-dismissed') !== '1' } catch { return true }
  })
  // 仍不渲染的 Typora 元素：[TOC] / YAML front matter / 非协议图片路径（mermaid 已支持渲染）
  const typoraMarkerCount = useMemo(() => {
    if (!mdContent) return 0
    let n = 0
    if (/^\s*\[TOC\]\s*$/im.test(mdContent)) n++
    // front matter：仅看文档头部若干行里的独立 '---' 行
    if (/^---\s*$/m.test(mdContent.split('\n', 5).join('\n'))) n++
    if (/!\[[^\]]*\]\((?!siyi-file:|https?:|data:)[^)]+\)/.test(mdContent)) n++
    return n
  }, [mdContent])

  // ── AI autocomplete（Markdown 编辑器）──
  const mdEditorRef = useRef<ContextStore | null>(null)
  const getMdTextarea = useCallback((): HTMLTextAreaElement | null => {
    const store = mdEditorRef.current
    if (store?.textarea && store.textarea.isConnected) return store.textarea
    return (store?.container?.querySelector?.('textarea') as HTMLTextAreaElement | null) ?? null
  }, [])
  const [mdAutocomplete, setMdAutocomplete] = useState<{ text: string; loading: boolean } | null>(null)
  const mdAutocompleteAbortRef = useRef<AbortController | null>(null)

  // Cleanup on unmount
  useEffect(() => {
    return () => { organizeAbortRef.current?.abort(); mdAutocompleteAbortRef.current?.abort() }
  }, [])

  // C4: Auto-exit when editing note is removed from store (e.g., deleted from another window)
  const noteExists = useStore(s => editingNoteId ? s.floatingNotes.some(n => n.id === editingNoteId) : true)
  useEffect(() => {
    if (editingNoteId && !noteExists) {
      setEditingNoteId(null)
    }
  }, [noteExists, editingNoteId, setEditingNoteId])

  // content 恒为 Markdown 真源（U0 治本 2026-09-19：读即迁移机件已整体移除——
  // contentFormat 全库无写入者 + 真库 0 篇 HTML，该路径零触发；真有 HTML 的笔记
  // 由预览 rehype-raw 可见兜底，不再有任何「打开即改写」行为。）
  useEffect(() => {
    if (!note) return
    setViewMode(note.contentViewMode || 'edit')
    setMdContent(note.content || '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [note?.id])

  // ── V1-4「打不丢字」：强制 flush（失焦 / 切页 / 退出编辑器 / 窗口隐藏）──
  // Markdown 模式下每次编辑已实时落 store（handleMdChange），flush 只需清编辑跟踪与落盘。
  const flushRef = useRef<() => Promise<void>>(async () => {})
  const liveNoteIdRef = useRef<string | null>(note?.id ?? null)
  liveNoteIdRef.current = note?.id ?? null

  /** 三层落盘：store → （后端 notes.update 串行链 + 本地 .md），可 await */
  const flushSave = useCallback(async (): Promise<void> => {
    flushPendingEdit()
    const id = note?.id
    try {
      await flushNotePersistence(id)
    } catch (e: unknown) {
      console.error('[NoteEditor] flush save failed:', e)
    }
  }, [note?.id])
  flushRef.current = flushSave

  // Flush pending edits on unmount —— 保存链在 store 侧，组件销毁不会打断在途写入
  useEffect(() => {
    return () => {
      flushPendingEdit()
      void flushNotePersistence(liveNoteIdRef.current ?? undefined)
    }
  }, [])

  /** 退出编辑器：先 await 保存 Promise，再真正销毁（保存未完成不切走） */
  const exitEditor = useCallback(async () => {
    if (exitingRef.current) return
    exitingRef.current = true
    setExiting(true)
    try {
      await flushRef.current()
      setEditingNoteId(null)
    } finally {
      exitingRef.current = false
      setExiting(false)
    }
  }, [setEditingNoteId])

  // Debounce note content for RelatedMemories
  useEffect(() => {
    if (!note) return
    const tm = setTimeout(() => {
      setDebouncedText(stripHtml(note.content || '').trim().slice(0, 500))
    }, 1500)
    return () => clearTimeout(tm)
  }, [note?.content, note?.id])

  const handleTitleChange = useCallback((val: string) => {
    if (!note) return
    updateFloatingNote(note.id, { title: val })
  }, [note, updateFloatingNote])

  const handleMdChange = useCallback((val: string) => {
    setMdContent(val)
    if (!note) return
    onContentEdit(note.id)
    // content 恒为 Markdown 真源：直存 MD，不再转 HTML
    updateFloatingNote(note.id, { content: val })
  }, [note, updateFloatingNote])

  // ── U0 方案 A：粘贴/拖拽图片 → notes.asset.put → 相对路径 markdown（Typora 兼容）──
  const insertImageAsset = useCallback(async (file: File) => {
    if (!note) return
    try {
      const buf = await file.arrayBuffer()
      const bytes = new Uint8Array(buf)
      let binary = ''
      const chunk = 0x8000
      for (let i = 0; i < bytes.length; i += chunk) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
      }
      const dataBase64 = btoa(binary)
      const ext = (file.name.match(/\.(png|jpe?g|gif|webp|svg|bmp)$/i)?.[0] || '.png').toLowerCase()
      const name = `${note.id}-${Date.now().toString(36)}${ext}`
      const client = getWsClient()
      if (client.status !== 'connected') {
        errorStore.error(t('noteEditor.assetBackendDown'))
        return
      }
      const res = await client.call<{ success?: boolean; rel_path?: string; error?: string }>('notes.asset.put', { name, dataBase64 })
      if (!res?.success || !res.rel_path) {
        errorStore.error(`${t('noteEditor.assetSaveFailed')}: ${res?.error || ''}`)
        return
      }
      const md = `![${file.name || 'image'}](${res.rel_path})`
      const ta = getMdTextarea()
      const pos = ta?.selectionStart ?? mdContent.length
      const next = mdContent.slice(0, pos) + md + mdContent.slice(pos)
      setMdContent(next)
      onContentEdit(note.id)
      updateFloatingNote(note.id, { content: next })
    } catch {
      errorStore.error(t('noteEditor.assetSaveFailed'))
    }
  }, [note, mdContent, updateFloatingNote, getMdTextarea, t])

  const handleEditorPaste = useCallback((e: React.ClipboardEvent) => {
    const files = Array.from(e.clipboardData?.files || []).filter(f => f.type.startsWith('image/'))
    if (files.length) {
      e.preventDefault()
      void Promise.all(files.map(insertImageAsset))
    }
  }, [insertImageAsset])

  const handleEditorDrop = useCallback((e: React.DragEvent) => {
    const files = Array.from(e.dataTransfer?.files || []).filter(f => f.type.startsWith('image/'))
    if (files.length) {
      e.preventDefault()
      void Promise.all(files.map(insertImageAsset))
    }
  }, [insertImageAsset])

  const handleMdEditorKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>, md: string) => {
    // Esc → dismiss
    if (e.key === 'Escape' && mdAutocomplete) {
      e.preventDefault()
      setMdAutocomplete(null)
      mdAutocompleteAbortRef.current?.abort()
      return
    }
    // Tab → accept autocomplete
    if (e.key === 'Tab' && mdAutocomplete && !mdAutocomplete.loading) {
      e.preventDefault()
      const ta = getMdTextarea()
      if (ta) {
        const start = ta.selectionStart ?? md.length
        const next = md.slice(0, start) + mdAutocomplete.text + md.slice(start)
        setMdContent(next)
        if (note) { onContentEdit(note.id); updateFloatingNote(note.id, { content: next }) }
        // restore cursor after the inserted text
        requestAnimationFrame(() => {
          const ta2 = getMdTextarea()
          if (ta2) {
            const pos = start + mdAutocomplete.text.length
            ta2.setSelectionRange(pos, pos)
          }
        })
      }
      setMdAutocomplete(null)
      return
    }
    // Ctrl+I → trigger autocomplete
    if ((e.ctrlKey || e.metaKey) && e.key === 'i') {
      e.preventDefault()
      if (!note || !aiEnabled || mdAutocomplete?.loading) return
      mdAutocompleteAbortRef.current?.abort()
      const ta = getMdTextarea()
      const prefix = md.slice(0, ta?.selectionStart ?? md.length).trim()
      if (prefix.length < 30) return
      const controller = new AbortController()
      mdAutocompleteAbortRef.current = controller
      setMdAutocomplete({ text: '', loading: true })
      const provider = getAIProvider(getActiveAIProviderConfig())
      provider.processText('continue', prefix, (chunk) => {
        setMdAutocomplete(prev => prev ? { text: chunk, loading: false } : null)
      }, controller.signal).then(fullText => {
        setMdAutocomplete(prev => prev ? { text: fullText, loading: false } : null)
      }).catch((err: Error) => {
        if (err.name === 'AbortError') return
        setMdAutocomplete(null)
      })
    }
  }, [note, aiEnabled, mdAutocomplete, getMdTextarea])

  const handleFloat = useCallback(async () => {
    if (!note) return
    // 浮出前先 await 落盘，浮窗拿到的一定是最新内容（防抖窗口内的字不滞留）
    flushPendingEdit()
    updateFloatingNote(note.id, { floated: true, zIndex: 999999 })
    await flushNotePersistence(note.id)
    const ei = window.electronAPI
    if (ei?.createFloatingWindow) {
      const noteData = useStore.getState().floatingNotes.find(n => n.id === note.id)
      if (noteData) {
        ei.createFloatingWindow({
          id: note.id,
          screenX: 150 + Math.random() * 80,
          screenY: 100 + Math.random() * 80,
          width: note.width || 360,
          height: note.height || 300,
          noteData,
        })
      }
    }
    setEditingNoteId(null)
  }, [note, updateFloatingNote, setEditingNoteId])

  const handleDelete = useCallback(() => {
    if (!note) return
    if (!window.confirm(t('note.deleteConfirm'))) return
    removeFloatingNote(note.id)
    setEditingNoteId(null)
  }, [note, removeFloatingNote, setEditingNoteId, t])

  // ── Idle detection + suggestion bubble ──
  const idleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const suggestedRef = useRef(false)
  const [showSuggestion, setShowSuggestion] = useState(false)
  const [suggestionText, setSuggestionText] = useState('')

  const clearIdleTimer = useCallback(() => {
    if (idleTimerRef.current) {
      clearTimeout(idleTimerRef.current)
      idleTimerRef.current = null
    }
  }, [])

  const resetIdleTimer = useCallback(() => {
    if (!aiEnabled || suggestedRef.current) return
    clearIdleTimer()
    idleTimerRef.current = setTimeout(() => {
      if (!note) return
      const plainText = stripHtml(note.content || '').trim()
      if (plainText.length < 100) return // too short to need organizing
      // Heuristic: content with few line breaks relative to length might be messy
      const lines = plainText.split('\n').length
      const avgLineLen = plainText.length / lines
      if (avgLineLen > 80 && plainText.length > 200) {
        suggestedRef.current = true
        setSuggestionText(t('note.aiOrganize.suggestion'))
        setShowSuggestion(true)
      }
    }, 8000)
  }, [aiEnabled, note, clearIdleTimer, t])

  // Restart idle timer when content changes
  useEffect(() => {
    if (!note) return
    resetIdleTimer()
    return clearIdleTimer
  }, [note?.content, note, resetIdleTimer, clearIdleTimer])

  // Reset suggestion when switching notes
  useEffect(() => {
    suggestedRef.current = false
    setShowSuggestion(false)
  }, [note?.id])

  const handleOrganize = useCallback(async () => {
    if (!note || !aiEnabled || organizing) return
    const plainText = stripHtml(note.content || '').trim()
    if (!plainText) return

    setOrganizing(true)
    setOrganizeResult(null)
    const controller = new AbortController()
    organizeAbortRef.current = controller

    try {
      const provider = getAIProvider(getActiveAIProviderConfig())
      const result = await provider.processAction('organize', plainText, controller.signal)
      setOrganizeResult(result)
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      errorStore.error(t('noteEditor.organizeFailedFmt', { error: err instanceof Error ? err.message : t('noteEditor.unknownError') }))
    } finally {
      setOrganizing(false)
      organizeAbortRef.current = null
    }
  }, [note, aiEnabled, organizing, t])

  const handleSuggestOrganize = useCallback(() => {
    setShowSuggestion(false)
    handleOrganize()
  }, [handleOrganize])

  const handleDismissSuggestion = useCallback(() => {
    setShowSuggestion(false)
  }, [])

  const handleApplyOrganize = useCallback(() => {
    if (!note || !organizeResult) return
    // AI 整理结果为 Markdown 文本，直接作为真源写回（content 恒为 MD）
    updateFloatingNote(note.id, { content: organizeResult })
    onContentEdit(note.id)
    setMdContent(organizeResult)
    setOrganizeResult(null)
  }, [note, organizeResult, updateFloatingNote])

  // C3: Fallback when note is not found — show back button instead of dead end
  if (!note) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3">
        <p className="text-text-muted text-xs">{t('common.notFound')}</p>
        <button
          onClick={() => setEditingNoteId(null)}
          className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] text-text-secondary hover:text-text-secondary hover:bg-white/[0.06] transition-all"
        >
          <Icon icon="fa-solid fa-chevron-left text-[8px]" />
          {t('kanban.backToList')}
        </button>
      </div>
    )
  }

  const showEdit = viewMode === 'edit' || viewMode === 'split'
  const showPreview = viewMode === 'preview' || viewMode === 'split'

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* V1-4：乐观锁冲突行内裁决条（D11：写操作冲突=行内可操作提示，禁静默覆盖） */}
      {noteConflict && note?.id === noteConflict.id && (
        <div
          role="alert"
          className="mx-3 mt-2 px-3 py-2 rounded-lg border border-amber-500/40 bg-amber-500/10 text-[11px] text-text-primary flex items-start gap-3"
        >
          <Icon icon="fa-solid fa-triangle-exclamation text-[10px] text-amber-400 mt-0.5" />
          <span className="flex-1 leading-relaxed">{t('noteEditor.conflictDetected')}</span>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() => { void resolveNoteConflict('keep-local').catch(() => {}) }}
              className="px-2.5 py-1 rounded-md text-[10px] bg-amber-500/20 hover:bg-amber-500/30 text-amber-200 transition-colors"
            >
              {t('noteEditor.conflictKeepLocal')}
            </button>
            <button
              onClick={() => { void resolveNoteConflict('take-cloud').catch(() => {}) }}
              className="px-2.5 py-1 rounded-md text-[10px] bg-white/[0.06] hover:bg-white/[0.12] text-text-secondary transition-colors"
            >
              {t('noteEditor.conflictTakeCloud')}
            </button>
          </div>
        </div>
      )}
      {/* Top bar: back + title + actions */}
      <div className="flex items-center gap-2 px-3 py-1 border-b border-white/[0.04]">
        <button
          onClick={() => { void exitEditor() }}
          disabled={exiting}
          aria-label={t('kanban.backToList')}
          title={t('kanban.backToList')}
          className="flex items-center gap-1 text-[10px] text-text-secondary hover:text-text-secondary transition-colors disabled:opacity-50"
        >
          <Icon icon={`fa-solid ${exiting ? 'fa-spinner fa-spin' : 'fa-chevron-left'} text-[8px]`} />
          {exiting ? t('noteEditor.savingBeforeExit') : t('kanban.backToList')}
        </button>
        <div className="flex-1" />
        {aiEnabled && (
          <button
            onClick={handleOrganize}
            disabled={organizing}
            className="flex items-center gap-1 text-[10px] text-text-secondary hover:text-fluent-blue/70 transition-colors disabled:opacity-40"
            title={t('ai.toolbar.organize')}
          >
            <Icon icon={`fa-solid ${organizing ? 'fa-spinner fa-spin' : 'fa-broom'} text-[8px]`} />
            {organizing ? t('note.aiOrganize.organizing') : t('note.aiOrganize.organize')}
          </button>
        )}
        <button
          onClick={handleFloat}
          className="flex items-center gap-1 text-[10px] text-text-secondary hover:text-text-secondary transition-colors"
          title={t('note.floatMode')}
        >
          <Icon icon="fa-regular fa-window-restore text-[8px]" />
        </button>
        <button
          onClick={handleDelete}
          className="flex items-center gap-1 text-[10px] text-text-secondary hover:text-red-400/60 transition-colors"
          title={t('common.delete')}
        >
          <Icon icon="fa-regular fa-trash-can text-[8px]" />
        </button>
      </div>

      {/* U0 Typora-import tip: math/raw-HTML/soft-breaks/task-lists/mermaid now render,
          but [TOC] / YAML front matter do not; relative-path images
          require assets inside the app attachment directory. */}
      {typoraMarkerCount > 0 && showTyporaTip && (
        <div className="mx-3 mt-2 px-3 py-2 rounded-lg border border-sky-500/40 bg-sky-500/10 text-[11px] text-text-primary flex items-start gap-3">
          <Icon icon="fa-solid fa-circle-info text-[10px] text-sky-400 mt-0.5" />
          <span className="flex-1 leading-relaxed">{t('noteEditor.typoraTip')}</span>
          <button
            onClick={() => { setShowTyporaTip(false); try { localStorage.setItem('siyi-typora-tip-dismissed', '1') } catch { /* 隐私模式忽略 */ } }}
            className="shrink-0 text-text-muted hover:text-text-secondary"
            title={t('common.close')}
          >
            <Icon icon="fa-solid fa-xmark text-[10px]" />
          </button>
        </div>
      )}
      {/* Title */}
      <div className="px-3 pt-2 pb-1">
        <input
          value={note.title || ''}
          onChange={e => handleTitleChange(e.target.value)}
          placeholder={t('note.newNote')}
          className="w-full bg-transparent text-[11px] font-medium outline-none text-text-primary placeholder:text-text-dim"
        />
      </div>

      {/* FormatToolbar (fixed horizontal bar) — view mode + font size + AI chat */}
      <div className="px-2 pb-1">
        <FormatToolbar
          x={0} y={0} visible={true} containerEl={null} onClose={() => {}}
          fixed
          viewMode={viewMode}
          onViewModeChange={setViewMode}
          noteId={note.id}
        />
      </div>

      {/* Organize result modal */}
      {organizeResult !== null && (
        <div
          className="absolute inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-label={t('note.aiOrganize.resultTitle')}
        >
          <div
            className="rounded-xl border shadow-2xl max-w-[90%] max-h-[85%] w-[700px] flex flex-col"
            style={{ background: 'var(--panel-bg-solid, rgba(28,28,38,0.98))', borderColor: 'var(--border-color, rgba(255,255,255,0.08))' }}
          >
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-white/[0.06] flex-shrink-0">
              <span className="text-[11px] font-medium text-text-secondary flex items-center gap-1.5">
                <Icon icon="fa-solid fa-broom text-fluent-blue/60 text-xs" />
                {t('note.aiOrganize.resultTitle')}
              </span>
              <button
                onClick={() => setOrganizeResult(null)}
                className="w-5 h-5 flex items-center justify-center rounded text-text-muted hover:text-white hover:bg-white/[0.08] transition-all"
              >
                <Icon icon="fa-solid fa-xmark text-[10px]" />
              </button>
            </div>
            {/* Content: side-by-side */}
            <div className="flex-1 flex min-h-0">
              {/* Original */}
              <div className="flex-1 flex flex-col min-w-0 border-r border-white/[0.06]">
                <div className="px-3 py-1.5 text-[8px] text-text-muted uppercase tracking-wider border-b border-white/[0.04] flex-shrink-0">
                  {t('note.aiOrganize.original')}
                </div>
                <div className="flex-1 overflow-y-auto p-3 text-xs text-text-secondary leading-relaxed whitespace-pre-wrap select-text">
                  {stripHtml(note?.content || '')}
                </div>
              </div>
              {/* Organized */}
              <div className="flex-1 flex flex-col min-w-0">
                <div className="px-3 py-1.5 text-[8px] text-fluent-blue/50 uppercase tracking-wider border-b border-white/[0.04] flex-shrink-0">
                  {t('note.aiOrganize.organized')}</div>
                <div className="flex-1 overflow-y-auto p-3 text-xs text-text-primary leading-relaxed whitespace-pre-wrap select-text">
                  {organizeResult}
                </div>
              </div>
            </div>
            {/* Actions */}
            <div className="flex items-center justify-end gap-2 px-4 py-2 border-t border-white/[0.06] flex-shrink-0">
              {organizeFailed && (
                <span className="mr-auto text-[10px] text-red-400/90">
                  {t('note.aiOrganize.failedHint')}
                </span>
              )}
              <button
                onClick={() => setOrganizeResult(null)}
                className="px-3 py-1.5 rounded-lg text-xs text-text-secondary hover:text-text-secondary hover:bg-white/[0.06] transition-all"
              >
                {t('common.cancel')}
              </button>
              <button
                onClick={handleApplyOrganize}
                disabled={organizeFailed}
                className="px-3 py-1.5 rounded-lg text-xs text-text-primary bg-fluent-blue/20 hover:bg-fluent-blue/30 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Icon icon="fa-solid fa-check text-[10px] mr-1" />
                {t('note.aiOrganize.confirmReplace')}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Markdown AI autocomplete hint */}
      {mdAutocomplete && mdAutocomplete.loading && (
        <div className="absolute bottom-2 right-2 flex items-center gap-1.5 px-2 py-1 rounded-lg text-[8px] text-fluent-blue/60 bg-fluent-blue/[0.06] border border-fluent-blue/10">
          <Icon icon="fa-solid fa-spinner fa-spin text-[7px]" />
          {t('noteEditor.autocompleting')}
        </div>
      )}
      {mdAutocomplete && !mdAutocomplete.loading && (
        <div className="absolute bottom-2 right-2 flex items-center gap-1.5 px-2 py-1 rounded-lg text-[8px] text-text-secondary bg-black/40 backdrop-blur-sm border border-white/[0.06]">
          <span className="truncate max-w-[200px]">{mdAutocomplete.text.slice(0, 60)}</span>
          <span className="text-text-dim">{t('noteEditor.tabAccept')}</span>
          <span className="text-text-dim">{t('noteEditor.escDismiss')}</span>
        </div>
      )}

      {/* Content: edit + preview panes */}
      <div className="flex-1 flex min-h-0 relative">
        {/* Suggestion bubble — shown when idle detection triggers */}
        {showSuggestion && !organizing && (
          <div className="absolute top-0 left-0 right-0 z-30 flex items-center gap-2 px-3 py-1.5 bg-fluent-blue/8 border-b border-fluent-blue/15 backdrop-blur-sm">
            <Icon icon="fa-solid fa-lightbulb text-[10px] text-amber-400/70" />
            <span className="flex-1 text-[10px] text-text-secondary">{suggestionText}</span>
            <button
              onClick={handleSuggestOrganize}
              className="px-2 py-0.5 rounded text-[8px] text-text-primary bg-fluent-blue/20 hover:bg-fluent-blue/30 transition-all"
            >
              <Icon icon="fa-solid fa-broom text-[7px] mr-1" />
              {t('note.aiOrganize.organize')}
            </button>
            <button
              onClick={handleDismissSuggestion}
              className="w-4 h-4 flex items-center justify-center rounded text-text-dim hover:text-text-secondary hover:bg-white/[0.06] transition-all"
            >
              <Icon icon="fa-solid fa-xmark text-[7px]" />
            </button>
          </div>
        )}

        {showEdit && (
          <div className={`flex-1 min-w-0 flex flex-col ${viewMode === 'split' ? 'border-r border-white/[0.04]' : ''}`}>
            <div className="flex-1 min-h-0" data-color-mode="dark">
              <Suspense fallback={<div className="flex-1" />}>
                <MDEditor
                  ref={mdEditorRef}
                  value={mdContent}
                  onChange={val => handleMdChange(val ?? '')}
                  visiableDragbar={false}
                  textareaProps={{
                    placeholder: t('note.markdownPlaceholder'),
                    onKeyDown: (e: React.KeyboardEvent<HTMLTextAreaElement>) => handleMdEditorKeyDown(e, mdContent),
                    onPaste: handleEditorPaste,
                    onDrop: handleEditorDrop,
                    onDragOver: (e: React.DragEvent) => e.preventDefault(),
                  }}
                />
              </Suspense>
            </div>
          </div>
        )}
        {showPreview && (
          <div className="flex-1 min-w-0 overflow-y-auto px-3 py-1.5">
            <Suspense fallback={<div className="text-text-dim text-xs">...</div>}>
              <MdPreview
                content={mdContent}
                className="text-xs text-white/85 [&_*]:!text-inherit"
              />
            </Suspense>
          </div>
        )}
      </div>

      {/* Related memories */}
      {debouncedText && <RelatedMemories text={debouncedText} />}
    </div>
  )
}

export default NoteEditor
