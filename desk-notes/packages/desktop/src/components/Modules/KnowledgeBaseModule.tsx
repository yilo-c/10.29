import React, { useState, useEffect, useCallback, useMemo, useRef, Suspense } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import PageHeader from '../Common/PageHeader'
import { fetchMdList, fetchMdGet, saveMdFile, syncMdFile } from '../Graph/graphService'

const MDEditor = React.lazy(() => import('@uiw/react-md-editor'))

/** MD 文件归类：时间轴 / 主题 / 索引 / 图谱导出 */
interface MdEntry {
  rel: string
  basename: string
  group: 'timeline' | 'topic' | 'index' | 'graph' | 'other'
  year?: string
  month?: string
  day?: string
  topic?: string
  kind: string
}

const KIND_INFO = '_资讯摘要'
const KIND_CONV = '_对话'
const KIND_MEM = '_记忆'
const KIND_CARD = '_知识卡片'
const KIND_INDEX = '_当日索引'

function classify(rel: string): MdEntry {
  const parts = rel.split('/')
  const basename = parts[parts.length - 1] || rel
  const entry: MdEntry = { rel, basename, group: 'other', kind: basename }

  if (parts[0] === '00_索引' || basename.includes(KIND_INDEX)) {
    entry.group = 'index'
    entry.kind = KIND_INDEX
    if (basename.includes(KIND_INDEX)) {
      const m = basename.match(/(\d{4})-(\d{2})-(\d{2})/)
      if (m) { entry.year = m[1]; entry.month = `${m[1]}-${m[2]}`; entry.day = `${m[1]}-${m[2]}-${m[3]}`; entry.group = 'timeline' }
    }
    return entry
  }

  if (parts[0] === '主题') {
    entry.group = 'topic'
    entry.topic = parts[1] || basename.replace(/\.md$/, '')
    if (basename === 'MOC.md') entry.kind = 'MOC'
    else if (basename.startsWith('卡片-')) entry.kind = KIND_CARD
    return entry
  }

  if (parts[0] === '图谱导出') {
    entry.group = 'graph'
    return entry
  }

  const m = basename.match(/^(\d{4})-(\d{2})-(\d{2})_?(.+)?\.md$/)
  if (m) {
    entry.group = 'timeline'
    entry.year = m[1]
    entry.month = `${m[1]}-${m[2]}`
    entry.day = `${m[1]}-${m[2]}-${m[3]}`
    const suffix = m[4] || ''
    if (suffix.includes(KIND_INFO)) entry.kind = KIND_INFO
    else if (suffix.includes(KIND_CONV)) entry.kind = KIND_CONV
    else if (suffix.includes(KIND_MEM)) entry.kind = KIND_MEM
    else if (suffix.includes(KIND_CARD)) entry.kind = KIND_CARD
    else entry.kind = suffix
    return entry
  }

  return entry
}

function fileIcon(kind: string): string {
  if (kind === KIND_INFO) return 'fa-regular fa-newspaper'
  if (kind === KIND_CONV) return 'fa-regular fa-comment-dots'
  if (kind === KIND_MEM) return 'fa-regular fa-note-sticky'
  if (kind === KIND_CARD) return 'fa-regular fa-clone'
  if (kind === KIND_INDEX) return 'fa-regular fa-calendar-days'
  if (kind === 'MOC') return 'fa-regular fa-map'
  return 'fa-regular fa-file-lines'
}

