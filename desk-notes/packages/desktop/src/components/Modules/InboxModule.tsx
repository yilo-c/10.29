import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import Icon from '../Common/Icon'

import { getWsClient } from '../../utils/ai'
import { useBackendEvents } from '../../FloatingBubble/useBackendEvents'
import { swallowError } from '../../utils/swallowError'
import { errorStore } from '../../store/errorStore'
import { useTranslation } from '../../i18n'
import { trackUsage } from '../../utils/trackUsage'
import ApprovalQueuePanel from './ApprovalQueuePanel'

interface InboxEntry {
  id: string
  content: string
  title?: string
  source?: string
  status: string
  tags?: string[]
  metadata?: Record<string, unknown>
  classification?: { kind: string; confidence: number; reason: string; suggested_action: string }
  created_at?: string
}

const KIND_VISUAL: Record<string, { icon: string; color: string }> = {
  note: { icon: 'fa-regular fa-note-sticky', color: '#3b82f6' },
  task: { icon: 'fa-solid fa-check', color: 'var(--success)' },
  reminder: { icon: 'fa-regular fa-bell', color: '#eab308' },
  idea: { icon: 'fa-solid fa-lightbulb', color: '#a855f7' },
  question: { icon: 'fa-regular fa-circle-question', color: '#f97316' },
  reference: { icon: 'fa-solid fa-bookmark', color: '#06b6d4' },
  uncertain: { icon: 'fa-regular fa-clock', color: '#6b7280' },
}

const KIND_LABEL_KEY: Record<string, string> = {
  note: 'inbox.kindNote',
  task: 'inbox.kindTask',
  reminder: 'inbox.kindReminder',
  idea: 'inbox.kindIdea',
  question: 'inbox.kindQuestion',
  reference: 'inbox.kindReference',
  uncertain: 'inbox.kindUncertain',
}

/**
 * C-14①-5：前端切片分页页长——746 条全量渲染实测卡顿。
 * V1 修复（2026-09-16）：服务端 `inbox.list` 已支持 limit/offset（默认 50、上限 200），
 * fetchEntries 现改为按页拉满（每页 200），本常量仅控制**渲染**切片，不再代表数据上限。
 */
const PAGE_SIZE = 50

/** inbox.classify 成功响应形状（后端 _inbox_crud.py） */
interface ClassifyResult {
  success?: boolean
  error?: string
  kind?: string
  confidence?: number
  reason?: string
  suggested_action?: string
}

