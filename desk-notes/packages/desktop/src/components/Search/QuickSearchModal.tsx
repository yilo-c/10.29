import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import Icon from '../Common/Icon'

import { createPortal } from 'react-dom'
import Fuse from 'fuse.js'
import { useStore } from '../../store/useStore'
import { stripHtml, uid } from '../../utils/helpers'
import { extractContext } from '../../utils/searchContext'
import { getAIProvider, getWsClient } from '../../utils/ai'
import { getLocalAIConfig } from '../../utils/aiConfig'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { useTranslation } from '../../i18n'
import { swallowError } from '../../utils/swallowError'
import { buildQuickCreateNotePrompt } from './QuickSearchPrompts'

interface FlatItem {
  id: string
  noteId: string
  type: 'note' | 'todo'
  todoId?: string
  title: string
  preview: string
  tags: string[]
  done?: boolean
  createdAt: number
  matchContext?: string
}

interface NavCmd {
  key: string
  label: string
  icon: string
}

// D1：动作命令（直接执行后端 RPC，B2 命令面板规划一部分）
interface ActionCmdDef {
  key: string
  labelKey: string
  icon: string
  method: string
  params?: Record<string, unknown>
}

const ACTION_COMMAND_DEFS: ActionCmdDef[] = [
  { key: 'sync-now', labelKey: 'search.actionSyncNow', icon: 'fa-solid fa-rotate', method: 'sync.sync_now' },
  { key: 'mark-all-read', labelKey: 'search.actionMarkAllRead', icon: 'fa-solid fa-envelope-open', method: 'notification.mark_all_read' },
]

// Visual icons keyed by module — labels are resolved through i18n inside the component.
const NAV_COMMAND_DEFS: { key: string; labelKey: string; icon: string }[] = [
  { key: 'chat',      labelKey: 'search.navChat',      icon: 'fa-solid fa-comment-dots' },
  { key: 'dashboard', labelKey: 'search.navDashboard', icon: 'fa-solid fa-table-cells-large' },
  { key: 'notes',     labelKey: 'search.navNotes',     icon: 'fa-regular fa-note-sticky' },
  { key: 'todo',      labelKey: 'search.navTodo',      icon: 'fa-regular fa-square-check' },
  { key: 'calendar',  labelKey: 'search.navCalendar',  icon: 'fa-regular fa-calendar' },
  { key: 'info',      labelKey: 'search.navInfo',      icon: 'fa-regular fa-newspaper' },
  { key: 'agent',     labelKey: 'search.navAgent',     icon: 'fa-solid fa-robot' },
  { key: 'settings',  labelKey: 'search.navSettings',  icon: 'fa-solid fa-gear' },
]

interface Props {
  isOpen: boolean
  onClose: () => void
}