const KnowledgeBaseModule: React.FC = () => {
  const { t } = useTranslation()

  const [treeMode, setTreeMode] = useState<'timeline' | 'topics'>('timeline')
  const [files, setFiles] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())

  // 编辑器状态
  const [selectedRel, setSelectedRel] = useState<string | null>(null)
  const [mdContent, setMdContent] = useState('')
  const [loadedRel, setLoadedRel] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saved' | 'failed' | 'skipped' | 'sync_failed'>('idle')
  const editorWrapRef = useRef<HTMLDivElement>(null)
  const [editorH, setEditorH] = useState(600)

  const entries = useMemo(() => files.map(classify), [files])

  // 刷新文件树
  const loadFiles = useCallback(async () => {
    setLoading(true)
    const { files: list } = await fetchMdList()
    setFiles(list)
    setLoading(false)
  }, [])

  useEffect(() => { loadFiles() }, [loadFiles])

  // 测量编辑器容器高度（@uiw/react-md-editor 的 height 需为数值）
  useEffect(() => {
    const el = editorWrapRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) setEditorH(Math.max(200, Math.floor(entry.contentRect.height)))
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // 打开文件
  const openFile = useCallback(async (rel: string) => {
    if (dirty) {
      // 有未保存修改时先提示，简单处理：直接丢弃并切换
    }
    setSelectedRel(rel)
    setEditing(false)
    setSaveStatus('idle')
    setMdContent('')
    setLoadedRel(null)
    const got = await fetchMdGet(rel)
    if (!got) return
    setMdContent(got.content)
    setLoadedRel(rel)
    setDirty(false)
  }, [dirty])

  const toggleExpand = useCallback((key: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const handleSave = useCallback(async () => {
    if (!selectedRel || saving) return
    setSaving(true)
    setSaveStatus('idle')
    const res = await saveMdFile(selectedRel, mdContent)
    setSaving(false)
    if (res.success) {
      setDirty(false)
      const sync = res.sync
      if (sync?.success) setSaveStatus('saved')
      else if (sync?.skipped) setSaveStatus('skipped')
      else setSaveStatus('sync_failed')
    } else {
      setSaveStatus('failed')
    }
  }, [selectedRel, mdContent, saving])

  const handleReSync = useCallback(async () => {
    if (!selectedRel) return
    await syncMdFile(selectedRel)
    setSaveStatus('saved')
  }, [selectedRel])

  // ── 时间轴树：年 → 月 → 日 → 文件 ──
  const timelineTree = useMemo(() => {
    const tl = entries.filter(e => e.group === 'timeline')
    const years = new Map<string, Map<string, Map<string, MdEntry[]>>>()
    for (const e of tl) {
      if (!e.year || !e.month || !e.day) continue
      if (!years.has(e.year)) years.set(e.year, new Map())
      const months = years.get(e.year)!
      if (!months.has(e.month!)) months.set(e.month!, new Map())
      const days = months.get(e.month!)!
      if (!days.has(e.day!)) days.set(e.day!, [])
      days.get(e.day!)!.push(e)
    }
    return Array.from(years.entries())
      .sort((a, b) => (a[0] > b[0] ? -1 : 1))
      .map(([year, months]) => ({
        year,
        months: Array.from(months.entries())
          .sort((a, b) => (a[0] > b[0] ? -1 : 1))
          .map(([month, days]) => ({
            month,
            days: Array.from(days.entries())
              .sort((a, b) => (a[0] > b[0] ? -1 : 1))
              .map(([day, dayFiles]) => ({ day, dayFiles })),
          })),
      }))
  }, [entries])

  // ── 主题轴树：主题目录 → 文件 ──
  const topicsTree = useMemo(() => {
    const topics = new Map<string, MdEntry[]>()
    const indexFiles: MdEntry[] = []
    const graphFiles: MdEntry[] = []
    for (const e of entries) {
      if (e.group === 'topic') {
        const key = e.topic || '未分类'
        if (!topics.has(key)) topics.set(key, [])
        topics.get(key)!.push(e)
      } else if (e.group === 'index') {
        indexFiles.push(e)
      } else if (e.group === 'graph') {
        graphFiles.push(e)
      }
    }
    const topicArr = Array.from(topics.entries()).sort((a, b) => (a[0] > b[0] ? 1 : -1))
    return { topics: topicArr, indexFiles, graphFiles }
  }, [entries])

  const kindLabel = useCallback((kind: string): string => {
    if (kind === KIND_INFO) return t('knowledgeBase.typeInfo')
    if (kind === KIND_CONV) return t('knowledgeBase.typeConversation')
    if (kind === KIND_MEM) return t('knowledgeBase.typeMemory')
    if (kind === KIND_CARD) return t('knowledgeBase.typeCard')
    if (kind === KIND_INDEX) return t('knowledgeBase.typeIndex')
    if (kind === 'MOC') return t('knowledgeBase.typeMoc')
    return t('knowledgeBase.typeNote')
  }, [t])

  // ── 文件树节点 ──
  const renderFile = (e: MdEntry) => {
    const active = selectedRel === e.rel
    return (
      <button
        key={e.rel}
        onClick={() => openFile(e.rel)}
        className={`w-full flex items-center gap-1.5 pl-6 pr-2 py-1 rounded-md text-xs transition-colors text-left ${
          active ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-text-secondary hover:bg-white/5 hover:text-text-primary'
        }`}
      >
        <Icon icon={`${fileIcon(e.kind)} text-2xs flex-shrink-0`} />
        <span className="truncate flex-1">{kindLabel(e.kind)}</span>
        {active && <span className="w-1.5 h-1.5 rounded-full bg-fluent-blue flex-shrink-0" />}
      </button>
    )
  }

  const renderTimeline = () => (
    <div className="space-y-3">
      {timelineTree.map(({ year, months }) => {
        const yk = `y:${year}`
        const yOpen = expanded.has(yk)
        return (
          <div key={yk}>
            <button
              onClick={() => toggleExpand(yk)}
              className="w-full flex items-center gap-1.5 px-1 py-1 rounded-md text-xs text-text-primary hover:bg-white/5 transition-colors"
            >
              <Icon icon={`fa-solid fa-chevron-${yOpen ? 'down' : 'right'} text-[8px] text-text-muted`} />
              <Icon icon="fa-regular fa-calendar" className="text-2xs text-text-muted" />
              <span className="font-medium">{year}</span>
              <span className="text-text-dim text-[9px]">{months.reduce((n, m) => n + m.days.length, 0)}</span>
            </button>
            {yOpen && months.map(({ month, days }) => {
              const mk = `${yk}/${month}`
              const mOpen = expanded.has(mk)
              return (
                <div key={mk} className="ml-3 border-l" style={{ borderColor: 'var(--border)' }}>
                  <button
                    onClick={() => toggleExpand(mk)}
                    className="w-full flex items-center gap-1.5 pl-2 py-1 rounded-md text-xs text-text-secondary hover:bg-white/5 transition-colors"
                  >
                    <Icon icon={`fa-solid fa-chevron-${mOpen ? 'down' : 'right'} text-[8px] text-text-muted`} />
                    <span className="font-medium">{month}</span>
                    <span className="text-text-dim text-[9px]">{days.length}</span>
                  </button>
                  {mOpen && days.map(({ day, dayFiles }) => {
                    const dk = `${mk}/${day}`
                    const dOpen = expanded.has(dk)
                    return (
                      <div key={dk} className="ml-3 border-l" style={{ borderColor: 'var(--border)' }}>
                        <button
                          onClick={() => toggleExpand(dk)}
                          className="w-full flex items-center gap-1.5 pl-2 py-1 rounded-md text-xs text-text-secondary hover:bg-white/5 transition-colors"
                        >
                          <Icon icon={`fa-solid fa-chevron-${dOpen ? 'down' : 'right'} text-[8px] text-text-muted`} />
                          <span>{day}</span>
                        </button>
                        {dOpen && <div className="space-y-0.5 py-0.5">{dayFiles.map(renderFile)}</div>}
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        )
      })}
      {timelineTree.length === 0 && (
        <p className="text-xs text-text-dim px-2 py-4 text-center">{t('knowledgeBase.empty')}</p>
      )}
    </div>
  )

  const renderTopics = () => {
    const { topics, indexFiles, graphFiles } = topicsTree
    return (
      <div className="space-y-3">
        {/* 索引 */}
        {indexFiles.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 px-1 py-1 text-xs text-text-muted">
              <Icon icon="fa-solid fa-folder text-2xs" />
              <span>{t('knowledgeBase.folderIndex')}</span>
            </div>
            <div className="space-y-0.5">{indexFiles.map(renderFile)}</div>
          </div>
        )}
        {/* 主题 */}
        {topics.map(([topic, topicFiles]) => {
          const tk = `t:${topic}`
          const open = expanded.has(tk)
          const moc = topicFiles.find(f => f.kind === 'MOC')
          const cards = topicFiles.filter(f => f.kind === KIND_CARD)
          return (
            <div key={tk}>
              <button
                onClick={() => toggleExpand(tk)}
                className="w-full flex items-center gap-1.5 px-1 py-1 rounded-md text-xs text-text-primary hover:bg-white/5 transition-colors"
              >
                <Icon icon={`fa-solid fa-chevron-${open ? 'down' : 'right'} text-[8px] text-text-muted`} />
                <Icon icon="fa-solid fa-folder-tree text-2xs text-fluent-blue" />
                <span className="font-medium truncate flex-1">{topic}</span>
                <span className="text-text-dim text-[9px]">{topicFiles.length}</span>
              </button>
              {open && (
                <div className="ml-3 border-l space-y-0.5 py-0.5" style={{ borderColor: 'var(--border)' }}>
                  {moc && renderFile(moc)}
                  {cards.map(renderFile)}
                </div>
              )}
            </div>
          )
        })}
        {/* 图谱导出 */}
        {graphFiles.length > 0 && (
          <div>
            <div className="flex items-center gap-1.5 px-1 py-1 text-xs text-text-muted">
              <Icon icon="fa-solid fa-folder text-2xs" />
              <span>{t('knowledgeBase.folderGraph')}</span>
            </div>
            <div className="space-y-0.5">{graphFiles.map(renderFile)}</div>
          </div>
        )}
        {topics.length === 0 && indexFiles.length === 0 && graphFiles.length === 0 && (
          <p className="text-xs text-text-dim px-2 py-4 text-center">{t('knowledgeBase.empty')}</p>
        )}
      </div>
    )
  }

  const saveStatusLabel = (): { text: string; tone: string } => {
    switch (saveStatus) {
      case 'saved': return { text: t('knowledgeBase.saved'), tone: 'text-emerald-400' }
      case 'skipped': return { text: t('knowledgeBase.syncSkipped'), tone: 'text-amber-400' }
      case 'sync_failed': return { text: t('knowledgeBase.syncFailed'), tone: 'text-amber-400' }
      case 'failed': return { text: t('knowledgeBase.saveFailed'), tone: 'text-red-400' }
      default: return { text: '', tone: '' }
    }
  }
  const status = saveStatusLabel()

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className="flex-shrink-0 px-5 pt-5 pb-3">
        <PageHeader
          title={t('nav.knowledgeBase')}
          subtitle={t('knowledgeBase.fileCountFmt', { count: files.length })}
          actions={(
            <button
              onClick={loadFiles}
              className="w-8 h-8 flex items-center justify-center rounded-lg text-sm text-text-muted hover:text-text-secondary hover:bg-hover-bg transition-colors"
              title={t('knowledgeBase.refresh')}
            >
              <Icon icon={`fa-solid fa-rotate ${loading ? 'animate-spin' : ''}`} />
            </button>
          )}
        />
      </div>

      <div className="flex flex-1 min-h-0 border-t" style={{ borderColor: 'var(--border)' }}>
        {/* ── 左侧：文件树 ── */}
        <div className="w-60 flex-shrink-0 border-r flex flex-col min-h-0" style={{ borderColor: 'var(--border)' }}>
          <div className="flex items-center gap-0.5 bg-white/[0.04] rounded-lg p-0.5 border border-white/[0.04] m-3 mb-2">
            <button
              onClick={() => setTreeMode('timeline')}
              className={`flex items-center justify-center gap-1.5 flex-1 px-2 py-1 rounded-md text-xs transition-colors ${
                treeMode === 'timeline' ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Icon icon="fa-solid fa-timeline" />
              {t('knowledgeBase.viewTimeline')}
            </button>
            <button
              onClick={() => setTreeMode('topics')}
              className={`flex items-center justify-center gap-1.5 flex-1 px-2 py-1 rounded-md text-xs transition-colors ${
                treeMode === 'topics' ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Icon icon="fa-solid fa-folder-tree" />
              {t('knowledgeBase.viewTopics')}
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-3 pb-3">
            {treeMode === 'timeline' ? renderTimeline() : renderTopics()}
          </div>
        </div>

        {/* ── 右侧：MD 编辑器 / 空态 ── */}
        <div className="flex-1 flex flex-col min-h-0">
          {!selectedRel ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center px-6">
              <Icon icon="fa-regular fa-folder-open text-4xl text-text-dim" />
              <p className="text-sm text-text-muted">{t('knowledgeBase.emptyHint')}</p>
            </div>
          ) : (
            <>
              {/* 编辑器头 */}
              <div className="flex items-center gap-2 px-4 py-2 border-b" style={{ borderColor: 'var(--border)' }}>
                <button
                  onClick={() => { setSelectedRel(null); setDirty(false) }}
                  className="text-xs text-text-muted hover:text-text-secondary transition-colors"
                >
                  <Icon icon="fa-solid fa-arrow-left" />
                </button>
                <Icon icon={`${fileIcon(entries.find(e => e.rel === selectedRel)?.kind || '')} text-xs text-text-muted`} />
                <span className="text-xs text-text-primary truncate flex-1 font-medium">{selectedRel}</span>
                {dirty && (
                  <span className="text-2xs px-1.5 py-0.5 rounded bg-amber-400/15 text-amber-400 flex-shrink-0">
                    {t('knowledgeBase.dirtyBadge')}
                  </span>
                )}
                {status.text && (
                  <span className={`text-2xs flex-shrink-0 ${status.tone}`}>{status.text}</span>
                )}
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  <button
                    onClick={handleReSync}
                    title="重新回灌"
                    className="px-2 py-1 rounded-md text-xs text-text-muted hover:text-text-secondary hover:bg-white/5 transition-colors"
                  >
                    <Icon icon="fa-solid fa-arrows-rotate" />
                  </button>
                  <button
                    onClick={() => setEditing(v => !v)}
                    className={`px-2.5 py-1 rounded-md text-xs transition-colors ${
                      editing ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-text-muted hover:text-text-secondary hover:bg-white/5'
                    }`}
                  >
                    {editing ? '预览' : '编辑'}
                  </button>
                  <button
                    onClick={handleSave}
                    disabled={saving || !dirty}
                    className="px-3 py-1 rounded-md text-xs font-medium bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 transition-colors disabled:opacity-40"
                  >
                    <Icon icon={`fa-solid ${saving ? 'fa-circle-notch animate-spin' : 'fa-floppy-disk'} mr-1`} />
                    {saving ? t('knowledgeBase.saving') : t('knowledgeBase.save')}
                  </button>
                </div>
              </div>

              {/* 编辑/预览 主体 */}
              <div ref={editorWrapRef} className="flex-1 min-h-0" data-color-mode="dark">
                <Suspense fallback={<div className="flex-1 flex items-center justify-center text-xs text-text-dim">...</div>}>
                  <MDEditor
                    value={mdContent}
                    onChange={val => { setMdContent(val ?? ''); if (loadedRel === selectedRel) setDirty(true) }}
                    height={editorH}
                    preview={editing ? 'edit' : 'preview'}
                    visiableDragbar={false}
                    hideToolbar={!editing}
                  />
                </Suspense>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default KnowledgeBaseModule