export default function InboxModule() {
  const { t } = useTranslation()
  const [entries, setEntries] = useState<InboxEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [input, setInput] = useState('')
  const [filterKind, setFilterKind] = useState<string | null>(null)
  const [showArchived, setShowArchived] = useState(false)
  const [sending, setSending] = useState(false)
  const [showUrlImport, setShowUrlImport] = useState(false)
  const [urlImport, setUrlImport] = useState('')
  const [urlImporting, setUrlImporting] = useState(false)
  const [offline, setOffline] = useState(false)
  /** C-14①-5：当前已展示条数（前端切片分页） */
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const fetchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { lastEvent } = useBackendEvents()

  // Cleanup timer on unmount
  useEffect(() => {
    return () => { if (fetchTimerRef.current) clearTimeout(fetchTimerRef.current) }
  }, [])

  const wsClient = getWsClient()

  const fetchEntries = useCallback(async () => {
    if (wsClient.status !== 'connected') {
      setOffline(true)
      setLoading(false)
      return
    }
    try {
      // V1 修复（2026-09-16）：服务端 inbox.list 已新增 limit/offset（默认 50、上限 200），
      // 且单帧超 4MiB 会被出站守卫拦成 -32007。本组件的分类计数(kindCounts)与类型筛选
      // 基于**全量** entries，故这里按页拉满（每页 200 = 服务端上限，避免单帧过大），
      // 而不是只取默认的 50 条（只取 50 条会让计数/筛选/加载更多全部错位）。
      const PAGE_FETCH = 200
      const MAX_PAGES = 10 // 保护：最多 2000 条，避免异常情况下无限拉取
      const all: InboxEntry[] = []
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const chunk = await wsClient.call<InboxEntry[]>('inbox.list', {
          include_archived: showArchived,
          limit: PAGE_FETCH,
          offset: page * PAGE_FETCH,
        })
        if (!Array.isArray(chunk) || chunk.length === 0) break
        all.push(...chunk)
        if (chunk.length < PAGE_FETCH) break // 已到末页
      }
      setEntries(all)
      setOffline(false)
    } catch (e) {
      swallowError('InboxModule:56', e)
      errorStore.error(`${t('inbox.title')} ${t('ux.loadFailedSuffix')}`)
    } finally {
      setLoading(false)
    }
  }, [wsClient, showArchived])

  useEffect(() => {
    fetchEntries()
  }, [fetchEntries])

  // 切换筛选/归档视图时分页从头开始
  useEffect(() => {
    setVisibleCount(PAGE_SIZE)
  }, [filterKind, showArchived])

  // Refresh when backend pushes new inbox items
  useEffect(() => {
    const ev = lastEvent('inbox.delivered')
    if (ev) fetchEntries()
  }, [lastEvent])

  const handleAdd = async (rawValue?: string) => {
    const text = (rawValue ?? input).trim()
    if (!text || sending) return
    setSending(true)
    try {
      await wsClient.call('inbox.add', { content: text })
      trackUsage('capture.inbox')
      setInput('')
      // Re-fetch after classification completes
      if (fetchTimerRef.current) clearTimeout(fetchTimerRef.current)
      fetchTimerRef.current = setTimeout(fetchEntries, 1500)
    } catch (e) {
      swallowError('InboxModule:83', e)
      errorStore.error(`${t('inbox.title')} ${t('ux.addFailedSuffix')}`)
    } finally {
      setSending(false)
    }
  }

  const handleUrlImport = async () => {
    const url = urlImport.trim()
    if (!url || urlImporting) return
    setUrlImporting(true)
    try {
      const result = await wsClient.call<{ title?: string; summary?: string }>('url_learning.learn', { url, timeout: 30 })
      const content = [result.title, result.summary].filter(Boolean).join(' — ')
      await wsClient.call('inbox.add', { content: content || url, source: 'url_import' })
      setUrlImport('')
      setShowUrlImport(false)
      if (fetchTimerRef.current) clearTimeout(fetchTimerRef.current)
      fetchTimerRef.current = setTimeout(fetchEntries, 1500)
    } catch (e) {
      swallowError('InboxModule:102', e)
      errorStore.error(`${t('inbox.importFromUrl')} ${t('ux.importFailedSuffix')}`)
    } finally {
      setUrlImporting(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleAdd()
    }
  }

  const handleArchive = async (id: string) => {
    try {
      await wsClient.call('inbox.update', { entry_id: id, status: 'archived' })
      setEntries(prev => prev.filter(e => e.id !== id))
    } catch (e) {
      swallowError('InboxModule:120', e)
      errorStore.error(`${t('inbox.archive')} ${t('ux.archiveFailedSuffix')}`)
    }
  }

  const handleDelete = async (id: string) => {
    try {
      await wsClient.call('inbox.update', { entry_id: id, status: 'dismissed' })
      setEntries(prev => prev.filter(e => e.id !== id))
    } catch (e) {
      swallowError('InboxModule:129', e)
      errorStore.error(`${t('inbox.delete')} ${t('ux.deleteFailedSuffix')}`)
    }
  }

  /**
   * C-14①-1 手动分类回写：inbox.classify 后端只把结果写进平铺 ai_kind 字段
   * （UI 读的是 metadata.classification → 直接刷新永远显示"分类中"）。
   * 这里把 classify 返回值合并进 metadata.classification 经 inbox.update 持久化，
   * 并乐观更新本地状态（不再全量 refetch，避免 746 条重拉）。
   */
  const handleClassify = async (entry: InboxEntry) => {
    try {
      const res = await wsClient.call<ClassifyResult>('inbox.classify', { entry_id: entry.id })
      if (res?.error || res?.success === false) throw new Error(res?.error || 'classify failed')
      const classification = {
        kind: res.kind || 'uncertain',
        confidence: res.confidence ?? 0,
        reason: res.reason || '',
        suggested_action: res.suggested_action || 'keep',
      }
      // inbox.update 的 metadata 是整体替换（repo entry.update）→ 必须带上旧 metadata 防抹（C-14①-2）
      const metadata = { ...(entry.metadata || {}), classification }
      await wsClient.call('inbox.update', { entry_id: entry.id, metadata })
      setEntries(prev => prev.map(e => (e.id === entry.id ? { ...e, metadata } : e)))
    } catch (e) {
      console.warn('[inbox] classify failed:', e)
    }
  }

  const filteredEntries = useMemo(() => {
    let items = entries.filter(e => e.status !== 'dismissed')
    if (!showArchived) items = items.filter(e => e.status !== 'archived')
    if (filterKind) {
      items = items.filter(e => {
        const kind = (e.metadata as Record<string, any>)?.classification?.kind || 'uncertain'
        return kind === filterKind
      })
    }
    return items
  }, [entries, showArchived, filterKind])

  /** C-14①-5：前端切片分页——只渲染前 visibleCount 条，"加载更多"递增 */
  const pagedEntries = useMemo(
    () => filteredEntries.slice(0, visibleCount),
    [filteredEntries, visibleCount],
  )
  const remainingCount = filteredEntries.length - pagedEntries.length

  const kindCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const e of entries) {
      const kind = (e.metadata as Record<string, any>)?.classification?.kind || 'uncertain'
      counts[kind] = (counts[kind] || 0) + 1
    }
    return counts
  }, [entries])

  const formatTime = (ts?: string) => {
    if (!ts) return ''
    const d = new Date(ts)
    const now = new Date()
    const isToday = d.toDateString() === now.toDateString()
    if (isToday) {
      return t('inbox.timeTodayFmt', {
        hh: d.getHours().toString().padStart(2, '0'),
        mm: d.getMinutes().toString().padStart(2, '0'),
      })
    }
    return t('inbox.timeMonthDayFmt', { month: d.getMonth() + 1, day: d.getDate() })
  }

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 h-10 border-b flex-shrink-0" style={{ borderColor: 'var(--separator)' }}>
        <div className="flex items-center gap-2">
          <Icon icon="fa-regular fa-inbox text-sm" style={{ color: 'var(--text-primary)' }} />
          <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{t('inbox.title')}</span>
          <span className="text-xs px-1.5 py-0.5 rounded-full" style={{ background: 'var(--hover-bg)', color: 'var(--text-dim)' }}>
            {entries.filter(e => e.status !== 'dismissed' && e.status !== 'archived').length}
          </span>
        </div>
        <label className="flex items-center gap-1.5 text-xs cursor-pointer" style={{ color: 'var(--text-muted)' }}>
          <input type="checkbox" checked={showArchived} onChange={e => setShowArchived(e.target.checked)}
            className="w-3 h-3 rounded" />
          {t('inbox.showArchived')}
        </label>
      </div>

      {/* M2.2 ②：审批页（待处理队列 + 已批准/拒绝历史） */}
      <ApprovalQueuePanel />

      <div className="flex-1 flex min-h-0 overflow-hidden">
        {/* Sidebar — kind filters */}
        <div className="w-36 flex-shrink-0 border-r p-2 space-y-1 overflow-y-auto" style={{ borderColor: 'var(--separator)' }}>
          <button
            onClick={() => setFilterKind(null)}
            className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs transition-all ${
              !filterKind ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-white/75 hover:bg-white/[0.04]'
            }`}
          >
            <Icon icon="fa-regular fa-inbox text-2xs" />
            {t('inbox.filterAll')}
            <span className="ml-auto text-[8px]" style={{ color: 'var(--text-dim)' }}>
              {entries.filter(e => e.status !== 'dismissed' && e.status !== 'archived').length}
            </span>
          </button>
          {Object.entries(KIND_VISUAL).map(([kind, cfg]) => {
            const count = kindCounts[kind] || 0
            if (count === 0) return null
            const labelKey = KIND_LABEL_KEY[kind] || 'inbox.kindUncertain'
            return (
              <button key={kind}
                onClick={() => setFilterKind(kind)}
                className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs transition-all ${
                  filterKind === kind ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:text-white/75 hover:bg-white/[0.04]'
                }`}
              >
                <Icon icon={cfg.icon} style={{ color: cfg.color, fontSize: 9 }} />
                {t(labelKey)}
                <span className="ml-auto text-[8px]" style={{ color: 'var(--text-dim)' }}>{count}</span>
              </button>
            )
          })}
        </div>

        {/* Main content */}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {/* Input area */}
          <div className="flex-shrink-0 px-3 pt-2 pb-1.5 space-y-1.5">
            <div className="flex items-end gap-2 rounded-xl border px-3 py-2" style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)' }}>
              {!showUrlImport && (
                <>
                  <textarea
                    ref={inputRef}
                    value={input}
                    onChange={e => setInput(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder={t('inbox.inputPlaceholder')}
                    rows={1}
                    className="flex-1 bg-transparent border-none outline-none resize-none text-sm leading-relaxed"
                    style={{ color: 'var(--text-primary)', maxHeight: 80 }}
                  />
                  <button onClick={() => setShowUrlImport(true)}
                    className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-text-dim hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
                    title={t('inbox.importFromUrl')}>
                    <Icon icon="fa-solid fa-link" />
                  </button>
                  <button onClick={() => handleAdd()} disabled={!input.trim() || sending}
                    className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all text-xs">
                    <Icon icon={`fa-solid ${sending ? 'fa-spinner animate-spin' : 'fa-arrow-up'}`} />
                  </button>
                </>
              )}
              {showUrlImport && (
                <>
                  <input
                    value={urlImport}
                    onChange={e => setUrlImport(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter') handleUrlImport() }}
                    placeholder={t('inbox.urlPlaceholder')}
                    className="flex-1 bg-transparent border-none outline-none text-sm"
                    style={{ color: 'var(--text-primary)' }}
                    autoFocus
                  />
                  <button onClick={handleUrlImport} disabled={!urlImport.trim() || urlImporting}
                    className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all text-xs">
                    <Icon icon={`fa-solid ${urlImporting ? 'fa-spinner animate-spin' : 'fa-arrow-up'}`} />
                  </button>
                  <button onClick={() => { setShowUrlImport(false); setUrlImport('') }}
                    className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg text-text-dim hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs">
                    <Icon icon="fa-solid fa-xmark" />
                  </button>
                </>
              )}
            </div>
          </div>

          {/* Entry list */}
          <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-1.5">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
              </div>
            ) : offline ? (
              <div className="flex flex-col items-center justify-center py-12 gap-2">
                <Icon icon="fa-solid fa-wifi text-2xl" style={{ color: 'var(--text-dim)' }} />
                <div className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
                  {t('ux.offlineTitle')}
                </div>
                <div className="text-xs text-center max-w-[240px]" style={{ color: 'var(--text-muted)' }}>
                  {t('ux.offlineDesc')}
                </div>
                <button
                  onClick={() => { setLoading(true); fetchEntries() }}
                  className="mt-1 text-xs px-3 py-1.5 rounded-lg border transition-all hover:bg-white/[0.06]"
                  style={{ borderColor: 'var(--panel-border)', color: 'var(--text-secondary)' }}
                >
                  {t('ux.offlineRetry')}
                </button>
              </div>
            ) : filteredEntries.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 gap-2">
                <Icon icon="fa-regular fa-inbox text-2xl" style={{ color: 'var(--text-dim)' }} />
                <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {filterKind ? t('inbox.emptyFiltered') : t('inbox.emptyAll')}
                </div>
              </div>
            ) : (
              pagedEntries.map((entry) => {
                const meta = (entry.metadata || {}) as Record<string, any>
                const classification = meta.classification as Record<string, any> | undefined
                const kind = classification?.kind || 'uncertain'
                const cfg = KIND_VISUAL[kind] || KIND_VISUAL.uncertain
                const labelKey = KIND_LABEL_KEY[kind] || 'inbox.kindUncertain'
                const confidence = classification?.confidence ?? 0

                return (
                  <div key={entry.id}
                    className="group flex items-start gap-2.5 px-3 py-2.5 rounded-xl border transition-all hover:bg-white/[0.03]"
                    style={{ borderColor: 'var(--panel-border)' }}
                  >
                    {/* Kind icon */}
                    <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5"
                      style={{ background: `${cfg.color}12` }}>
                      <Icon icon={`${cfg.icon} text-xs`} style={{ color: cfg.color }} />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 mb-0.5">
                        {/* Classification badge */}
                        <span className="flex-shrink-0 px-1.5 py-0.5 rounded text-[8px] font-medium"
                          style={{ background: `${cfg.color}18`, color: cfg.color }}>
                          {!classification ? (
                            <><Icon icon="fa-solid fa-spinner animate-spin mr-1" />{t('inbox.classifying')}</>
                          ) : (
                            <>{t(labelKey)}{confidence > 0 && confidence < 0.8 ? ` ${Math.round(confidence * 100)}%` : ''}</>
                          )}
                        </span>
                        {/* Source */}
                        {entry.source && entry.source !== 'manual' && (
                          <span className="text-[8px]" style={{ color: 'var(--text-dim)' }}>
                            {t('inbox.sourceViaFmt', { source: entry.source })}
                          </span>
                        )}
                        <span className="ml-auto text-[8px]" style={{ color: 'var(--text-dim)' }}>
                          {formatTime(entry.created_at)}
                        </span>
                      </div>
                      <div className="text-xs leading-relaxed whitespace-pre-wrap break-words" style={{ color: 'var(--text-primary)' }}>
                        {entry.content}
                      </div>
                      {/* Classification reason */}
                      {classification?.reason && classification.kind !== 'uncertain' && (
                        <div className="text-[8px] mt-0.5" style={{ color: 'var(--text-dim)' }}>
                          {classification.reason}
                        </div>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex items-center gap-0.5 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                      {!classification && (
                        <button onClick={() => { void handleClassify(entry) }} className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-purple-400 hover:bg-purple-400/[0.08] transition-all text-2xs"
                          title={t('inbox.classifyTitle')}>
                          <Icon icon="fa-solid fa-wand-magic-sparkles" />
                        </button>
                      )}
                      {entry.status === 'routed' && (
                        <span className="text-[8px] px-1 py-0.5 rounded" style={{ color: 'var(--success)', background: 'rgba(34,197,94,0.08)' }}>{t('inbox.statusRouted')}</span>
                      )}
                      {(kind === 'note' || kind === 'idea') && (
                        <button
                          onClick={async () => {
                            try {
                              await wsClient.call('notes.create', { title: entry.title || '', content: entry.content, tags: entry.tags || [] })
                              handleArchive(entry.id)
                            } catch (e) { console.warn('[inbox] notes.create failed:', e) }
                          }}
                          className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-green-400 hover:bg-green-400/[0.08] transition-all text-2xs"
                          title={t('inbox.convertToNote')}
                        >
                          <Icon icon="fa-regular fa-note-sticky" />
                        </button>
                      )}
                      {(kind === 'task' || kind === 'reminder') && (
                        <button
                          onClick={async () => {
                            try {
                              await wsClient.call('tasks.create', { title: entry.content.slice(0, 100), tags: entry.tags || [] })
                              handleArchive(entry.id)
                            } catch (e) { console.warn('[inbox] notes.create failed:', e) }
                          }}
                          className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-green-400 hover:bg-green-400/[0.08] transition-all text-2xs"
                          title={t('inbox.convertToTask')}
                        >
                          <Icon icon="fa-solid fa-check" />
                        </button>
                      )}
                      <button onClick={() => handleArchive(entry.id)}
                        className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-blue-400 hover:bg-blue-400/[0.08] transition-all text-2xs"
                        title={t('inbox.archive')}>
                        <Icon icon="fa-regular fa-folder" />
                      </button>
                      <button onClick={() => handleDelete(entry.id)}
                        className="w-6 h-6 flex items-center justify-center rounded text-text-muted hover:text-red-400 hover:bg-red-400/[0.08] transition-all text-2xs"
                        title={t('inbox.delete')}>
                        <Icon icon="fa-solid fa-xmark" />
                      </button>
                    </div>
                  </div>
                )
              })
            )}
            {remainingCount > 0 && (
              <button
                onClick={() => setVisibleCount(c => c + PAGE_SIZE)}
                className="w-full py-2 text-xs rounded-lg border transition-all hover:bg-white/[0.06]"
                style={{ borderColor: 'var(--panel-border)', color: 'var(--text-muted)' }}
              >
                {t('inbox.loadMore', { count: remainingCount })}
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
