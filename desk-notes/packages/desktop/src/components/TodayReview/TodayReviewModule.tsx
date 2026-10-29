import React, { useCallback, useMemo, useState } from 'react'
import Icon from '../Common/Icon'
import Skeleton from '../Common/Skeleton'
import { useTranslation } from '../../i18n'
import { useStore } from '../../store/useStore'
import { CATEGORY_META, formatRelative } from '@desk-notes/shared'
import { useTodayReviewData } from './useTodayReviewData'
import { aggregateDayReview, topCategories, type ReviewEntry } from './todayReviewLogic'

/**
 * V1-6「今日回顾」面板：把今日（本地时区）的资讯捕获 + 今日修改的笔记按时间倒序聚合，
 * 顶部一句统计条，点击条目跳转既有编辑/详情（无跳转目标时展开卡片）。
 * 出口是「打开有东西看」，不依赖 R2 记忆点火。
 */

type DayOffset = 0 | -1

const SUMMARY_CLAMP = 'line-clamp-2'

const categoryMeta = (category?: string) =>
  category ? CATEGORY_META[category] : undefined

const LoadingRows: React.FC = () => (
  <div className="space-y-2" data-testid="today-review-skeleton">
    {[0, 1, 2, 3, 4].map(i => (
      <div key={i} className="rounded-card border border-border bg-surface p-4 space-y-2">
        <div className="flex items-center gap-2">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-3 w-10" />
        </div>
        <Skeleton className="h-4 w-3/5" />
        <Skeleton className="h-3 w-4/5" />
      </div>
    ))}
  </div>
)

const EntryCard: React.FC<{
  entry: ReviewEntry
  expanded: boolean
  onToggle: () => void
  metaLabel: string
}> = ({ entry, expanded, onToggle, metaLabel }) => {
  const { t } = useTranslation()
  const meta = categoryMeta(entry.category)
  return (
    <div className="rounded-card border border-border bg-surface transition-colors hover:bg-hover-bg">
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle() } }}
        className="flex items-start gap-3 p-4 cursor-pointer"
        data-testid={`today-review-entry-${entry.kind}`}
      >
        <Icon
          icon={entry.kind === 'note' ? 'fa-solid fa-file-lines text-base' : 'fa-solid fa-newspaper text-base'}
          className="mt-0.5 shrink-0 text-text-muted"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base font-medium text-text-primary truncate">{entry.title}</span>
            <span
              className="shrink-0 rounded-full px-2 py-0.5 text-2xs"
              style={meta
                ? { color: meta.color, background: `${meta.color}1f` }
                : { color: 'var(--text-muted)', background: 'var(--hover-bg)' }}
            >
              {metaLabel}
            </span>
            <span className="shrink-0 text-2xs text-text-dim">{formatRelative(entry.ts)}</span>
          </div>
          {entry.summary && (
            <p className={`mt-1 text-sm text-text-secondary ${expanded ? 'whitespace-pre-wrap' : SUMMARY_CLAMP}`}>
              {expanded ? entry.summary : entry.summary.slice(0, 400)}
            </p>
          )}
          {expanded && entry.kind === 'info' && entry.url && (
            <a
              href={entry.url}
              target="_blank"
              rel="noopener noreferrer"
              onClick={e => e.stopPropagation()}
              className="mt-2 inline-flex items-center gap-1 text-sm text-brand hover:underline"
            >
              {t('todayReview.openSource')}
              <Icon icon="fa-solid fa-arrow-up-right-from-square text-2xs" />
            </a>
          )}
        </div>
        <Icon
          icon={expanded ? 'fa-solid fa-chevron-up text-2xs' : 'fa-solid fa-chevron-down text-2xs'}
          className="mt-1 shrink-0 text-text-dim"
        />
      </div>
    </div>
  )
}

