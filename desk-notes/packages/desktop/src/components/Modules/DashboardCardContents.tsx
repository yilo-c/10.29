import React, { useState, useEffect, useMemo, useRef } from 'react'
import Icon from '../Common/Icon'
import Skeleton from '../Common/Skeleton'

import { useStore } from '../../store/useStore'
import { getWsClient } from '../../utils/ai'
import { useWsConnected } from '../../hooks/useWsConnected'
import type { FloatingNote } from '@desk-notes/shared'
import { COLOR_MAP, type InfoItem } from './DashboardTypes'
import { swallowError } from '../../utils/swallowError'
import { useTranslation } from '../../i18n'
import EmptyState from '../Common/EmptyState'

type InboxEntry = {
  id: string
  content: string
  title: string
  tags: string[]
  status: string
  created_at: string
  metadata?: Record<string, unknown>
}

export const TaskCardContent: React.FC = () => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const setActiveModule = useStore(s => s.setActiveModule)

  const pending = useMemo(() =>
    todos.filter(t => !t.done).sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0)).slice(0, 5),
    [todos],
  )

  if (pending.length === 0) {
    return <EmptyState compact icon="fa-solid fa-circle-check" title={t('dashCards.taskEmpty')} />
  }

  return (
    <div className="space-y-1">
      {pending.map(todo => (
        <div
          key={todo.id}
          className="flex items-center gap-2.5 py-1.5 px-2 rounded-lg cursor-pointer transition-all text-xs hover:bg-white/[0.05]"
          style={{ color: 'var(--text-secondary)' }}
          onClick={() => { useStore.getState().toggleTodo(todo.id) }}
        >
          <div
            className="w-4 h-4 rounded-full border-2 flex-shrink-0 transition-all flex items-center justify-center"
            style={{
              borderColor: todo.priority && todo.priority >= 2
                ? COLOR_MAP.purple.hex : 'var(--panel-border)',
            }}
          >
            {todo.done && <Icon icon="fa-solid fa-check text-[6px]" style={{ color: COLOR_MAP.green.hex }} />}
          </div>
          <span className="flex-1 truncate">{todo.text}</span>
          {todo.dueDate && (
            <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
              {new Date(todo.dueDate).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })}
            </span>
          )}
        </div>
      ))}
      <button
        onClick={() => setActiveModule('todo')}
        className="w-full text-xs py-1.5 rounded-lg transition-all mt-1 hover:bg-white/[0.05]"
        style={{ color: 'var(--text-muted)' }}
      >
        {t('dashCards.taskViewAll')}
      </button>
    </div>
  )
}

export const NotesCardContent: React.FC = () => {
  const { t } = useTranslation()
  const floatingNotes = useStore(s => s.floatingNotes)
  const setActiveModule = useStore(s => s.setActiveModule)

  // B2：初始同步计算——不再用 useState([]) + 2s setTimeout 制造假空态，
  // 首屏即显示真实笔记；throttle 只约束后续 floatingNotes 变化的更新。
  const computeDisplayNotes = () =>
    [...floatingNotes]
      .filter(n => !n.archived)
      .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0))
      .slice(0, 4)

  const [displayNotes, setDisplayNotes] = useState<FloatingNote[]>(computeDisplayNotes)
  const throttleRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (throttleRef.current) clearTimeout(throttleRef.current)
    throttleRef.current = setTimeout(() => {
      setDisplayNotes(computeDisplayNotes())
    }, 2000)
    return () => { if (throttleRef.current) clearTimeout(throttleRef.current) }
  }, [floatingNotes])

  if (displayNotes.length === 0) {
    return <EmptyState compact icon="fa-regular fa-note-sticky" title={t('dashCards.notesEmpty')} />
  }

  return (
    <div className="space-y-1">
      {displayNotes.map(note => (
        <div
          key={note.id}
          className="flex items-center gap-2.5 py-1.5 px-2 rounded-lg cursor-pointer transition-all text-xs hover:bg-white/[0.05]"
          style={{ color: 'var(--text-secondary)' }}
        >
          <Icon icon="fa-regular fa-file-lines text-xs flex-shrink-0 opacity-40" />
          <span className="flex-1 truncate">{note.title}</span>
          <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
            {note.updatedAt ? new Date(note.updatedAt).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) : ''}
          </span>
        </div>
      ))}
      <button
        onClick={() => setActiveModule('notes')}
        className="w-full text-xs py-1.5 rounded-lg transition-all mt-1 hover:bg-white/[0.05]"
        style={{ color: 'var(--text-muted)' }}
      >
        {t('dashCards.notesViewAll')}
      </button>
    </div>
  )
}

