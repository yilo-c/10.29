import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useStore } from '@desk-notes/shared'
import { haptic, touchScale } from '../../../lib/haptic'
import {
  type ArticleItem,
  loadArticles,
  mergeArticles,
  loadBookmarks,
  toggleBookmark as storeToggle,
  isBookmarked,
  markRead,
  loadReadSet,
  cacheArticleContent,
  getCachedContent,
} from '../../../lib/mobileArticleStore'
import { collectAll } from '../../../lib/mobileInfoCollector'
import { syncBookmark, fullSync } from '../../../lib/mobileBookmarkSync'
import { isAiConfigured, translateText, summarizeText, speakText, generateBrief } from '../../../lib/mobileAiUtils'
import {
  fetchBrief,
  fetchItems,
  fetchCachedItems,
  fetchPageTitle,
  getSectionKeywords,
  setSectionKeywords,
} from '../../../lib/bridges/infoBridge'
import { tokenManager } from '../../../lib/tokenManager'
import { swallowError } from '../../../lib/swallowError'

const CATEGORY_LABELS: Record<string, string> = {
  ai: 'AI',
  dev: '技术',
  tool: '工具',
  internet: '互联网',
  domestic: '国内',
  international: '国际',
  local: '同城',
  general: '综合',
  policy: '政策',
  science: '科技突破',
}

const CATEGORY_ORDER = ['ai', 'dev', 'tool', 'internet', 'domestic', 'international', 'local']

const SOURCE_LABELS: Record<string, { label: string; color: string }> = {
  hackernews: { label: 'HN', color: '#ff6600' },
  v2ex: { label: 'V2EX', color: '#a0a0a0' },
  juejin: { label: '掘金', color: '#1e80ff' },
  github: { label: 'GitHub', color: '#6e40c9' },
  '36kr': { label: '36氪', color: '#3b82f6' },
  thepaper: { label: '澎湃', color: '#e03131' },
}

function keywordScore(item: ArticleItem, kws: Record<string, string[]>): number {
  const cat = item.category || 'general'
  let catsToCheck = [cat]
  // tool items also match dev keywords
  if (cat === 'tool') catsToCheck = ['tool', 'dev']
  let score = 0
  const text = ((item.title || '') + ' ' + (item.tags || []).join(' ')).toLowerCase()
  for (const c of catsToCheck) {
    const words = kws[c]
    if (!words) continue
    for (const w of words) {
      if (text.includes(w.toLowerCase())) score += 2
    }
  }
  return score
}