const TodayReviewModule: React.FC = () => {
  const { t } = useTranslation()
  const { infoItems, notes, loading, error, reload } = useTodayReviewData()
  const [dayOffset, setDayOffset] = useState<DayOffset>(0)
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  const floatingNotes = useStore(s => s.floatingNotes)
  const setActiveModule = useStore(s => s.setActiveModule)
  const setEditingNoteId = useStore(s => s.setEditingNoteId)

  const review = useMemo(
    () => aggregateDayReview({ infoItems, notes, dayOffset }),
    [infoItems, notes, dayOffset],
  )
  const cats = useMemo(() => topCategories(review.entries, 3), [review.entries])
  const localNoteIds = useMemo(() => new Set(floatingNotes.map(n => n.id)), [floatingNotes])

  // 笔记跳既有编辑器（复用 activeModule + editingNoteId 路由动作），
  // 后端 id 与本地笔记对不上、或资讯条目时 → 就地展开卡片。
  const openEntry = useCallback((entry: ReviewEntry) => {
    if (entry.kind === 'note' && entry.id && localNoteIds.has(entry.id)) {
      setActiveModule('notes')
      setEditingNoteId(entry.id)
      return
    }
    setExpandedKey(prev => (prev === entry.key ? null : entry.key))
  }, [localNoteIds, setActiveModule, setEditingNoteId])

  const isEmpty = !loading && !error && review.entries.length === 0

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden" data-testid="today-review">
      <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-border shrink-0">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-clock-rotate-left text-md" className="text-brand" />
            <h2 className="text-lg font-semibold text-text-primary truncate">{t('todayReview.title')}</h2>
          </div>
          <p className="mt-0.5 text-xs text-text-muted">
            {review.dateKey} · {t('todayReview.shortcutHint')}
          </p>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {([-1, 0] as DayOffset[]).map(offset => (
            <button
              key={offset}
              onClick={() => { setDayOffset(offset); setExpandedKey(null) }}
              className={`px-3 py-1.5 rounded-md text-xs transition-colors ${
                dayOffset === offset ? 'text-brand bg-brand-soft' : 'text-text-secondary hover:bg-hover-bg'
              }`}
            >
              {offset === 0 ? t('todayReview.today') : t('todayReview.yesterday')}
            </button>
          ))}
          <button
            onClick={() => void reload()}
            disabled={loading}
            title={t('todayReview.refresh')}
            className="px-2 py-1.5 rounded-md text-xs text-text-secondary hover:bg-hover-bg disabled:opacity-50"
          >
            <Icon icon={loading ? 'fa-solid fa-spinner fa-spin text-sm' : 'fa-solid fa-rotate text-sm'} />
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4 space-y-3">
        {error && (
          <div className="flex items-center justify-between gap-3 rounded-card border border-danger/30 bg-danger-soft px-4 py-3">
            <span className="text-sm text-danger truncate">
              {t('todayReview.loadFailed', { error })}
            </span>
            <button
              onClick={() => void reload()}
              className="shrink-0 rounded-md px-3 py-1 text-xs text-brand bg-brand-soft hover:opacity-80"
            >
              {t('todayReview.retry')}
            </button>
          </div>
        )}

        {!error && !loading && (
          <div className="flex flex-wrap items-center gap-2 text-xs text-text-secondary">
            <span className="text-text-muted">{t('todayReview.scopeLabel')}</span>
            <span className="rounded-full px-2 py-0.5 bg-surface border border-border">
              {t('todayReview.captures', { count: review.captureCount })}
            </span>
            <span className="rounded-full px-2 py-0.5 bg-surface border border-border">
              {t('todayReview.notes', { count: review.noteCount })}
            </span>
            {cats.length > 0 && <span className="text-text-muted">{t('todayReview.activeCategories')}</span>}
            {cats.map(c => {
              const meta = categoryMeta(c.category)
              return (
                <span
                  key={c.category}
                  className="rounded-full px-2 py-0.5 border border-border"
                  style={{ color: meta?.color ?? 'var(--text-secondary)' }}
                >
                  {meta?.label ?? c.category} {c.count}
                </span>
              )
            })}
          </div>
        )}

        {loading && <LoadingRows />}

        {isEmpty && (
          <div className="flex flex-col items-center justify-center gap-3 py-16 text-center" data-testid="today-review-empty">
            <Icon icon="fa-solid fa-inbox text-3xl text-text-dim" />
            <p className="text-base text-text-secondary">{t('todayReview.empty')}</p>
            <button
              onClick={() => { setDayOffset(-1); setExpandedKey(null) }}
              className="rounded-md px-3 py-1.5 text-xs text-brand bg-brand-soft hover:opacity-80"
            >
              {t('todayReview.yesterdayEntry')}
            </button>
          </div>
        )}

        {!loading && review.entries.length > 0 && (
          <div className="space-y-2">
            {review.entries.map(entry => (
              <EntryCard
                key={entry.key}
                entry={entry}
                expanded={expandedKey === entry.key}
                onToggle={() => openEntry(entry)}
                metaLabel={
                  entry.kind === 'note'
                    ? t('todayReview.noteBadge')
                    : (categoryMeta(entry.category)?.label ?? t('todayReview.infoBadge'))
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default TodayReviewModule