export const SuggestionsCardContent: React.FC = () => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const floatingNotes = useStore(s => s.floatingNotes)

  const suggestions = useMemo(() => {
    const items: { icon: string; text: string; onClick: () => void }[] = []
    const pending = todos.filter(t => !t.done)
    const recentNotes = floatingNotes.filter(n => !n.archived)

    if (pending.length > 0) {
      items.push({
        icon: 'fa-regular fa-square-check',
        text: t('dashCards.suggestionsPendingTasksFmt', { count: pending.length }),
        onClick: () => useStore.getState().setActiveModule('todo'),
      })
    }
    if (recentNotes.length === 0) {
      items.push({
        icon: 'fa-regular fa-pen-to-square',
        text: t('dashCards.suggestionsWriteNote'),
        onClick: () => useStore.getState().setActiveModule('notes'),
      })
    }
    items.push({
      icon: 'fa-regular fa-inbox',
      text: t('dashCards.suggestionsCollectInbox'),
      onClick: () => useStore.getState().setActiveModule('inbox'),
    })
    return items.slice(0, 4)
  }, [todos, floatingNotes, t])

  if (suggestions.length === 0) {
    return <EmptyState compact icon="fa-regular fa-lightbulb" title={t('dashCards.suggestionsEmpty')} />
  }

  return (
    <div className="space-y-1">
      {suggestions.map((s, i) => (
        <div
          key={i}
          className="flex items-center gap-2.5 py-2 px-2.5 rounded-lg cursor-pointer transition-all text-xs leading-relaxed hover:bg-white/[0.05]"
          style={{ color: 'var(--text-secondary)' }}
          onClick={s.onClick}
        >
          <Icon icon={`${s.icon} text-xs flex-shrink-0 opacity-50`} />
          <span>{s.text}</span>
        </div>
      ))}
    </div>
  )
}