function formatTime(ts: number): string {
  if (!ts) return ''
  const now = Date.now()
  const diff = now - ts
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`
  return new Date(ts).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

export default function InfoView() {
  const sectionKeywords = useStore(s => s.infoSectionKeywords) as Record<string, string[]>
  const setInfoSectionKeywords = useStore(s => s.setInfoSectionKeywords)
  const prevCountRef = useRef(0)
  const [articles, setArticles] = useState<ArticleItem[]>(() => loadArticles())
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [filter, setFilter] = useState<string | null>(null)
  const [bookmarks, setBookmarks] = useState<ArticleItem[]>(() => loadBookmarks())
  const [showBookmarks, setShowBookmarks] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [readSet, setReadSet] = useState<Set<string>>(() => loadReadSet())
    const [aiResults, setAiResults] = useState<Record<string, { type: string; text: string; loading?: boolean }>>({})
  const [showImport, setShowImport] = useState(false)
  const [importUrl, setImportUrl] = useState('')
  const [importing, setImporting] = useState(false)
  const [brief, setBrief] = useState<string | null>(() => {
    const cached = localStorage.getItem('siyi-brief')
    if (cached) {
      try { const parsed = JSON.parse(cached); if (Date.now() - parsed.time < 3600000) return parsed.text } catch { console.warn('[info] invalid brief cache') }
    }
    return null
  })
  const [briefLoading, setBriefLoading] = useState(false)
  const [briefCollapsed, setBriefCollapsed] = useState(false)
  const briefLoadingRef = useRef(false)
  const [localKeywords, setLocalKeywords] = useState<Record<string, string[]>>(() => ({ ...sectionKeywords }))
  const [localInputs, setLocalInputs] = useState<Record<string, string>>({})
  const [showSectionConfig, setShowSectionConfig] = useState(false)

  const refresh = useCallback(async (background = false) => {
    if (background) {
      setRefreshing(true)
    } else {
      setLoading(true)
    }
    const prevCount = prevCountRef.current
    setError(null)
    try {
      let remoteItems: ArticleItem[] | null = null
      let remoteBrief: string | null = null
      let remoteErrors: { source: string; error: string }[] | null = null

      // 优先走桌面端 RPC；若桌面未配对或 RPC 失败，回退到本地采集。
      if (tokenManager.isPaired) {
        try {
          const cached = await fetchCachedItems()
          if (cached?.items && cached.items.length > 0) {
            remoteItems = cached.items as ArticleItem[]
            remoteBrief = cached.brief || null
          }
          // 前台/后台都优先快速采集条目（no_ai），避免用户等待 AI 简报生成。
          // AI 简报由用户主动点击「生成今日 AI 简报」触发，或在后台自动过期刷新。
          const rpc = await fetchItems()
          if (rpc?.items && rpc.items.length > 0) {
            remoteItems = rpc.items as ArticleItem[]
            remoteBrief = rpc.brief || null
            remoteErrors = rpc.errors || null
          }

          // 后台且缓存简报过期时，异步触发完整采集+AI简报生成
          if (background && remoteItems && remoteItems.length > 0) {
            const briefCache = localStorage.getItem('siyi-brief')
            let briefStale = true
            if (briefCache) {
              try { const parsed = JSON.parse(briefCache); if (Date.now() - parsed.time < 3600000) briefStale = false } catch { console.warn('[info] invalid brief cache') }
            }
            if (briefStale && !briefLoadingRef.current) {
              briefLoadingRef.current = true; setBriefLoading(true)
              fetchBrief().then(briefRpc => {
                if (briefRpc?.brief) {
                  setBrief(briefRpc.brief)
                  setBriefCollapsed(false)
                  localStorage.setItem('siyi-brief', JSON.stringify({ text: briefRpc.brief, time: Date.now() }))
                }
              }).catch(() => {}).finally(() => { briefLoadingRef.current = false; setBriefLoading(false) })
            }
          }
        } catch (e) {
          swallowError('InfoView:desktopRefresh', e)
        }
      }

      if (remoteItems && remoteItems.length > 0) {
        const merged = mergeArticles(remoteItems)
        const newCount = Math.max(0, merged.length - prevCountRef.current)
        prevCountRef.current = merged.length
        setArticles(merged)
        if (remoteBrief) {
          setBrief(remoteBrief)
          setBriefCollapsed(false)
          localStorage.setItem('siyi-brief', JSON.stringify({ text: remoteBrief, time: Date.now() }))
        }
        if (remoteErrors && remoteErrors.length > 0) {
          const failedCount = remoteErrors.length
          setError(failedCount >= 4 ? `${failedCount} 个桌面端源获取失败` : `${failedCount} 个源获取失败`)
        }
        // 后台刷新且发现新增条目时发出通知
        if (background && newCount > 0 && 'Notification' in window && Notification.permission === 'granted') {
          new Notification('📰 新资讯', { body: `新增 ${newCount} 条资讯`, silent: true })
        }
      } else {
        // 回退：本地采集（离线或无桌面端时）
        const result = await collectAll()
        if (result.items.length > 0) {
          const merged = mergeArticles(result.items); prevCountRef.current = merged.length
          setArticles(merged)
          // Auto-generate brief in background if stale (> 1 hour)
          if (isAiConfigured() && merged.length > 0) {
            const cached = localStorage.getItem('siyi-brief')
            let stale = true
            if (cached) {
              try { const parsed = JSON.parse(cached); if (Date.now() - parsed.time < 3600000) stale = false } catch { console.warn('[info] invalid brief cache') }
            }
            if (stale && !briefLoadingRef.current) {
              briefLoadingRef.current = true; setBriefLoading(true)
              generateBrief(merged.slice(0, 30)).then(text => {
                setBrief(text)
                setBriefCollapsed(false)
                localStorage.setItem('siyi-brief', JSON.stringify({ text, time: Date.now() }))
              }).catch(() => {}).finally(() => { briefLoadingRef.current = false; setBriefLoading(false) })
            }
          }
        }
        if (result.errors.length > 0) {
          const failedCount = result.errors.length
          if (failedCount >= 4) {
            setError(`${failedCount}/${6} 个源获取失败，请检查网络`)
          } else {
            setError(`${failedCount} 个源获取失败`)
          }
        }
      }

      // Show notification for new articles (background refresh only)
      if (background && prevCount > 0 && articles.length > 0) {
        const newCount = articles.length - prevCount
        if (newCount > 0 && 'Notification' in window && Notification.permission === 'granted') {
          new Notification('📰 新资讯', {
            body: `新增 ${newCount} 条资讯`,
            silent: true,
          })
        }
      }
    } catch {
      setError('获取失败，请检查网络连接')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [articles.length])

  // Load cached on mount, then refresh + sync in background
  useEffect(() => {
    refresh(true)
    // Try bookmark sync with desktop (best-effort, non-blocking)
    fullSync()
    // 尝试从桌面端同步资讯板块关键词配置
    getSectionKeywords().then(kws => {
      if (kws && Object.keys(kws).length > 0) {
        setInfoSectionKeywords(kws)
        setLocalKeywords(kws)
      }
    }).catch(e => swallowError('InfoView:loadSectionKws', e))
  }, [refresh, setInfoSectionKeywords])

  const handleToggleBookmark = useCallback((item: ArticleItem) => {
    haptic.light()
    const added = storeToggle(item)
    setBookmarks(loadBookmarks())
    if (showBookmarks && !added) {
      setBookmarks(prev => prev.filter(b => b.url !== item.url))
    }
    // Cache article content for offline reading
    if (added) {
      const text = item.title + (item.summary ? '\n' + item.summary : '')
      cacheArticleContent(item.url, item.title, text)
    }
    // Sync to desktop (best-effort, doesn't block UI)
    syncBookmark(item)
  }, [showBookmarks])

  const handleOpenArticle = useCallback((item: ArticleItem) => {
    haptic.light()
    markRead(item.url)
    setReadSet(new Set(loadReadSet()))
    // Check for offline cached content
    const cached = getCachedContent(item.url)
    if (cached) {
      // If cached, show in-app (but still try to open URL for fresh version)
      window.open(item.url, '_blank')
    } else {
      window.open(item.url, '_blank')
    }
  }, [])

  const handleTranslate = useCallback(async (item: ArticleItem) => {
    const key = `${item.url}-translate`
    if (aiResults[key]?.text) return
    setAiResults(prev => ({ ...prev, [key]: { type: 'translate', text: '', loading: true } }))
    try {
      const text = await translateText(item.title + (item.summary ? '\n' + item.summary : ''))
      setAiResults(prev => ({ ...prev, [key]: { type: 'translate', text, loading: false } }))
    } catch {
      setAiResults(prev => ({ ...prev, [key]: { type: 'translate', text: '翻译失败（请配置 AI）', loading: false } }))
    }
  }, [aiResults])

  const handleGenerateBrief = useCallback(async () => {
    if (articles.length === 0 || briefLoadingRef.current) return
    haptic.light()
    briefLoadingRef.current = true; setBriefLoading(true)
    try {
      // 优先使用桌面端生成的简报（含更多源和更好的分类）
      if (tokenManager.isPaired) {
        const res = await fetchBrief()
        if (res?.brief) {
          setBrief(res.brief)
          setBriefCollapsed(false)
          localStorage.setItem('siyi-brief', JSON.stringify({ text: res.brief, time: Date.now() }))
          return
        }
      }
      // 回退到本地云 AI 生成
      if (isAiConfigured()) {
        const text = await generateBrief(articles)
        setBrief(text)
        setBriefCollapsed(false)
        localStorage.setItem('siyi-brief', JSON.stringify({ text, time: Date.now() }))
      } else {
        setBrief('未配置 AI，无法生成简报')
      }
    } catch { setBrief('AI 简报生成失败') }
    finally { briefLoadingRef.current = false; setBriefLoading(false) }
  }, [articles])

  const handleImportUrl = useCallback(async () => {
    const url = importUrl.trim()
    if (!url || importing) return
    if (!/^https?:\/\/[^\s]+/.test(url)) return
    haptic.light()
    setImporting(true)
    try {
      // 优先走桌面端代理绕过 CORS；失败则回退到本地直接 fetch
      let title = url
      let summary = ''
      const proxy = await fetchPageTitle(url)
      if (proxy?.title) {
        title = proxy.title
        summary = proxy.summary || ''
      } else {
        const local = await import('../../../lib/mobileInfoCollector').then(m => m.fetchPageTitle(url))
        title = local.title || url
        summary = local.summary || ''
      }
      const item: ArticleItem = {
        id: `import-${Date.now()}`,
        title,
        url,
        source: 'web',
        summary,
        tags: [],
        score: 0,
        publishedAt: Date.now(),
        savedAt: Date.now(),
        category: 'general',
      }
      storeToggle(item)
      setBookmarks(loadBookmarks())
      setShowImport(false)
      setImportUrl('')
      syncBookmark(item)
    } catch (e) { /* ignore */ swallowError('InfoView:240', e) }
    finally { setImporting(false) }
  }, [importUrl, importing])

  const handleSummarize = useCallback(async (item: ArticleItem) => {
    const key = `${item.url}-summarize`
    if (aiResults[key]?.text) return
    setAiResults(prev => ({ ...prev, [key]: { type: 'summarize', text: '', loading: true } }))
    try {
      const text = await summarizeText(item.title + (item.summary ? '\n' + item.summary : ''))
      setAiResults(prev => ({ ...prev, [key]: { type: 'summarize', text, loading: false } }))
    } catch {
      setAiResults(prev => ({ ...prev, [key]: { type: 'summarize', text: '摘要失败（请配置 AI）', loading: false } }))
    }
  }, [aiResults])

  // Keyword-boosted sorting: articles with matching section keywords rank higher
  const scored = useMemo(() => {
    if (!sectionKeywords || Object.keys(sectionKeywords).length === 0) return articles
    return [...articles].sort((a, b) => {
      const scoreA = keywordScore(a, sectionKeywords)
      const scoreB = keywordScore(b, sectionKeywords)
      return scoreB - scoreA
    })
  }, [articles, sectionKeywords])

  const displayItems: ArticleItem[] = showBookmarks
    ? bookmarks
    : filter
      ? scored.filter(a => (a.category || 'general') === filter)
      : scored

  const catCounts: Record<string, number> = {}
  for (const a of articles) {
    const cat = a.category || 'general'
    catCounts[cat] = (catCounts[cat] || 0) + 1
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Header */}
      <div style={{
        padding: 'var(--safe-top) var(--space-4) var(--space-2)',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <h1 style={{
          fontSize: 'var(--text-large-title)', fontWeight: 700,
          color: 'var(--text-primary)', margin: 0,
        }}>
          资讯
        </h1>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {/* Refresh spinner */}
          {(loading || refreshing) && (
            <span style={{
              display: 'inline-block', width: 14, height: 14,
              border: '2px solid var(--border-subtle)',
              borderTopColor: 'var(--accent)',
              borderRadius: '50%',
              animation: 'spin 0.6s linear infinite',
              marginRight: 4,
            }} />
          )}
          <button
            onClick={() => { haptic.light(); setShowBookmarks(false) }}
            style={{
              padding: '6px 12px', borderRadius: 'var(--radius-sm)',
              border: 'none', fontSize: 'var(--text-footnote)',
              fontWeight: showBookmarks ? 400 : 600,
              background: showBookmarks ? 'transparent' : 'var(--accent-surface)',
              color: showBookmarks ? 'var(--text-tertiary)' : 'var(--accent)',
            }}
          >
            发现
          </button>
          <button
            onClick={() => { haptic.light(); setShowBookmarks(true) }}
            style={{
              padding: '6px 12px', borderRadius: 'var(--radius-sm)',
              border: 'none', fontSize: 'var(--text-footnote)',
              fontWeight: showBookmarks ? 600 : 400,
              background: showBookmarks ? 'var(--accent-surface)' : 'transparent',
              color: showBookmarks ? 'var(--accent)' : 'var(--text-tertiary)',
            }}
          >
            收藏 {bookmarks.length > 0 && `(${bookmarks.length})`}
          </button>
          <button
            onClick={() => { haptic.light(); setShowImport(true) }}
            style={{
              padding: '6px 8px', borderRadius: 'var(--radius-sm)',
              border: 'none', fontSize: 'var(--text-caption-1)',
              background: 'transparent', color: 'var(--text-tertiary)',
            }}
            title="导入链接"
          >
            🔗
          </button>
          <button
            onClick={() => { haptic.light(); setShowSectionConfig(true) }}
            style={{
              padding: '6px 8px', borderRadius: 'var(--radius-sm)',
              border: 'none', fontSize: 'var(--text-caption-1)',
              background: 'transparent', color: 'var(--text-tertiary)',
            }}
            title="板块关键词"
          >
            ⚙️
          </button>
        </div>
      </div>

      {/* URL import dialog */}
      {showImport && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 1000,
          display: 'flex', alignItems: 'flex-end', justifyContent: 'center',
          background: 'rgba(0,0,0,0.4)',
        }} onClick={() => setShowImport(false)}>
          <div style={{
            width: '100%', maxWidth: 400,
            padding: 'var(--space-4)', paddingBottom: 'calc(var(--space-4) + env(safe-area-inset-bottom))',
            background: 'var(--bg-secondary)',
            borderTopLeftRadius: 'var(--radius-lg)',
            borderTopRightRadius: 'var(--radius-lg)',
          }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 'var(--text-headline)', fontWeight: 600, marginBottom: 12, color: 'var(--text-primary)' }}>
              导入链接
            </div>
            <input
              autoFocus
              value={importUrl}
              onChange={e => setImportUrl(e.target.value)}
              placeholder="粘贴链接地址..."
              style={{
                width: '100%', padding: '10px 12px', borderRadius: 'var(--radius-sm)',
                border: '0.5px solid var(--border-default)', outline: 'none',
                fontSize: 'var(--text-body)', background: 'var(--bg-primary)',
                color: 'var(--text-primary)', boxSizing: 'border-box',
              }}
            />
            <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
              <button
                onClick={() => { setShowImport(false); setImportUrl('') }}
                style={{
                  flex: 1, padding: '10px', borderRadius: 'var(--radius-sm)',
                  border: 'none', fontSize: 'var(--text-body)',
                  background: 'var(--bg-tertiary)', color: 'var(--text-secondary)',
                }}
              >
                取消
              </button>
              <button
                onClick={handleImportUrl}
                disabled={!importUrl.trim() || importing}
                style={{
                  flex: 1, padding: '10px', borderRadius: 'var(--radius-sm)',
                  border: 'none', fontSize: 'var(--text-body)',
                  background: 'var(--accent)', color: '#fff',
                  fontWeight: 600, opacity: !importUrl.trim() || importing ? 0.5 : 1,
                }}
              >
                {importing ? '导入中…' : '收藏'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Brief section — 7-section AI brief */}
      {!showBookmarks && (
        <>
          {!brief && articles.length > 0 && (
            <div style={{ padding: '0 var(--space-4) var(--space-2)' }}>
              <button onClick={handleGenerateBrief} disabled={briefLoading}
                style={{ width: '100%', padding: '12px 0', borderRadius: 'var(--radius-sm)', border: '0.5px dashed var(--border-default)', background: 'var(--bg-secondary)', color: 'var(--accent)', fontSize: 'var(--text-footnote)', fontWeight: 500, cursor: 'pointer', opacity: briefLoading ? 0.5 : 1 }}>
                {briefLoading ? '🤖 AI 生成中…' : '📰 生成今日 AI 简报'}
              </button>
            </div>
          )}
          {brief && (
            <div style={{ margin: '0 var(--space-4) var(--space-3)', borderRadius: 'var(--radius-md)', background: 'var(--bg-secondary)', border: '0.5px solid var(--border-subtle)', overflow: 'hidden' }}>
              <button onClick={() => setBriefCollapsed(!briefCollapsed)}
                style={{ width: '100%', display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 14px', background: 'none', border: 'none', color: 'var(--text-primary)', fontSize: 'var(--text-footnote)', fontWeight: 600, cursor: 'pointer' }}>
                <span>📰 今日 AI 简报</span>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <button onClick={e => { e.stopPropagation(); handleGenerateBrief() }}
                    disabled={briefLoading} style={{ background: 'none', border: 'none', color: 'var(--accent)', fontSize: 10, cursor: 'pointer' }}>
                    🔄
                  </button>
                  <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--text-caption-2)' }}>{briefCollapsed ? '展开 ▾' : '收起 ▴'}</span>
                </div>
              </button>
              {!briefCollapsed && (
                <div style={{ padding: '0 14px 14px', fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)', lineHeight: 1.7, whiteSpace: 'pre-wrap', maxHeight: '50vh', overflowY: 'auto' }}>
                  {brief}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Category filter (only in discover mode) */}
      {!showBookmarks && articles.length > 0 && (
        <div style={{
          display: 'flex', gap: 6, overflowX: 'auto',
          padding: '0 var(--space-4) var(--space-2)',
          WebkitOverflowScrolling: 'touch',
          scrollbarWidth: 'none',
        }}>
          <button
            onClick={() => setFilter(null)}
            style={{
              padding: '4px 10px', borderRadius: 'var(--radius-full)',
              border: '0.5px solid var(--border-subtle)',
              fontSize: 'var(--text-caption-1)', whiteSpace: 'nowrap',
              background: !filter ? 'var(--accent-surface)' : 'transparent',
              color: !filter ? 'var(--accent)' : 'var(--text-secondary)',
              fontWeight: !filter ? 600 : 400, flexShrink: 0,
            }}
          >
            全部 ({articles.length})
          </button>
          {CATEGORY_ORDER.filter(c => catCounts[c]).map(cat => (
            <button
              key={cat}
              onClick={() => setFilter(filter === cat ? null : cat)}
              style={{
                padding: '4px 10px', borderRadius: 'var(--radius-full)',
                border: '0.5px solid var(--border-subtle)',
                fontSize: 'var(--text-caption-1)', whiteSpace: 'nowrap',
                background: filter === cat ? 'var(--accent-surface)' : 'transparent',
                color: filter === cat ? 'var(--accent)' : 'var(--text-secondary)',
                fontWeight: filter === cat ? 600 : 400, flexShrink: 0,
              }}
            >
              {CATEGORY_LABELS[cat] || cat} ({catCounts[cat]})
            </button>
          ))}
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div style={{
          margin: '0 var(--space-4) var(--space-2)',
          padding: '8px 12px', borderRadius: 'var(--radius-sm)',
          background: 'var(--tone-caution)',
          color: '#fff',
          fontSize: 'var(--text-caption-1)',
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        }}>
          <span>{error}</span>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button
              onClick={() => { refresh() }}
              style={{ background: 'rgba(255,255,255,0.2)', border: 'none', color: '#fff', borderRadius: 4, padding: '3px 8px', fontSize: 11, fontWeight: 600 }}
            >
              重试
            </button>
            <button onClick={() => setError(null)} style={{ background: 'none', border: 'none', color: '#fff', fontSize: 16 }}>✕</button>
          </div>
        </div>
      )}

      {/* Article list */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '0 var(--space-4)' }}>
        {displayItems.length === 0 && !loading && (
          <div style={{
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            justifyContent: 'center', padding: '60px 20px', gap: 8,
          }}>
            <span style={{ fontSize: 32, opacity: 0.3 }}>📰</span>
            <span style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)' }}>
              {showBookmarks ? '暂无收藏' : '暂无资讯'}
            </span>
            {!showBookmarks && (
              <button
                onClick={() => refresh()}
                style={{
                  marginTop: 8, padding: '8px 20px', borderRadius: 'var(--radius-sm)',
                  border: 'none', fontSize: 'var(--text-footnote)',
                  background: 'var(--accent)', color: '#fff', fontWeight: 600,
                }}
              >
                获取资讯
              </button>
            )}
          </div>
        )}

        {displayItems.map((item) => {
          const sourceMeta = SOURCE_LABELS[item.source] || { label: item.source, color: '#888' }
          const saved = isBookmarked(item.url)
          const isRead = readSet.has(item.url)

          return (
            <div
              key={`${item.source}-${item.id}`}
              onClick={() => handleOpenArticle(item)}
              onTouchStart={touchScale.onStart}
              onTouchEnd={touchScale.onEnd}
              onMouseDown={touchScale.onStart}
              onMouseUp={touchScale.onEnd}
              style={{
                display: 'flex', alignItems: 'flex-start', gap: 10,
                padding: '10px 0',
                borderBottom: '0.5px solid var(--border-subtle)',
                cursor: 'pointer', minHeight: 52,
                WebkitTapHighlightColor: 'transparent',
                userSelect: 'none',
                opacity: isRead ? 0.5 : 1,
              }}
            >
              {/* Source indicator dot */}
              <div style={{
                width: 6, height: 6, borderRadius: '50%',
                background: sourceMeta.color,
                marginTop: 7, flexShrink: 0,
              }} />

              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 'var(--text-footnote)',
                  fontWeight: isRead ? 400 : 500,
                  color: isRead ? 'var(--text-tertiary)' : 'var(--text-primary)',
                  lineHeight: 1.4,
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }}>
                  {item.title}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4 }}>
                  <span style={{
                    fontSize: 'var(--text-caption-2)',
                    color: sourceMeta.color, fontWeight: 500,
                  }}>
                    {sourceMeta.label}
                  </span>
                  {(item.score ?? 0) > 0 && (
                    <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
                      ↑{item.score}
                    </span>
                  )}
                  <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
                    {formatTime(item.publishedAt ?? 0)}
                  </span>
                </div>

                {/* Action buttons — visible in bookmarks view when AI configured */}
                {showBookmarks && isAiConfigured() && (
                  <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                    <button
                      onClick={e => { e.stopPropagation(); speakText(item.title) }}
                      style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: 12, color: 'var(--text-tertiary)' }}
                      title="朗读"
                    >
                      🔊
                    </button>
                    <button
                      onClick={e => { e.stopPropagation(); handleTranslate(item) }}
                      style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: 12, color: 'var(--text-tertiary)' }}
                      title="翻译"
                    >
                      🌐
                    </button>
                    <button
                      onClick={e => { e.stopPropagation(); handleSummarize(item) }}
                      style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: 12, color: 'var(--text-tertiary)' }}
                      title="AI 摘要"
                    >
                      🤖
                    </button>
                    <button
                      onClick={e => {
                        e.stopPropagation()
                        if (navigator.share) {
                          navigator.share({ title: item.title, url: item.url }).catch(() => {})
                        }
                      }}
                      style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontSize: 12, color: 'var(--text-tertiary)' }}
                      title="分享"
                    >
                      📤
                    </button>
                    {/* AI result display */}
                    {aiResults[`${item.url}-translate`]?.loading && (
                      <span style={{ fontSize: 10, color: 'var(--accent)' }}>翻译中…</span>
                    )}
                    {aiResults[`${item.url}-summarize`]?.loading && (
                      <span style={{ fontSize: 10, color: 'var(--accent)' }}>摘要中…</span>
                    )}
                  </div>
                )}

                {/* AI result text */}
                {showBookmarks && (
                  <>
                    {aiResults[`${item.url}-translate`]?.text && !aiResults[`${item.url}-translate`]?.loading && (
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.4 }}>
                        🌐 {aiResults[`${item.url}-translate`].text.slice(0, 500)}
                      </div>
                    )}
                    {aiResults[`${item.url}-summarize`]?.text && !aiResults[`${item.url}-summarize`]?.loading && (
                      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.4 }}>
                        🤖 {aiResults[`${item.url}-summarize`].text.slice(0, 200)}
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Bookmark button */}
              <button
                onClick={e => { e.stopPropagation(); handleToggleBookmark(item) }}
                onTouchStart={e => e.stopPropagation()}
                onMouseDown={e => e.stopPropagation()}
                style={{
                  background: 'none', border: 'none', padding: 4,
                  cursor: 'pointer', flexShrink: 0,
                  color: saved ? 'var(--tone-caution)' : 'var(--text-tertiary)',
                  fontSize: 16, lineHeight: 1,
                  WebkitTapHighlightColor: 'transparent',
                }}
                aria-label={saved ? '取消收藏' : '收藏'}
              >
                {saved ? '★' : '☆'}
              </button>
            </div>
          )
        })}

        {/* Refresh button at bottom */}
        {!loading && displayItems.length > 0 && (
          <div style={{ textAlign: 'center', padding: 'var(--space-4) 0 var(--space-8)' }}>
            <button
              onClick={() => refresh()}
              disabled={refreshing}
              style={{
                padding: '8px 24px', borderRadius: 'var(--radius-full)',
                border: '0.5px solid var(--border-subtle)',
                fontSize: 'var(--text-footnote)',
                background: 'var(--bg-secondary)',
                color: refreshing ? 'var(--text-tertiary)' : 'var(--text-secondary)',
                cursor: refreshing ? 'default' : 'pointer',
              }}
            >
              {refreshing ? '刷新中…' : '刷新'}
            </button>
          </div>
        )}
      </div>

      {/* ── Section keyword config sheet ── */}
      {showSectionConfig && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 2000, display: 'flex', alignItems: 'flex-end', background: 'rgba(0,0,0,0.4)' }}
          onClick={() => setShowSectionConfig(false)}>
          <div style={{ width: '100%', maxWidth: 400, maxHeight: '70vh', display: 'flex', flexDirection: 'column', padding: 'var(--space-4)', paddingBottom: 'calc(var(--space-4) + env(safe-area-inset-bottom))', background: 'var(--bg-secondary)', borderTopLeftRadius: 'var(--radius-lg)', borderTopRightRadius: 'var(--radius-lg)' }}
            onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 'var(--text-headline)', fontWeight: 600, marginBottom: 12, color: 'var(--text-primary)' }}>板块关键词</div>
            <div style={{ flex: 1, overflowY: 'auto', marginBottom: 12 }}>
              {CATEGORY_ORDER.map(cat => {
                const label = CATEGORY_LABELS[cat] || cat
                const currentKws = localKeywords[cat] || []
                return (
                  <div key={cat} style={{ marginBottom: 16 }}>
                    <div style={{ fontSize: 'var(--text-footnote)', fontWeight: 600, color: 'var(--text-primary)', marginBottom: 6 }}>{label}</div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 4 }}>
                      {currentKws.map((kw, i) => (
                        <span key={i} style={{ padding: '2px 8px', borderRadius: 12, background: 'var(--accent-surface)', color: 'var(--accent)', fontSize: 'var(--text-caption-1)', display: 'flex', alignItems: 'center', gap: 4 }}>
                          {kw}
                          <span onClick={() => {
                            const next = { ...localKeywords, [cat]: currentKws.filter((_, j) => j !== i) }
                            setLocalKeywords(next)
                          }} style={{ cursor: 'pointer', opacity: 0.6 }}>×</span>
                        </span>
                      ))}
                    </div>
                    <input
                      placeholder="添加关键词，回车确认"
                      value={localInputs[cat] || ''}
                      onChange={e => setLocalInputs(prev => ({ ...prev, [cat]: e.target.value }))}
                      onKeyDown={e => {
                        if (e.key === 'Enter') {
                          const val = (e.target as HTMLInputElement).value.trim()
                          if (val) {
                            const next = { ...localKeywords, [cat]: [...(localKeywords[cat] || []), val] }
                            setLocalKeywords(next)
                          }
                          setLocalInputs(prev => ({ ...prev, [cat]: '' }))
                        }
                      }}
                      style={{ width: '100%', padding: '6px 10px', borderRadius: 'var(--radius-sm)', border: '0.5px solid var(--border-default)', outline: 'none', fontSize: 'var(--text-caption-1)', background: 'var(--bg-primary)', color: 'var(--text-primary)', boxSizing: 'border-box' }}
                    />
                  </div>
                )
              })}
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button onClick={async () => {
                setInfoSectionKeywords(localKeywords)
                // 同步到桌面端，失败时不阻塞本地保存
                setSectionKeywords(localKeywords).catch(e => swallowError('InfoView:saveSectionKws', e))
                setShowSectionConfig(false)
              }} style={{ flex: 1, padding: '10px 0', borderRadius: 'var(--radius-sm)', border: 'none', background: 'var(--accent)', color: '#fff', fontSize: 'var(--text-footnote)', fontWeight: 600, cursor: 'pointer' }}>保存</button>
              <button onClick={() => setShowSectionConfig(false)} style={{ flex: 1, padding: '10px 0', borderRadius: 'var(--radius-sm)', border: '0.5px solid var(--border-default)', background: 'transparent', color: 'var(--text-tertiary)', fontSize: 'var(--text-footnote)', cursor: 'pointer' }}>取消</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