const QuickSearchModal: React.FC<Props> = ({ isOpen, onClose }) => {
  const floatingNotes = useStore(s => s.floatingNotes)
  const focusNote = useStore(s => s.focusNote)
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const addTodo = useStore(s => s.addTodo)
  const nextZ = useStore(s => s.nextZ)
  const aiEnabled = useStore(s => s.aiEnabled)
  const setSearchHighlight = useStore(s => s.setSearchHighlight)
  const activeFolderId = useStore(s => s.activeFolderId)
  const setActiveModule = useStore(s => s.setActiveModule)
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [selectedIdx, setSelectedIdx] = useState(0)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiCreated, setAiCreated] = useState(false)
  const [askResult, setAskResult] = useState('')
  const [askSources, setAskSources] = useState<string[]>([])
  const [memoryResults, setMemoryResults] = useState<Array<{
    entry_id: string
    title: string
    snippet: string
    source_label: string
    updated_at?: string
  }>>([])
  const [memoryLoading, setMemoryLoading] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useFocusTrap(dialogRef, isOpen)

  const isCreateMode = aiEnabled && query.startsWith('>')
  const isAskMode = aiEnabled && query.startsWith('?')
  // B2：显式命令模式 —— `/` 开头只展示「模块跳转 + 动作命令」，走纯键盘匹配不触发全文搜索
  const isCommandMode = query.startsWith('/')

  // Reset state when opening
  useEffect(() => {
    if (isOpen) {
      setSearchHighlight(null)
      setQuery('')
      setSelectedIdx(0)
      setAiLoading(false)
      setAiCreated(false)
      setAskResult('')
      setAskSources([])
      const timer = setTimeout(() => inputRef.current?.focus(), 50)
      return () => clearTimeout(timer)
    }
  }, [isOpen])

  // Clear close timer on unmount
  useEffect(() => {
    return () => { if (closeTimerRef.current) clearTimeout(closeTimerRef.current) }
  }, [])

  // Backend memory search — debounced, only when backend connected
  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed || isCreateMode || isAskMode) {
      setMemoryResults([])
      return
    }

    const client = getWsClient()
    if (client.status !== 'connected') return

    setMemoryLoading(true)
    const timer = setTimeout(async () => {
      try {
        const results = await client.call('memory.search', { query: trimmed, limit: 5 })
        if (Array.isArray(results)) {
          // U3（S3-5）：后端 memory.search 返回 _format_entry 形状
          // （entry_id/title/snippet/source_label/updated_at），旧映射读的
          // text/score/tags/timestamp 四字段全不存在 → 记忆区恒渲染空白行。
          setMemoryResults(
            results
              .map((r: Record<string, unknown>) => ({
                entry_id: String(r.entry_id ?? r.source_id ?? ''),
                title: typeof r.title === 'string' ? r.title : '',
                snippet: typeof r.snippet === 'string' ? r.snippet : String(r.content ?? ''),
                source_label: typeof r.source_label === 'string' ? r.source_label : String(r.source_type ?? ''),
                updated_at: typeof r.updated_at === 'string' ? r.updated_at : undefined,
              }))
              .filter((m) => m.entry_id && (m.snippet || m.title)),
          )
        }
      } catch {
        // Silently fail — memory search is best-effort
        setMemoryResults([])
      } finally {
        setMemoryLoading(false)
      }
    }, 300)
    return () => clearTimeout(timer)
  }, [query, isCreateMode, isAskMode])

  // Unified knowledge search — searches memory, knowledge graph, wordbook
  const [unifiedResults, setUnifiedResults] = useState<Array<{ _label?: string; _source?: string; title?: string; snippet?: string }>>([])
  const [unifiedLoading, setUnifiedLoading] = useState(false)
  useEffect(() => {
    const trimmed = query.trim()
    if (!trimmed || trimmed.length < 2) { setUnifiedResults([]); return }

    const client = getWsClient()
    if (client.status !== 'connected') return

    setUnifiedLoading(true)
    const timer = setTimeout(async () => {
      try {
        const resp = await client.call<{ intent?: string; results?: any[]; sources?: Record<string, any[]> }>('knowledge.unified_query', { text: trimmed, limit: 10 })
        if (resp?.results?.length) setUnifiedResults(resp.results)
        else setUnifiedResults([])
      } catch { setUnifiedResults([]) }
      finally { setUnifiedLoading(false) }
    }, 300)
    return () => clearTimeout(timer)
  }, [query, isCreateMode, isAskMode])
  const fuse = useMemo(() => {
    const items: FlatItem[] = []
    for (const n of floatingNotes) {
      const plainContent = stripHtml(n.content || '')
      items.push({
        id: `note-${n.id}`,
        noteId: n.id,
        type: 'note',
        title: n.title,
        preview: plainContent.slice(0, 120),
        tags: n.tags || [],
        createdAt: n.createdAt || 0,
      })
      for (const td of n.todos || []) {
        if (!td?.text?.trim()) continue
        items.push({
          id: `todo-${td.id}`,
          noteId: n.id,
          todoId: td.id,
          type: 'todo',
          title: td.text,
          preview: t('search.belongsToNote', { title: n.title }),
          tags: [],
          done: td.done,
          createdAt: n.createdAt || 0,
        })
      }
    }
    return new Fuse(items, {
      keys: [
        { name: 'title', weight: 0.4 },
        { name: 'preview', weight: 0.3 },
        { name: 'tags', weight: 0.2 },
      ],
      threshold: 0.4,
      distance: 100,
      minMatchCharLength: 1,
      includeScore: true,
    })
  }, [floatingNotes])

  // Compute results
  const results = useMemo(() => {
    const trimmed = query.trim()
    if (!trimmed || isCommandMode) return null

    const raw = fuse.search(trimmed)
    const notes: FlatItem[] = []
    const todos: FlatItem[] = []
    for (const r of raw) {
      if (r.item.type === 'note') {
        // Clone to avoid mutating Fuse index
        const item = { ...r.item }
        const noteData = floatingNotes.find(n => n.id === item.noteId)
        if (noteData) {
          const plainContent = stripHtml(noteData.content || '')
          if (plainContent.toLowerCase().includes(trimmed.toLowerCase())) {
            item.matchContext = extractContext(plainContent, trimmed, 25)
          }
        }
        notes.push(item)
      } else {
        todos.push(r.item)
      }
    }
    return { notes: notes.slice(0, 10), todos: todos.slice(0, 10) }
  }, [query, fuse, floatingNotes, isCommandMode])

  // Recent notes (when query is empty)
  const recentNotes = useMemo(() => {
    return [...floatingNotes]
      .filter(n => !n.archived)
      .sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
      .slice(0, 5)
  }, [floatingNotes])

  // Navigation commands (jump to module) — resolved via t()
  const navCommands = useMemo(() => {
    return NAV_COMMAND_DEFS.map(def => ({ key: def.key, label: t(def.labelKey), icon: def.icon }))
  }, [t])

  const navResults = useMemo(() => {
    const trimmed = query.trim().toLowerCase()
    if (!trimmed) return []
    // 命令模式（/开头）：剥掉前导 / 再匹配；空 key（仅输入 /）展示全部命令
    const key = isCommandMode ? trimmed.slice(1) : trimmed
    if (!key && !isCommandMode) return []
    return key ? navCommands.filter(cmd => cmd.label.toLowerCase().includes(key)) : navCommands
  }, [query, navCommands, isCommandMode])

  const navCount = navResults.length

  // D1: action commands (execute backend RPC)
  const actionCommands = useMemo(() => {
    return ACTION_COMMAND_DEFS.map(def => ({ ...def, label: t(def.labelKey) }))
  }, [t])

  const actionResults = useMemo(() => {
    const trimmed = query.trim().toLowerCase()
    if (!trimmed) return []
    const key = isCommandMode ? trimmed.slice(1) : trimmed
    if (!key && !isCommandMode) return []
    return key ? actionCommands.filter(cmd => cmd.label.toLowerCase().includes(key)) : actionCommands
  }, [query, actionCommands, isCommandMode])

  const actionCount = actionResults.length

  // All result items flattened for keyboard nav
  const allItems = useMemo((): (FlatItem | (NavCmd & { id: string; type: 'nav' }) | (ActionCmdDef & { label: string; id: string; type: 'action' }))[] => {
    const items: (FlatItem | (NavCmd & { id: string; type: 'nav' }) | (ActionCmdDef & { label: string; id: string; type: 'action' }))[] = []
    for (const cmd of navResults) {
      items.push({ ...cmd, id: `nav-${cmd.key}`, type: 'nav' })
    }
    for (const cmd of actionResults) {
      items.push({ ...cmd, id: `action-${cmd.key}`, type: 'action' })
    }
    if (results) {
      for (const n of results.notes) items.push(n)
      for (const t of results.todos) items.push(t)
    }
    return items
  }, [navResults, actionResults, results])

  // Clamp selected index on results change
  useEffect(() => {
    setSelectedIdx(0)
  }, [query])

  const activateItem = useCallback((item: FlatItem | (NavCmd & { id: string; type: string }) | (ActionCmdDef & { label: string; id: string; type: string })) => {
    if (item.type === 'nav') {
      setActiveModule(item.key as 'dashboard' | 'chat' | 'notes' | 'todo' | 'calendar' | 'info' | 'agent' | 'settings')
      onClose()
      return
    }
    if (item.type === 'action') {
      // D1：执行后端 RPC 动作后关闭面板
      const def = item as ActionCmdDef
      ;(async () => {
        try {
          const client = getWsClient()
          if (client.status === 'connected') {
            await client.call(def.method, def.params ?? {})
          }
        } catch (e) {
          swallowError('QuickSearchModal:action', e)
        }
      })()
      onClose()
      return
    }
    const trimmed = query.trim()
    if (trimmed) {
      setSearchHighlight({ keyword: trimmed, noteId: (item as FlatItem).noteId })
    }
    focusNote((item as FlatItem).noteId)
    onClose()
  }, [focusNote, onClose, setSearchHighlight, query, setActiveModule])

  const handleAICreate = useCallback(async () => {
    const text = query.slice(1).trim()
    if (!text) return
    setAiLoading(true)
    try {
      let intent: { type: string; title?: string; content?: string; tags?: string[]; dueDate?: number } | null = null

      // Try backend RPC when connected
      try {
        const client = getWsClient()
        if (client.status === 'connected') {
          const result: any = await client.call('ai.parse_intent', { text })
          if (result?.type) {
            intent = result as typeof intent
          }
        }
      } catch (e) {
        // RPC failed, fall through to local AI
        swallowError('QuickSearchModal:277', e)
      }

      if (!intent) {
        // Fallback: default local AI config
        intent = await getAIProvider(getLocalAIConfig()).parseIntent(text)
      }

      if (intent.type === 'create_todo') {
        addTodo(intent.title || text, undefined)
        setAiCreated(true)
        setAiLoading(false)
        closeTimerRef.current = setTimeout(() => onClose(), 800)
        return
      }

      // Generate rich content if parsed intent has minimal content
      let content = intent.content || ''
      let title = intent.title || text.slice(0, 40)
      if (!content || content === intent.title || content.length < 20) {
        try {
          const prompt = buildQuickCreateNotePrompt(text)
          const genContent = await getAIProvider(getLocalAIConfig()).processText('continue', prompt, () => {})
          if (genContent && genContent.length > content.length) {
            content = genContent
            // Extract title from first line if generated content has one
            const firstLine = genContent.split('\n')[0].replace(/^#\s*/, '').trim()
            if (firstLine && firstLine.length < 60) title = firstLine
          }
        } catch (e) { /* fallback to parsed content */ swallowError('QuickSearchModal:307', e) }
      }

      addFloatingNote({
        id: uid(),
        type: 'text',
        title,
        content,
        tags: intent.tags || [],
        folderId: activeFolderId || undefined,
        x: 100 + Math.random() * 80,
        y: 100 + Math.random() * 80,
        width: 260,
        height: 200,
        zIndex: nextZ,
        floated: true,
        createdAt: Date.now(),
      })
      setAiCreated(true)
      setAiLoading(false)
      closeTimerRef.current = setTimeout(() => onClose(), 800)
    } catch {
      setAiLoading(false)
    }
  }, [query, addFloatingNote, addTodo, nextZ, onClose, activeFolderId])

  const handleAsk = useCallback(async () => {
    const question = query.slice(1).trim()
    if (!question) return
    setAiLoading(true)
    setAskResult('')
    setAskSources([])

    // Build context from fuse search results
    const raw = fuse.search(question)
    const contextNotes = raw.slice(0, 5).map(r => {
      const n = floatingNotes.find(fn => fn.id === r.item.noteId)
      return n ? { id: n.id, title: n.title, content: stripHtml(n.content || '').slice(0, 1500) } : null
    }).filter(Boolean) as { id: string; title: string; content: string }[]

    const contexts = contextNotes.map(n => `[${n.title}]\n${n.content}`)
    setAskSources(contextNotes.map(n => n.title))

    try {
      const answer = await getAIProvider(getLocalAIConfig()).ask(question, contexts)
      setAskResult(answer)
    } catch {
      setAskResult(t('ai.askFailed'))
    } finally {
      setAiLoading(false)
    }
  }, [query, fuse, floatingNotes])

  // U3（S3-5）：记忆可删——软删（后端 revoke 语义，审计留痕可恢复），
  // 删除后本地列表即时移除该行，无需重跑搜索。
  const handleDeleteMemory = useCallback(async (entryId: string) => {
    if (!entryId) return
    if (!window.confirm(t('search.deleteMemoryConfirm'))) return
    try {
      const client = getWsClient()
      if (!client) return
      const resp = await client.call<{ success?: boolean; error?: string }>('memory.delete', {
        entry_id: entryId,
        reason: 'quick_search_delete',
      })
      if (resp?.success) {
        setMemoryResults((prev) => prev.filter((m) => m.entry_id !== entryId))
      }
    } catch {
      // 删除失败静默——行保留，用户可重试（本组件无 toast 通道）
    }
  }, [t])

  // Arrow key + enter navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (isCreateMode || isAskMode) {
      if (e.key === 'Enter') {
        e.preventDefault()
        if (isCreateMode && !aiLoading && !aiCreated) handleAICreate()
        if (isAskMode && !aiLoading) handleAsk()
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
      return
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelectedIdx(i => Math.min(i + 1, Math.max(allItems.length - 1, 0)))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelectedIdx(i => Math.max(i - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (allItems.length > 0 && selectedIdx < allItems.length) {
        activateItem(allItems[selectedIdx])
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    }
  }

  // Scroll active item into view
  useEffect(() => {
    if (!listRef.current) return
    const el = listRef.current.children[selectedIdx] as HTMLElement
    if (el) el.scrollIntoView({ block: 'nearest' })
  }, [selectedIdx])

  if (!isOpen) return null

  const hasResults = results && (results.notes.length > 0 || results.todos.length > 0)
  const isEmpty = floatingNotes.length === 0

  return createPortal(
    <div
      className="fixed inset-0 z-[99999] flex items-start justify-center pt-[15vh]"
      onMouseDown={onClose}
      style={{ background: 'rgba(0,0,0,0.45)' }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('search.placeholder')}
        tabIndex={-1}
        className="w-full max-w-[520px] border rounded-xl shadow-2xl overflow-hidden backdrop-blur-xl"
        onMouseDown={e => e.stopPropagation()}
        style={{
          background: 'var(--panel-bg-solid, rgba(22,22,32,0.95))',
          borderColor: 'var(--panel-border, rgba(255,255,255,0.08))',
        }}
      >
        {/* Search input */}
        <div className="flex items-center gap-2 px-4 py-3 border-b" style={{ borderColor: 'var(--panel-border, rgba(255,255,255,0.06))' }}>
          <Icon icon="fa-solid fa-magnifying-glass text-xs" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))' }} />
          <input
            ref={inputRef}
            autoFocus
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('search.placeholder')}
            className="flex-1 bg-transparent text-sm outline-none placeholder-white/30"
            style={{ color: 'var(--text-primary, rgba(255,255,255,0.92))' }}
          />
          <kbd className="text-2xs px-1.5 py-0.5 rounded border" style={{
            color: 'var(--text-muted, rgba(255,255,255,0.3))',
            borderColor: 'var(--panel-border, rgba(255,255,255,0.08))',
            background: 'var(--hover-bg, rgba(255,255,255,0.04))',
          }}>
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div ref={listRef} className="max-h-[50vh] overflow-y-auto py-1">
          {/* AI Create Mode */}
          {isCreateMode && (
            <div className="flex flex-col items-center py-8 gap-3 px-4">
              <Icon icon="fa-solid fa-wand-magic-sparkles text-lg" style={{ color: 'var(--fluent-blue, var(--brand))' }} />
              {aiLoading ? (
                <>
                  <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{t('search.aiCreating')}</span>
                  <div className="w-5 h-5 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
                </>
              ) : aiCreated ? (
                <>
                  <Icon icon="fa-solid fa-circle-check text-sm text-green-400" />
                  <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{t('search.aiCreated')}</span>
                </>
              ) : (
                <span className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
                  <kbd className="px-1 py-0.5 rounded border text-2xs" style={{ borderColor: 'var(--panel-border)' }}>Enter</kbd> {t('search.aiCreateHint')}
                </span>
              )}
            </div>
          )}

          {/* AI Ask Mode */}
          {isAskMode && (
            <div className="flex flex-col gap-3 px-4 py-4">
              {!aiLoading && !askResult && !askSources.length && (
                <div className="flex flex-col items-center gap-3 py-6">
                  <Icon icon="fa-solid fa-circle-question text-lg" style={{ color: 'var(--fluent-blue, var(--brand))' }} />
                  <span className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
                    <kbd className="px-1 py-0.5 rounded border text-2xs" style={{ borderColor: 'var(--panel-border)' }}>Enter</kbd> {t('search.askHint')}
                  </span>
                </div>
              )}

              {aiLoading && (
                <div className="flex flex-col items-center gap-3 py-6">
                  <div className="w-5 h-5 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
                  <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{t('search.askLoading')}</span>
                </div>
              )}

              {askResult && (
                <div className="flex flex-col gap-3">
                  <div className="text-xs leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text-primary)' }}>
                    {askResult}
                  </div>
                  {askSources.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('search.sources')}</span>
                      {askSources.map((s, i) => (
                        <span key={i} className="text-2xs px-1.5 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-secondary)' }}>
                          {s}
                        </span>
                      ))}
                    </div>
                  )}
                  <button
                    onClick={() => {
                      const sourcesText = askSources.length > 0 ? '\n\n' + t('search.sources') + askSources.join(', ') : ''
                      addFloatingNote({
                        id: uid(),
                        type: 'text',
                        title: query.slice(1).trim().slice(0, 60),
                        content: askResult + sourcesText,
                        x: 100 + Math.random() * 80,
                        y: 100 + Math.random() * 80,
                        width: 260,
                        height: 200,
                        zIndex: nextZ,
                        floated: true,
                        createdAt: Date.now(),
                      })
                      onClose()
                    }}
                    className="self-start flex items-center gap-1.5 text-2xs px-2 py-1 rounded-lg transition-colors hover:bg-white/[0.06]"
                    style={{ color: 'var(--fluent-blue, var(--brand))' }}
                  >
                    <Icon icon="fa-regular fa-note-sticky text-2xs" />
                    {t('common.save')}
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Normal search results (hidden in AI modes) */}
          {!isCreateMode && !isAskMode && (
            <>
          {isEmpty && (
            <div className="flex flex-col items-center py-8 gap-2">
              <Icon icon="fa-regular fa-note-sticky text-lg" style={{ color: 'var(--text-muted)' }} />
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('search.empty')}</span>
            </div>
          )}

          {!isEmpty && !results && !isCommandMode && (
            /* Recent notes */
            <div>
              <div className="px-4 py-1.5 text-xs font-medium tracking-wide" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))' }}>
                {t('search.recent')}
              </div>
              {recentNotes.map(n => (
                <button
                  key={n.id}
                  onClick={() => { focusNote(n.id); onClose() }}
                  className="w-full flex items-center gap-3 px-4 py-2 text-left transition-colors hover:bg-white/[0.04]"
                >
                  <Icon icon="fa-regular fa-note-sticky text-xs flex-shrink-0" style={{ color: 'var(--text-muted)' }} />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>{n.title}</div>
                    <div className="text-xs truncate mt-0.5" style={{ color: 'var(--text-muted)' }}>
                      {stripHtml(n.content || '').slice(0, 60) || (n.todos || []).map(t => t.text).join(', ').slice(0, 60)}
                    </div>
                  </div>
                  {n.tags && n.tags.length > 0 && (
                    <div className="flex gap-1 flex-shrink-0">
                      {n.tags.slice(0, 2).map(t => (
                        <span key={t} className="text-2xs px-1.5 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}>{t}</span>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
          )}

          {!isEmpty && results && !hasResults && (
            <div className="flex flex-col items-center py-8 gap-2">
              <Icon icon="fa-solid fa-magnifying-glass text-lg" style={{ color: 'var(--text-muted)' }} />
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {t('search.noMatchFor', { query: query.trim() })}
              </span>
            </div>
          )}

          {/* B2：显式命令模式（/xxx）—— 只展示模块跳转 + 动作命令 */}
          {isCommandMode && (
            <>
              {navCount > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-xs font-medium tracking-wide" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))' }}>
                    {t('search.sectionNav')}
                  </div>
                  {navResults.map((cmd, i) => (
                    <button
                      key={cmd.key}
                      onClick={() => { setActiveModule(cmd.key as 'dashboard' | 'chat' | 'notes' | 'todo' | 'calendar' | 'info' | 'agent' | 'settings'); onClose() }}
                      className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                        i === selectedIdx ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]'
                      }`}
                    >
                      <Icon icon={`${cmd.icon} text-xs flex-shrink-0`} style={{ color: 'var(--fluent-blue, var(--brand))' }} />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>
                          {highlightMatch(cmd.label, query.slice(1))}
                        </div>
                        <div className="text-xs truncate mt-0.5" style={{ color: 'var(--text-muted)' }}>
                          {t('search.switchTo', { label: cmd.label })}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
              {actionCount > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-xs font-medium tracking-wide" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))' }}>
                    {t('search.sectionActions')}
                  </div>
                  {actionResults.map((cmd, i) => (
                    <button
                      key={cmd.key}
                      onClick={() => activateItem({ ...cmd, id: `action-${cmd.key}`, type: 'action' })}
                      className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                        navCount + i === selectedIdx ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]'
                      }`}
                    >
                      <Icon icon={`${cmd.icon} text-xs flex-shrink-0`} style={{ color: 'var(--fluent-green, var(--success))' }} />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>
                          {highlightMatch(cmd.label, query.slice(1))}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
              {navCount === 0 && actionCount === 0 && (
                <div className="flex flex-col items-center py-8 gap-2">
                  <Icon icon="fa-solid fa-terminal text-lg" style={{ color: 'var(--text-muted)' }} />
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('search.noCommand')}</span>
                </div>
              )}
            </>
          )}

          {results && hasResults && (
            <>
              {navCount > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-xs font-medium tracking-wide" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))' }}>
                    {t('search.sectionNav')}
                  </div>
                  {navResults.map((cmd, i) => (
                    <button
                      key={cmd.key}
                      onClick={() => { setActiveModule(cmd.key as 'dashboard' | 'chat' | 'notes' | 'todo' | 'calendar' | 'info' | 'agent' | 'settings'); onClose() }}
                      className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                        i === selectedIdx ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]'
                      }`}
                    >
                      <Icon icon={`${cmd.icon} text-xs flex-shrink-0`} style={{ color: 'var(--fluent-blue, var(--brand))' }} />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>
                          {highlightMatch(cmd.label, query)}
                        </div>
                        <div className="text-xs truncate mt-0.5" style={{ color: 'var(--text-muted)' }}>
                          {t('search.switchTo', { label: cmd.label })}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
              {actionCount > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-xs font-medium tracking-wide" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))' }}>
                    {t('search.sectionActions')}
                  </div>
                  {actionResults.map((cmd, i) => (
                    <button
                      key={cmd.key}
                      onClick={() => activateItem({ ...cmd, id: `action-${cmd.key}`, type: 'action' })}
                      className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                        navCount + i === selectedIdx ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]'
                      }`}
                    >
                      <Icon icon={`${cmd.icon} text-xs flex-shrink-0`} style={{ color: 'var(--fluent-green, var(--success))' }} />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>
                          {highlightMatch(cmd.label, query)}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              )}
              {results.notes.length > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-xs font-medium tracking-wide" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))' }}>
                    {t('search.sectionNotes', { count: results.notes.length })}
                  </div>
                  {results.notes.map((item, i) => (
                    <button
                      key={item.id}
                      onClick={() => activateItem(item)}
                      className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                        i === selectedIdx ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]'
                      }`}
                    >
                      <Icon icon="fa-regular fa-note-sticky text-xs flex-shrink-0" style={{ color: 'var(--text-muted)' }} />
                      <div className="flex-1 min-w-0">
                        <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>{highlightMatch(item.title, query)}</div>
                        {item.matchContext && (
                          <div className="text-xs truncate mt-0.5 text-amber-400/60">
                            <Icon icon="fa-solid fa-location-arrow mr-1 text-[8px]" />
                            {item.matchContext}
                          </div>
                        )}
                        {item.preview && (
                          <div className="text-xs truncate mt-0.5" style={{ color: 'var(--text-muted)' }}>{item.preview.slice(0, 80)}</div>
                        )}
                      </div>
                      {item.tags.length > 0 && (
                        <div className="flex gap-1 flex-shrink-0">
                          {item.tags.slice(0, 2).map(t => (
                            <span key={t} className="text-2xs px-1.5 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}>{t}</span>
                          ))}
                        </div>
                      )}
                    </button>
                  ))}
                </div>
              )}
              {results.todos.length > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-xs font-medium tracking-wide border-t" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))', borderColor: 'var(--panel-border, rgba(255,255,255,0.04))' }}>
                    {t('search.sectionTodos', { count: results.todos.length })}
                  </div>
                  {results.todos.map((item, i) => {
                    const listIdx = navCount + results.notes.length + i
                    return (
                      <button
                        key={item.id}
                        onClick={() => activateItem(item)}
                        className={`w-full flex items-center gap-3 px-4 py-2 text-left transition-colors ${
                          listIdx === selectedIdx ? 'bg-white/[0.08]' : 'hover:bg-white/[0.04]'
                        }`}
                      >
                        <Icon icon={`text-xs flex-shrink-0 ${item.done ? 'fa-regular fa-circle-check' : 'fa-regular fa-circle'}`}
                          style={{ color: item.done ? 'var(--fluent-blue, var(--brand))' : 'var(--text-muted)' }} />
                        <div className="flex-1 min-w-0">
                          <div className={`text-xs truncate ${item.done ? 'line-through' : ''}`}
                            style={{ color: item.done ? 'var(--text-muted)' : 'var(--text-primary)' }}>
                            {highlightMatch(item.title, query)}
                          </div>
                          <div className="text-xs truncate mt-0.5" style={{ color: 'var(--text-dim, rgba(255,255,255,0.25))' }}>
                            {item.preview}
                          </div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}

              {/* Backend memory search results */}
              {memoryResults.length > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-xs font-medium tracking-wide border-t flex items-center gap-2" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))', borderColor: 'var(--panel-border, rgba(255,255,255,0.04))' }}>
                    <Icon icon="fa-solid fa-brain text-[8px]" />
                    {t('search.sectionMemory', { count: memoryResults.length })}
                  </div>
                  {memoryResults.map((item, i) => (
                    <div
                      key={`mem-${item.entry_id || i}`}
                      data-testid={`memory-search-row-${item.entry_id}`}
                      className="w-full flex items-start gap-3 px-4 py-2 text-left"
                    >
                      <Icon icon="fa-solid fa-brain text-xs flex-shrink-0 mt-0.5" style={{ color: 'var(--text-muted)' }} />
                      <div className="flex-1 min-w-0">
                        {item.title && (
                          <div className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                            {item.title}
                          </div>
                        )}
                        <div className="text-xs leading-relaxed line-clamp-3" style={{ color: 'var(--text-secondary)' }}>
                          {item.snippet}
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                          {item.source_label && (
                            <span className="text-2xs px-1.5 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}>
                              {item.source_label}
                            </span>
                          )}
                          {item.updated_at && (
                            <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{item.updated_at.slice(0, 10)}</span>
                          )}
                        </div>
                      </div>
                      <button
                        type="button"
                        className="text-2xs px-1.5 py-0.5 rounded shrink-0"
                        style={{ color: 'var(--text-muted)' }}
                        title={t('search.deleteMemoryTitle')}
                        aria-label={t('search.deleteMemoryAria', { title: item.title || item.entry_id })}
                        onClick={() => void handleDeleteMemory(item.entry_id)}
                      >
                        {t('search.deleteMemory')}
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {memoryLoading && (
                <div className="flex items-center justify-center gap-2 py-3">
                  <div className="w-3 h-3 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('search.searchingMemory')}</span>
                </div>
              )}

              {/* Unified knowledge results */}
              {unifiedResults.length > 0 && (
                <div>
                  <div className="px-4 py-1.5 text-xs font-medium tracking-wide border-t flex items-center gap-2" style={{ color: 'var(--text-muted, rgba(255,255,255,0.35))', borderColor: 'var(--panel-border, rgba(255,255,255,0.04))' }}>
                    <Icon icon="fa-solid fa-diagram-project text-[8px]" />
                    {t('search.sectionKnowledge', { count: unifiedResults.length })}
                  </div>
                  {unifiedResults.map((item, i) => (
                    <div key={`uni-${i}`} className="w-full flex items-start gap-3 px-4 py-2 text-left">
                      <span className="text-xs flex-shrink-0 mt-0.5">{item._label || '📄'}</span>
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>{item.title || item.snippet?.slice(0, 80) || ''}</div>
                        {item.snippet && <div className="text-xs leading-relaxed line-clamp-2 mt-0.5" style={{ color: 'var(--text-tertiary)' }}>{item.snippet.slice(0, 200)}</div>}
                        <div className="text-2xs mt-0.5" style={{ color: 'var(--text-dim)' }}>{item._source}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {unifiedLoading && (
                <div className="flex items-center justify-center gap-2 py-3">
                  <div className="w-3 h-3 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
                  <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('search.searchingKnowledge')}</span>
                </div>
              )}
            </>
          )}
            </>
          )}
        </div>

        {/* Footer hint */}
        <div className="px-4 py-1.5 border-t flex items-center gap-3 flex-wrap" style={{ borderColor: 'var(--panel-border, rgba(255,255,255,0.04))' }}>
          {aiEnabled && (
            <>
              <span className="text-2xs" style={{ color: 'var(--text-dim, rgba(255,255,255,0.25))' }}>
                <kbd className="px-1 py-0.5 rounded border text-[8px]" style={{ borderColor: 'var(--panel-border)' }}>/</kbd> {t('search.cmd')}
              </span>
              <span className="text-2xs" style={{ color: 'var(--text-dim, rgba(255,255,255,0.25))' }}>
                <kbd className="px-1 py-0.5 rounded border text-[8px]" style={{ borderColor: 'var(--panel-border)' }}>&gt;</kbd> {t('search.aiCreate')}
              </span>
              <span className="text-2xs" style={{ color: 'var(--text-dim, rgba(255,255,255,0.25))' }}>
                <kbd className="px-1 py-0.5 rounded border text-[8px]" style={{ borderColor: 'var(--panel-border)' }}>?</kbd> {t('search.aiAsk')}
              </span>
            </>
          )}
          <span className="text-2xs" style={{ color: 'var(--text-dim, rgba(255,255,255,0.25))' }}>
            <kbd className="px-1 py-0.5 rounded border text-[8px]" style={{ borderColor: 'var(--panel-border)' }}>↑↓</kbd> {t('search.nav')}
          </span>
          <span className="text-2xs" style={{ color: 'var(--text-dim, rgba(255,255,255,0.25))' }}>
            <kbd className="px-1 py-0.5 rounded border text-[8px]" style={{ borderColor: 'var(--panel-border)' }}>Enter</kbd> {t('search.open')}
          </span>
          <span className="text-2xs" style={{ color: 'var(--text-dim, rgba(255,255,255,0.25))' }}>
            <kbd className="px-1 py-0.5 rounded border text-[8px]" style={{ borderColor: 'var(--panel-border)' }}>Esc</kbd> {t('search.close')}
          </span>
        </div>
      </div>
    </div>,
    document.body
  )
}

/** Wrap matching portions of text in a highlight span */
function highlightMatch(text: string, query: string): React.ReactNode {
  if (!query.trim()) return text
  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'))
  if (parts.length === 1) return text
  return parts.map((part, i) =>
    part.toLowerCase() === query.toLowerCase()
      ? <span key={i} style={{ color: 'var(--fluent-blue, var(--brand))' }}>{part}</span>
      : part
  )
}

export default QuickSearchModal