export const InfoCardContent: React.FC<{ onNavigate: () => void }> = ({ onNavigate }) => {
  const { t } = useTranslation()
  const [items, setItems] = useState<InfoItem[] | null>(null)
  const [loading, setLoading] = useState(true)
  // I3：连接状态纳入 deps —— 断线后 onStatusChange 触发 connected 翻转，
  // effect 重跑并重新拉取，修复断线后永久空白。
  const connected = useWsConnected()

  useEffect(() => {
    if (!connected) {
      setLoading(false)
      return
    }
    let cancelled = false
    const load = async () => {
      try {
        const client = getWsClient()
        const res = await client.call<{ items?: InfoItem[] }>('info.brief', { topics: [] })
        if (!cancelled && res?.items) setItems(res.items.slice(0, 3))
      } catch (e) { /* ignore */ swallowError('DashboardCardContents:Info', e) }
      if (!cancelled) setLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [connected])

  if (loading) {
    // C5：骨架占位（模拟列表布局，避免加载完成后跳动）
    return (
      <div className="space-y-2 py-1">
        {[0, 1, 2].map(i => (
          <div key={i} className="space-y-1">
            <Skeleton className="h-3 w-4/5 rounded" />
            <Skeleton className="h-2 w-2/5 rounded" />
          </div>
        ))}
      </div>
    )
  }

  if (!items || items.length === 0) {
    return <EmptyState compact icon="fa-regular fa-newspaper" title={t('dashCards.infoEmpty')} />
  }

  return (
    <div className="space-y-1">
      {items.map((item, i) => (
        <div
          key={i}
          className="flex items-center gap-2.5 py-1.5 px-2 rounded-lg cursor-pointer transition-all text-xs hover:bg-white/[0.05]"
          style={{ color: 'var(--text-secondary)' }}
          onClick={() => {
            if (item.url) window.open(item.url, '_blank')
          }}
        >
          <Icon icon="fa-regular fa-file-lines text-xs flex-shrink-0 opacity-40" />
          <span className="flex-1 truncate">{item.title}</span>
          {item.source && (
            <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
              {item.source}
            </span>
          )}
        </div>
      ))}
      <button
        onClick={onNavigate}
        className="w-full text-xs py-1.5 rounded-lg transition-all mt-1 hover:bg-white/[0.05]"
        style={{ color: 'var(--text-muted)' }}
      >
        {t('dashCards.infoViewAll')}
      </button>
    </div>
  )
}

export const TranslateCardContent: React.FC<{ onNavigate: () => void }> = ({ onNavigate }) => {
  const { t } = useTranslation()
  return (
    <div className="flex flex-col items-center justify-center py-3 gap-2.5 text-center">
      <div className="flex items-center gap-2">
        <span className="px-2 py-0.5 rounded text-2xs bg-white/[0.06]" style={{ color: 'var(--brand)' }}>{t('dashCards.translateQuick')}</span>
        <span className="px-2 py-0.5 rounded text-2xs bg-white/[0.06]" style={{ color: '#a855f7' }}>{t('dashCards.translateLearn')}</span>
        <span className="px-2 py-0.5 rounded text-2xs bg-white/[0.06]" style={{ color: 'var(--warning)' }}>{t('dashCards.translateDeep')}</span>
      </div>
      <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
        {t('dashCards.translateHint')}
      </div>
      <button
        onClick={onNavigate}
        className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
        style={{ background: 'rgba(96,165,250,0.12)', color: 'var(--brand)' }}
      >
        <Icon icon="fa-solid fa-language mr-1" />{t('dashCards.translateEnter')}
      </button>
    </div>
  )
}

const KIND_COLORS: Record<string, string> = {
  note: '#3b82f6',
  task: 'var(--warning)',
  reminder: 'var(--danger)',
  idea: '#a855f7',
  question: '#10b981',
  reference: '#6b7280',
  uncertain: '#78716c',
}

const KIND_LABEL_KEY: Record<string, string> = {
  note: 'dashCards.kindNote',
  task: 'dashCards.kindTask',
  reminder: 'dashCards.kindReminder',
  idea: 'dashCards.kindIdea',
  question: 'dashCards.kindQuestion',
  reference: 'dashCards.kindReference',
  uncertain: 'dashCards.kindUncertain',
}

export const InboxCardContent: React.FC<{ onNavigate: () => void }> = ({ onNavigate }) => {
  const { t } = useTranslation()
  const [entries, setEntries] = useState<InboxEntry[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [totalCount, setTotalCount] = useState(0)
  // I3：连接状态纳入 deps —— 断线重连后自动重新拉取收件箱列表。
  const connected = useWsConnected()

  useEffect(() => {
    if (!connected) {
      setLoading(false)
      return
    }
    let cancelled = false
    const load = async () => {
      try {
        const client = getWsClient()
        // V1 修复（2026-09-16）：服务端 inbox.list 默认只回 50（上限 200），而本卡片
        // 的「总数」需要全量口径（否则 689 条会显示成 ≤50），故按页拉满再统计。
        const PAGE_FETCH = 200
        const MAX_PAGES = 10
        let res: InboxEntry[] = []
        for (let page = 0; page < MAX_PAGES; page += 1) {
          const chunk = await client.call<InboxEntry[]>('inbox.list', {
            limit: PAGE_FETCH,
            offset: page * PAGE_FETCH,
          })
          if (!Array.isArray(chunk) || chunk.length === 0) break
          res = res.concat(chunk)
          if (chunk.length < PAGE_FETCH) break
        }
        if (!cancelled && Array.isArray(res)) {
          const active = res.filter(e => e.status !== 'archived' && e.status !== 'dismissed')
          setTotalCount(active.length)
          setEntries(active.slice(0, 3))
        }
      } catch (e) { /* ignore */ swallowError('DashboardCardContents:Inbox', e) }
      if (!cancelled) setLoading(false)
    }
    load()
    return () => { cancelled = true }
  }, [connected])

  if (loading) {
    // C5：骨架占位（替代裸 spinner）
    return (
      <div className="space-y-2 py-1">
        {[0, 1, 2].map(i => (
          <div key={i} className="space-y-1">
            <Skeleton className="h-3 w-3/4 rounded" />
            <Skeleton className="h-2 w-1/3 rounded" />
          </div>
        ))}
      </div>
    )
  }

  if (!entries || entries.length === 0) {
    return (
      <EmptyState
        compact
        icon="fa-regular fa-inbox"
        title={t('dashCards.inboxEmpty')}
        subtitle={t('dashCards.inboxEmptyHint')}
      />
    )
  }

  return (
    <div className="space-y-1">
      {entries.map(entry => {
        const kind = (entry.tags?.[0] || 'uncertain') as string
        const color = KIND_COLORS[kind] || KIND_COLORS['uncertain']
        const labelKey = KIND_LABEL_KEY[kind] || KIND_LABEL_KEY['uncertain']
        return (
          <div
            key={entry.id}
            className="flex items-center gap-2.5 py-1.5 px-2 rounded-lg text-xs"
            style={{ color: 'var(--text-secondary)' }}
          >
            <span className="text-2xs px-1.5 py-0.5 rounded shrink-0" style={{ background: `${color}18`, color }}>
              {t(labelKey)}
            </span>
            <span className="flex-1 truncate">{entry.title || entry.content?.slice(0, 40) || t('dashCards.inboxNoTitle')}</span>
          </div>
        )
      })}
      <button
        onClick={onNavigate}
        className="w-full text-xs py-1.5 rounded-lg transition-all mt-1 hover:bg-white/[0.05]"
        style={{ color: 'var(--text-muted)' }}
      >
        {t('dashCards.inboxViewAllFmt', { count: totalCount })}
      </button>
    </div>
  )
}
