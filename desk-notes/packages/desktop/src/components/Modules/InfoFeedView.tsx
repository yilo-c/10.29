import React, { useMemo, useState, useCallback } from 'react'
import Icon from '../Common/Icon'

import { CATEGORY_META, type RawSourceItem } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'
import { SOURCE_META } from './info-constants'
import { swallowError } from '../../utils/swallowError'
import { useTranslation } from '../../i18n'

interface Props {
  items: RawSourceItem[]
  filterSource: string | null
  onFilterSource: (source: string | null) => void
  onItemClick: (item: RawSourceItem) => void
  savedUrls?: Set<string>
  onToggleBookmark?: (item: RawSourceItem) => void
  userCity?: string
}

const InfoFeedView: React.FC<Props> = ({ items, filterSource, onFilterSource, onItemClick, savedUrls, onToggleBookmark, userCity }) => {
  const { t } = useTranslation()
  const [bilingual, setBilingual] = useState(true)
  const [groupByCategory, setGroupByCategory] = useState(true)
  const [filterCategory, setFilterCategory] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [personalized, setPersonalized] = useState(false)
  const [personalizedScores, setPersonalizedScores] = useState<Record<string, number>>({})
  const [personalizing, setPersonalizing] = useState(false)
  const [summaries, setSummaries] = useState<Record<string, { loading: boolean; text: string; error: string }>>({})

  const wsClient = getWsClient()

  const handleTogglePersonalized = useCallback(async () => {
    const next = !personalized
    setPersonalized(next)
    if (!next) {
      setPersonalizedScores({})
      return
    }
    setPersonalizing(true)
    try {
      const result = await wsClient.call<{ scores: Record<string, number>; order: string[]; keywords_matched: number }>('info.personalize', {
        items: items.map(it => ({ id: it.id, title: it.title, title_zh: it.title_zh, summary: it.summary, summary_zh: it.summary_zh, tags: it.tags })),
      })
      if (result?.scores) setPersonalizedScores(result.scores)
    } catch (e) { /* non-critical */ swallowError('InfoFeedView:42', e) }
    finally { setPersonalizing(false) }
  }, [personalized, items, wsClient])

  const handleSummarize = useCallback(async (item: RawSourceItem) => {
    const existing = summaries[item.id]
    if (existing?.loading) return
    if (existing?.text) return // already have it

    setSummaries(prev => ({ ...prev, [item.id]: { loading: true, text: '', error: '' } }))
    try {
      const result = await wsClient.call<{ summary: string; error: string }>('info.summarize', {
        title: item.title,
        url: item.url,
        summary: item.summary || '',
      })
      if (result?.summary) {
        setSummaries(prev => ({ ...prev, [item.id]: { loading: false, text: result.summary, error: '' } }))
      } else {
        setSummaries(prev => ({ ...prev, [item.id]: { loading: false, text: '', error: result?.error || t('infoFeed.aiGenerateFailed') } }))
      }
    } catch {
      setSummaries(prev => ({ ...prev, [item.id]: { loading: false, text: '', error: t('infoFeed.aiRequestFailed') } }))
    }
  }, [wsClient, summaries, t])

  const categoryCounts = useMemo(() => {
    const counts: Record<string, { total: number }> = {}
    for (const item of items) {
      const cat = item.category || 'general'
      if (!counts[cat]) counts[cat] = { total: 0 }
      counts[cat].total++
    }
    return counts
  }, [items])

  const sourceCounts = useMemo(() => {
    const counts: Record<string, { total: number }> = {}
    for (const item of items) {
      if (!counts[item.source]) counts[item.source] = { total: 0 }
      counts[item.source].total++
    }
    return counts
  }, [items])

  const filtered = useMemo(() => {
    let result = items
    if (filterCategory) {
      result = result.filter(item => (item.category || 'general') === filterCategory)
    }
    if (filterSource) {
      result = result.filter(item => item.source === filterSource)
    }
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase()
      result = result.filter(item => {
        const srcLabel = (SOURCE_META[item.source]?.label || item.source).toLowerCase()
        return (item.title || '').toLowerCase().includes(q)
          || (item.title_zh || '').toLowerCase().includes(q)
          || (item.summary || '').toLowerCase().includes(q)
          || (item.summary_zh || '').toLowerCase().includes(q)
          || item.tags.some(t => t.toLowerCase().includes(q))
          || srcLabel.includes(q)
          || (item.source || '').toLowerCase().includes(q)
      })
    }
    return result
  }, [items, filterCategory, filterSource, searchQuery])

  const sorted = useMemo(() => {
    if (personalized && Object.keys(personalizedScores).length > 0) {
      return [...filtered].sort((a, b) => (personalizedScores[b.id] || 0) - (personalizedScores[a.id] || 0))
    }
    return [...filtered].sort((a, b) => b.score - a.score)
  }, [filtered, personalized, personalizedScores])

  const grouped = useMemo(() => {
    const catOrder = ['ai', 'dev', 'internet', 'domestic', 'international', 'local', 'policy', 'science', 'tool', 'general']
    const groups: { cat: string; items: typeof sorted }[] = []
    const remaining = new Set(sorted.map(it => it.id))
    for (const cat of catOrder) {
      const catItems = sorted.filter(it => (it.category || 'general') === cat && remaining.has(it.id)).slice(0, 5)
      if (catItems.length > 0) {
        groups.push({ cat, items: catItems })
        catItems.forEach(it => remaining.delete(it.id))
      }
    }
    return groups
  }, [sorted])

  // ── inline helper: renders a single feed item card ──
  function renderFeedItem(item: (typeof sorted)[0]) {
    const sourceMeta = SOURCE_META[item.source] || { label: item.source, icon: 'fa-regular fa-circle', color: '#888' }
    const catMeta = CATEGORY_META[item.category || 'general'] || CATEGORY_META['general']
    const showBilingual = bilingual && !!(item.title_zh || item.summary_zh)
    const displayTitle = showBilingual && item.title_zh ? item.title_zh : item.title
    const displaySummary = showBilingual && item.summary_zh ? item.summary_zh : item.summary

    return (
      <div
        key={item.id}
        className="flex items-start gap-3 px-3 py-2.5 rounded-xl border transition-all hover:bg-white/[0.04] group"
        style={{ borderColor: 'var(--panel-border)' }}
      >
        {/* Source icon */}
        <div
          className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5 cursor-pointer"
          style={{ background: `${sourceMeta.color}18`, color: sourceMeta.color, fontSize: 10 }}
          onClick={() => onItemClick(item)}
        >
          <Icon icon={sourceMeta.icon} />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0" onClick={() => onItemClick(item)} style={{ cursor: 'pointer' }}>
          <div className="text-xs font-medium leading-snug line-clamp-2" style={{ color: 'var(--text-primary)' }}>
            {displayTitle}
          </div>
          {showBilingual && item.title_zh && item.title !== item.title_zh && (
            <div className="text-xs mt-0.5 leading-snug line-clamp-1" style={{ color: 'var(--text-muted)' }}>
              {item.title}
            </div>
          )}
          {displaySummary && (
            <div className="text-xs mt-1 leading-relaxed line-clamp-2" style={{ color: 'var(--text-muted)' }}>
              {displaySummary}
            </div>
          )}
          <div className="flex items-center gap-2 mt-1.5">
            <span className="text-2xs px-1 py-0.5 rounded" style={{ background: `${sourceMeta.color}12`, color: sourceMeta.color }}>
              {sourceMeta.label}
            </span>
            <span className="text-2xs px-1 py-0.5 rounded" style={{ background: `${catMeta.color}12`, color: catMeta.color }}>
              {catMeta.label}
            </span>
            {item.tags.length > 0 && (
              <span className="text-2xs truncate max-w-[120px]" style={{ color: 'var(--text-muted)' }}>
                #{item.tags.slice(0, 3).join(' #')}
              </span>
            )}
            {item.score > 0 && (
              <span className="text-2xs flex items-center gap-0.5" style={{ color: 'var(--text-muted)' }}>
                <Icon icon="fa-regular fa-arrow-up" /> {item.score}
              </span>
            )}
            <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
              {item.publishedAt > 0
                ? new Date(item.publishedAt).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
                : ''}
            </span>
            {/* Bookmark button */}
            {onToggleBookmark && (
              <button
                onClick={e => { e.stopPropagation(); onToggleBookmark(item) }}
                className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity text-2xs flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-white/[0.08]"
                style={{ color: savedUrls?.has(item.url) ? '#eab308' : 'var(--text-muted)' }}
                title={savedUrls?.has(item.url) ? t('infoFeed.bookmarkRemove') : t('infoFeed.bookmarkAdd')}
              >
                <Icon icon={`${savedUrls?.has(item.url) ? 'fa-solid' : 'fa-regular'} fa-bookmark text-[8px]`} />
              </button>
            )}
            {/* Direct link */}
            {item.url && (
              <a
                href={item.url}
                target="_blank"
                rel="noopener noreferrer"
                className="opacity-0 group-hover:opacity-100 transition-opacity text-2xs flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-white/[0.08]"
                style={{ color: 'var(--text-muted)' }}
                onClick={e => e.stopPropagation()}
                title={t('infoFeed.openOriginal')}
              >
                <Icon icon="fa-solid fa-arrow-up-right-from-square text-[8px]" />
              </a>
            )}
          </div>

          {/* ── AI 摘要 button + inline result ── */}
          <div className="mt-1.5">
            {summaries[item.id]?.text ? (
              <div
                className="text-xs leading-relaxed px-2.5 py-1.5 rounded-lg cursor-default"
                style={{ background: 'var(--blue-bg-faint)', color: 'var(--text-secondary)', border: '1px solid var(--blue-bg)' }}
                onClick={e => e.stopPropagation()}
              >
                <div className="text-2xs font-medium mb-0.5" style={{ color: 'var(--brand)' }}>{t('infoFeed.aiInterpret')}</div>
                {summaries[item.id].text}
              </div>
            ) : summaries[item.id]?.loading ? (
              <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--brand)' }} onClick={e => e.stopPropagation()}>
                <div className="w-2.5 h-2.5 border-2 border-white/20 border-t-blue-400 rounded-full animate-spin" />
                {t('infoFeed.aiInterpreting')}
              </div>
            ) : summaries[item.id]?.error ? (
              <div className="text-2xs" style={{ color: 'var(--danger)' }}>
                {summaries[item.id].error}
              </div>
            ) : null}
            {!summaries[item.id]?.text && !summaries[item.id]?.loading && (
              <button
                onClick={e => { e.stopPropagation(); handleSummarize(item) }}
                className="text-2xs flex items-center gap-1 px-2 py-0.5 rounded transition-all hover:bg-white/[0.06]"
                style={{ color: 'var(--text-muted)' }}
              >
                <Icon icon="fa-solid fa-wand-magic-sparkles text-[8px]" />
                {t('infoFeed.aiSummary')}
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 gap-2 text-center">
        <div className="text-xl opacity-20"><Icon icon="fa-regular fa-newspaper" /></div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('infoFeed.empty')}</div>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Display toggles */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => setBilingual(!bilingual)}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-2xs transition-all border"
          style={{
            background: bilingual ? 'var(--blue-bg)' : 'transparent',
            borderColor: bilingual ? 'var(--blue-border)' : 'var(--panel-border)',
            color: bilingual ? 'var(--brand)' : 'var(--text-muted)',
          }}
        >
          <Icon icon="fa-solid fa-language text-[8px]" />
          {t('infoFeed.bilingual')}
        </button>
        <button
          onClick={() => setGroupByCategory(!groupByCategory)}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-2xs transition-all border"
          style={{
            background: groupByCategory ? 'var(--purple-bg)' : 'transparent',
            borderColor: groupByCategory ? 'var(--purple-border)' : 'var(--panel-border)',
            color: groupByCategory ? '#a855f7' : 'var(--text-muted)',
          }}
        >
          <Icon icon="fa-solid fa-layer-group text-[8px]" />
          {t('infoFeed.groupByCategory')}
        </button>
        <button
          onClick={handleTogglePersonalized}
          disabled={personalizing}
          className="flex items-center gap-1 px-2 py-0.5 rounded text-2xs transition-all border disabled:opacity-50"
          style={{
            background: personalized ? 'var(--green-bg)' : 'transparent',
            borderColor: personalized ? 'var(--green-border)' : 'var(--panel-border)',
            color: personalized ? 'var(--success)' : 'var(--text-muted)',
          }}
        >
          {personalizing ? (
            <div className="w-2.5 h-2.5 border-2 border-white/20 border-t-green-400 rounded-full animate-spin" />
          ) : (
            <Icon icon={`${personalized ? 'fa-solid' : 'fa-regular'} fa-brain text-[8px]`} />
          )}
          {t('infoFeed.personalized')}
        </button>
      </div>

      {/* Search box */}
      <div className="relative">
        <Icon icon="fa-solid fa-search absolute left-2.5 top-1/2 -translate-y-1/2 text-xs" style={{ color: 'var(--text-muted)' }} />
        <input
          type="text"
          value={searchQuery}
          onChange={e => setSearchQuery(e.target.value)}
          placeholder={t('infoFeed.searchPlaceholder')}
          className="w-full pl-7 pr-3 py-1.5 rounded-lg text-xs border outline-none transition-all"
          style={{
            background: 'var(--overlay-faint)',
            borderColor: searchQuery ? 'var(--blue-border)' : 'var(--panel-border)',
            color: 'var(--text-primary)',
          }}
        />
        {searchQuery && (
          <button
            onClick={() => setSearchQuery('')}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-xs hover:text-text-secondary"
            style={{ color: 'var(--text-muted)' }}
          >
            <Icon icon="fa-solid fa-xmark" />
          </button>
        )}
      </div>

      {/* Category filter chips */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setFilterCategory(null)}
          className="px-2.5 py-1 rounded-lg text-xs font-medium transition-all border"
          style={{
            background: !filterCategory ? 'var(--blue-bg-strong)' : 'transparent',
            borderColor: !filterCategory ? 'var(--blue-border)' : 'var(--panel-border)',
            color: !filterCategory ? 'var(--brand)' : 'var(--text-muted)',
          }}
        >
          {t('infoFeed.allCountFmt', { count: items.length })}
        </button>
        {Object.entries(categoryCounts).map(([cat, counts]) => {
          const meta = CATEGORY_META[cat] || { label: cat, icon: 'fa-solid fa-globe', color: '#6b7280' }
          const active = filterCategory === cat
          const label = cat === 'local' && userCity ? t('infoFeed.cityLocalFmt', { city: userCity }) : meta.label
          return (
            <button
              key={cat}
              onClick={() => setFilterCategory(active ? null : cat)}
              className="flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-all border"
              style={{
                background: active ? `${meta.color}18` : 'transparent',
                borderColor: active ? `${meta.color}40` : 'var(--panel-border)',
                color: active ? meta.color : 'var(--text-muted)',
              }}
            >
              <Icon icon={`${meta.icon} text-2xs`} />
              {label} ({counts.total})
            </button>
          )
        })}
      </div>

      {/* Source filter chips */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => onFilterSource(null)}
          className="px-2 py-1 rounded text-2xs font-medium transition-all border"
          style={{
            background: !filterSource ? 'var(--overlay-soft)' : 'transparent',
            borderColor: !filterSource ? 'var(--panel-border)' : 'transparent',
            color: !filterSource ? 'var(--text-secondary)' : 'var(--text-muted)',
          }}
        >
          {t('infoFeed.allSources')}
        </button>
        {Object.entries(sourceCounts).map(([source, counts]) => {
          const meta = SOURCE_META[source] || { label: source, icon: 'fa-regular fa-circle', color: '#888' }
          const active = filterSource === source
          return (
            <button
              key={source}
              onClick={() => onFilterSource(active ? null : source)}
              className="flex items-center gap-1 px-2 py-1 rounded text-2xs font-medium transition-all border"
              style={{
                background: active ? `${meta.color}18` : 'transparent',
                borderColor: active ? `${meta.color}40` : 'transparent',
                color: active ? meta.color : 'var(--text-muted)',
              }}
            >
              <Icon icon={`${meta.icon} text-[8px]`} />
              {meta.label} ({counts.total})
            </button>
          )
        })}
      </div>

      {/* Feed items */}
      <div className="space-y-1.5">
        {groupByCategory
          ? grouped.map(({ cat, items: catItems }) => {
              const catMeta = CATEGORY_META[cat] || CATEGORY_META['general']
              return (
                <div key={cat} className="space-y-1">
                  <div className="flex items-center gap-2 px-1 py-1">
                    <Icon icon={`${catMeta.icon} text-xs`} style={{ color: catMeta.color }} />
                    <span className="text-xs font-medium" style={{ color: catMeta.color }}>{catMeta.label}</span>
                    <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                  {t('infoFeed.countItemsFmt', { count: catItems.length })}
                  {sorted.filter(it => (it.category || 'general') === cat).length > 5 && (
                    <span className="ml-0.5 opacity-50">{t('infoFeed.totalItemsFmt', { count: sorted.filter(it => (it.category || 'general') === cat).length })}</span>
                  )}
                </span>
                  </div>
                  {catItems.map(it => renderFeedItem(it))}
                </div>
              )
            })
          : sorted.map(it => renderFeedItem(it))
        }
      </div>
    </div>
  )
}

export default InfoFeedView
