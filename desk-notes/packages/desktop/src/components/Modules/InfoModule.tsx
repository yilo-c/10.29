import React, { useState, useCallback, useEffect } from 'react'
import Icon from '../Common/Icon'

import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useStore } from '../../store/useStore'
import { getWsClient } from '../../utils/ai'
import { hardenSoftBreaks } from '../../utils/markdownHardBreaks'
import { useBackendEvents } from '../../FloatingBubble/useBackendEvents'
import type { RawSourceItem } from '@desk-notes/shared'
import InfoLibraryView from './InfoLibraryView'
import InfoDetailModal from './InfoDetailModal'
import InfoSectionConfig from './InfoSectionConfig'
import { swallowError } from '../../utils/swallowError'
import { useTranslation } from '../../i18n'
import { useInfoFetchStore, runInfoFetch, applyInfoCollected } from '../../store/infoFetchStore'
import Skeleton from '../Common/Skeleton'

const ei = window.electronAPI as ElectronAPI | undefined

interface SavedArticle { id: string; title: string; url: string; source: string; summary?: string; summary_zh?: string; tags?: string[]; category?: string; published_at?: string; saved_at?: string }
// RssFeed 字段以后端 rss.list / rss.subscribe 的返回为唯一真源
// （handlers/_rss.py → services/rss_subscriber.py:list_subscriptions）。
// 后端条目键：id / url / name / category_hint / feed_title /
// last_fetched_at / item_count / last_error。
interface RssFeed { id?: string; url: string; name?: string; category_hint?: string; feed_title?: string; last_fetched_at?: string; item_count?: number; last_error?: string }

type Tab = 'brief' | 'pocket' | 'library'

const InfoModule: React.FC = () => {
  const { t } = useTranslation()
  const infoBrief = useStore(s => s.infoBrief)
  const infoLoading = useStore(s => s.infoLoading)
  const infoLastFetch = useStore(s => s.infoLastFetch)
  const infoError = useStore(s => s.infoError)
  const setInfoBrief = useStore(s => s.setInfoBrief)
  const setInfoLastFetch = useStore(s => s.setInfoLastFetch)
  const setInfoError = useStore(s => s.setInfoError)
  const autoInfoEnabled = useStore(s => s.autoInfoEnabled)
  const autoInfoLastRun = useStore(s => s.autoInfoLastRun)
  const setAutoInfoEnabled = useStore(s => s.setAutoInfoEnabled)
  const setAutoInfoLastRun = useStore(s => s.setAutoInfoLastRun)
  const infoTopics = useStore(s => s.infoTopics)
  const setInfoTopics = useStore(s => s.setInfoTopics)
  // 关注领域编辑态（brief tab 筛选 chips）
  const [topicsEditing, setTopicsEditing] = useState(false)
  const [topicDraft, setTopicDraft] = useState('')

  const [tab, setTab] = useState<Tab>('brief')
  const [showSectionConfig, setShowSectionConfig] = useState(false)
  const [sectionRevision, setSectionRevision] = useState(0)

  // P6b：抓取状态提升为全局 store，切页不丢失
  const infoFetch = useInfoFetchStore()
  const fetchErrors = infoFetch.fetchErrors

  const [detailItem, setDetailItem] = useState<RawSourceItem | null>(null)

  // ── 稍后读 / RSS ──
  const [savedArticles, setSavedArticles] = useState<SavedArticle[]>([])
  const [pocketLoading, setPocketLoading] = useState(false)
  const [rssFeeds, setRssFeeds] = useState<RssFeed[]>([])
  const [rssUrl, setRssUrl] = useState('')
  const [rssSubscribing, setRssSubscribing] = useState(false)

  const wsClient = getWsClient()

  // ── Listen for backend-pushed info.collected events ──
  const { lastEvent } = useBackendEvents()
  useEffect(() => {
    const ev = lastEvent('info.collected')
    if (ev?.payload) {
      const data = ev.payload as { brief?: string; items?: RawSourceItem[] }
      if (data.brief) {
        setInfoBrief(data.brief)
        setInfoLastFetch(Date.now())
        setAutoInfoLastRun(Date.now())
        setTab('brief')
      }
      if (data.items) {
        applyInfoCollected(data)
      }
    }
  }, [lastEvent, setInfoBrief, setInfoLastFetch, setAutoInfoLastRun])

  // 挂载时回读后端缓存简报：组件若在抓取/简报生成期间卸载，会错过
  // info.collected 事件；回挂后从 info.cached_items 恢复最近一次简报，
  // 避免停留在"AI 未生成简报"占位文本。
  useEffect(() => {
    if (wsClient.status !== 'connected') return
    let cancelled = false
    // 2026-10-15（简报筛选）：回读持久化的关注领域（info_topics）——
    // 原先 infoTopics 永远是 uiSlice 默认值（AI/前端开发/…），用户无法设置。
    wsClient.call<{ value?: string[] }>('settings.get', { key: 'info_topics' })
      .then((r) => {
        if (cancelled) return
        const topics = r?.value
        if (Array.isArray(topics) && topics.length > 0) setInfoTopics(topics.map(String))
      })
      .catch(() => { /* 静默 */ })
    wsClient.call<{ brief?: string; items?: RawSourceItem[] }>('info.cached_items', {})
      .then((cached) => {
        if (cancelled) return
        if (cached?.brief) {
          setInfoBrief(cached.brief)
          setInfoLastFetch(Date.now())
        }
        if (cached?.items?.length) applyInfoCollected({ items: cached.items })
      })
      .catch(() => { /* 静默 */ })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 冷却剩余秒数：仅在点击「生成」时即时读取（cooldownRemaining 从不参与 JSX 渲染，
  // 无逐秒可见文本需要驱动）。旧实现用每秒 setInterval + nowTick 驱动整 InfoModule
  // body 逐秒重渲染，但它唯一消费方是 handleGenerate——纯属浪费。改为点击时直接从
  // Date.now 派生（比依赖上一 tick 的 stale 值更精确），彻底移除 interval。
  const cooldownRemaining = (cooldownUntil: number) =>
    Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000))

  const handleGenerate = useCallback(async () => {
    const remaining = cooldownRemaining(infoFetch.cooldownUntil)
    if (remaining > 0) {
      setInfoError(t('info.errCooldownFmt', { seconds: remaining }))
      return
    }
    // P6b：模块级运行器，组件卸载不中断抓取，结果写入全局 store
    void runInfoFetch('brief')
  }, [infoFetch.cooldownUntil, setInfoError, t])

  // 资讯页「抓取更新」：采集合并库，完成后刷新库列表
  const handleFetchLibrary = useCallback(async () => {
    await runInfoFetch('feed')
    setSectionRevision(r => r + 1)
  }, [])

  // ── 稍后读：加载已保存文章 + RSS 订阅 ──
  const loadPocket = useCallback(async () => {
    setPocketLoading(true)
    try {
      if (wsClient.status === 'connected') {
        const [articles, feeds] = await Promise.all([
          wsClient.call<{ articles: SavedArticle[] }>('reading.list_articles', {}),
          wsClient.call<{ feeds: RssFeed[] }>('rss.list', {}),
        ])
        const artList: SavedArticle[] = Array.isArray(articles?.articles) ? articles.articles : []
        setSavedArticles(artList)
        setRssFeeds(Array.isArray(feeds?.feeds) ? feeds.feeds : [])
      }
    } catch { /* 静默 */ } finally {
      setPocketLoading(false)
    }
  }, [wsClient])

  // 切换 tab 到 pocket 时自动加载
  useEffect(() => { if (tab === 'pocket') { void loadPocket() } }, [tab, loadPocket])

  // 后端 reading.delete_article 按 article_id（bookmark 的 uuid）索引，
  // 而非 url；旧实现传 url 会导致后端恒返回 not found，刷新后条目复活。
  const handleRemoveArticle = useCallback(async (articleId: string) => {
    if (!articleId) return
    try {
      await wsClient.call('reading.delete_article', { article_id: articleId })
      setSavedArticles((prev) => prev.filter((a) => a.id !== articleId))
    } catch (e) { swallowError('InfoModule:removeArticle', e) }
  }, [wsClient])

  const handleRssSubscribe = useCallback(async () => {
    const url = rssUrl.trim()
    if (!url) return
    setRssSubscribing(true)
    try {
      const result = await wsClient.call<{ success: boolean; feed?: RssFeed; error?: string }>('rss.subscribe', { url })
      if (result?.success) {
        setRssUrl('')
        void loadPocket()
      } else if (result?.error) {
        setInfoError(`RSS subscribe failed: ${result.error}`)
      }
    } catch (e) { setInfoError(`RSS subscribe failed: ${e instanceof Error ? e.message : String(e)}`) }
    finally { setRssSubscribing(false) }
  }, [rssUrl, wsClient, loadPocket, setInfoError])

  const handleRssUnsubscribe = useCallback(async (feedId: string) => {
    try {
      await wsClient.call('rss.unsubscribe', { feed_id: feedId })
      void loadPocket()
    } catch (e) { swallowError('InfoModule:rssUnsub', e) }
  }, [wsClient, loadPocket])

  const isEmpty = !infoBrief && !infoLoading && !infoError

  return (
    <div className="flex-1 flex flex-col min-h-0 p-4 gap-3">
      {/* ── Tab bar ── */}
      <div className="flex items-center gap-2">
        <div className="flex items-center gap-1 rounded-lg p-0.5 border self-start" style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>
        <button
          onClick={() => setTab('brief')}
          className="px-3 py-1 rounded-md text-xs font-medium transition-all"
          style={{
            background: tab === 'brief' ? 'rgba(96,165,250,0.12)' : 'transparent',
            color: tab === 'brief' ? 'var(--brand)' : 'var(--text-muted)',
          }}
        >
          <Icon icon="fa-solid fa-wand-magic-sparkles mr-1 text-xs" />{t('info.tabBrief')}
        </button>
        <button
          onClick={() => setTab('pocket')}
          className="px-3 py-1 rounded-md text-xs font-medium transition-all"
          style={{
            background: tab === 'pocket' ? 'rgba(234,179,8,0.12)' : 'transparent',
            color: tab === 'pocket' ? '#eab308' : 'var(--text-muted)',
          }}
        >
          <Icon icon="fa-regular fa-bookmark mr-1 text-xs" />{t('info.pocket')}
        </button>
        <button
          onClick={() => setTab('library')}
          className="px-3 py-1 rounded-md text-xs font-medium transition-all"
          style={{
            background: tab === 'library' ? 'rgba(34,197,94,0.12)' : 'transparent',
            color: tab === 'library' ? 'var(--success)' : 'var(--text-muted)',
          }}
        >
          <Icon icon="fa-solid fa-box-archive mr-1 text-xs" />{t('info.tabLibrary')}
        </button>
      </div>
        {ei && (
          <button
            onClick={() => ei.createViewWindow({ viewType: 'info' })}
            className="text-xs text-text-dim hover:text-text-secondary transition-colors self-start mt-0.5"
            title={t('info.openInWindow')}
          >
            <Icon icon="fa-solid fa-external-link-alt" />
          </button>
        )}
        <button
          onClick={() => setShowSectionConfig(true)}
          className="text-xs text-text-dim hover:text-text-secondary transition-colors self-start mt-0.5 ml-1"
          title={t('infoSection.title')}
        >
          <Icon icon="fa-solid fa-sliders" />
        </button>
      </div>

      {/* ── Content ── */}
      {tab === 'brief' && (
        <>
          {/* Action bar */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {infoLastFetch > 0 ? t('info.lastUpdateFmt', { value: new Date(infoLastFetch).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }) : ''}
              </div>
              {autoInfoLastRun > 0 && (
                <span className="text-2xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(34,197,94,0.1)', color: 'var(--success)' }}>
                  {t('info.autoTagFmt', { value: new Date(autoInfoLastRun).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) })}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={async () => {
                  const next = !autoInfoEnabled
                  setAutoInfoEnabled(next)
                  try {
                    await wsClient.call('info.auto_config', { enabled: next })
                  } catch (e) { /* best-effort */ swallowError('InfoModule:232', e) }
                }}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all"
                style={{
                  background: autoInfoEnabled ? 'var(--success-soft)' : 'var(--hover-bg)',
                  color: autoInfoEnabled ? 'var(--success)' : 'var(--text-muted)',
                }}
                title={autoInfoEnabled ? t('info.autoDisableTip') : t('info.autoEnableTip')}
              >
                <Icon icon={`fa-solid ${autoInfoEnabled ? 'fa-circle-check' : 'fa-clock'} text-xs`} />
                {autoInfoEnabled ? t('info.autoOn') : t('info.autoOff')}
              </button>
              <button
                onClick={handleGenerate}
                disabled={infoLoading}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
                style={{ background: 'var(--hover-bg)', color: 'var(--text-secondary)' }}
              >
                {infoLoading ? (
                  <><div className="w-3 h-3 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--panel-border)', borderTopColor: 'var(--text-secondary)' }} />{t('info.generating')}</>
                ) : (
                  <><Icon icon="fa-solid fa-wand-magic-sparkles text-xs" />{t('info.generate')}</>
                )}
              </button>
            </div>
          </div>

          {/* Error */}
          {infoError && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs" style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>
              <Icon icon="fa-solid fa-circle-exclamation" />
              <span className="flex-1">{infoError}</span>
              <button onClick={handleGenerate} className="underline" style={{ color: 'var(--text-muted)' }}>{t('info.retry')}</button>
            </div>
          )}

          {/* Source fetch warnings */}
          {fetchErrors.length > 0 && !infoLoading && infoBrief && (
            <div className="px-3 py-1.5 rounded-lg text-xs" style={{ background: 'rgba(250,204,21,0.08)', color: 'var(--text-muted)' }}>
              <Icon icon="fa-solid fa-triangle-exclamation mr-1" />
              {fetchErrors.filter(e => e.source !== 'system').map(e => e.source).join('、')}{t('info.fetchFailedSuffix')}
            </div>
          )}

          {/* Content */}
          {/* 关注领域筛选（简报 topics）——chips 编辑（settingsSync 自动持久化 info_topics） */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-2xs flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
              <Icon icon="fa-solid fa-filter mr-1" />{t('info.topicsLabel')}
            </span>
            {infoTopics.map((topic) => (
              <span key={topic} className="flex items-center gap-1 px-2 py-0.5 rounded-full text-2xs" style={{ background: 'var(--brand-soft)', color: 'var(--brand)' }}>
                {topic}
                {topicsEditing && (
                  <button
                    onClick={() => setInfoTopics(infoTopics.filter(t => t !== topic))}
                    className="hover:opacity-70"
                    title={t('info.topicsRemove')}
                  >
                    <Icon icon="fa-solid fa-xmark text-[8px]" />
                  </button>
                )}
              </span>
            ))}
            {topicsEditing ? (
              <>
                <input
                  value={topicDraft}
                  onChange={(e) => setTopicDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && topicDraft.trim()) {
                      if (!infoTopics.includes(topicDraft.trim())) setInfoTopics([...infoTopics, topicDraft.trim()])
                      setTopicDraft('')
                    }
                    if (e.key === 'Escape') setTopicsEditing(false)
                  }}
                  placeholder={t('info.topicsPlaceholder')}
                  autoFocus
                  className="px-2 py-0.5 rounded-full text-2xs border outline-none w-28"
                  style={{ background: 'var(--hover-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
                />
                <button onClick={() => setTopicsEditing(false)} className="text-2xs underline" style={{ color: 'var(--text-dim)' }}>
                  {t('info.topicsDone')}
                </button>
              </>
            ) : (
              <button
                onClick={() => setTopicsEditing(true)}
                className="text-2xs px-1.5 py-0.5 rounded-full border transition-colors hover:opacity-80"
                style={{ borderColor: 'var(--panel-border)', color: 'var(--text-dim)' }}
                title={t('info.topicsHint')}
              >
                <Icon icon="fa-solid fa-plus text-[8px] mr-0.5" />{t('info.topicsAdd')}
              </button>
            )}
          </div>

          {isEmpty ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-3 text-center py-12">
              <div className="text-2xl opacity-25"><Icon icon="fa-regular fa-newspaper" /></div>
              <div className="text-sm" style={{ color: 'var(--text-muted)' }}>
                {t('info.emptyHint')}
              </div>
              <button onClick={handleGenerate} className="px-4 py-2 rounded-lg text-sm font-medium transition-all" style={{ background: 'var(--hover-bg)', color: 'var(--text-secondary)' }}>
                <Icon icon="fa-solid fa-wand-magic-sparkles mr-1.5" />{t('info.emptyStart')}
              </button>
            </div>
          ) : infoLoading ? (
            <div className="flex-1 overflow-y-auto min-h-0 p-1 space-y-3" data-testid="brief-skeleton">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-5/6" />
              <div className="pt-2 space-y-3">
                <Skeleton className="h-4 w-1/2" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-11/12" />
                <Skeleton className="h-3 w-full" />
                <Skeleton className="h-3 w-4/6" />
              </div>
              <div className="text-center text-xs pt-2" style={{ color: 'var(--text-dim)' }}>{t('info.loadingFeed')}</div>
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto min-h-0 prose max-w-none text-sm leading-relaxed"
              style={{ color: 'var(--text-secondary)' }}>
              {/* hardenSoftBreaks: remark-gfm collapses single newlines into
                  spaces, flattening the 4-line brief entries into one block */}
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{hardenSoftBreaks(infoBrief)}</ReactMarkdown>
            </div>
          )}
        </>
      )}

      {tab === 'pocket' && (
        <div className="flex-1 overflow-y-auto min-h-0 space-y-4">
          {pocketLoading ? (
            <div className="flex items-center justify-center py-8 gap-2">
              <div className="w-4 h-4 border-2 rounded-full animate-spin" style={{ borderColor: 'var(--panel-border)', borderTopColor: 'var(--text-secondary)' }} />
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('info.loading')}</span>
            </div>
          ) : (
            <>
              {/* ── 已收藏文章 ── */}
              <div>
                <h3 className="text-xs font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>
                  <Icon icon="fa-solid fa-bookmark mr-1.5" style={{ color: '#eab308' }} />{t('info.savedArticles')} ({savedArticles.length})
                </h3>
                {savedArticles.length === 0 ? (
                  <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('info.noSavedArticles')}</p>
                ) : (
                  <div className="space-y-1">
                    {savedArticles.map((a) => (
                      <div key={a.id || a.url} className="flex items-start gap-2 px-3 py-2 rounded-lg border" style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg-solid)' }}>
                        <div className="flex-1 min-w-0">
                          <div className="text-xs font-medium leading-snug" style={{ color: 'var(--text-primary)' }}>
                            {a.url ? <a href={a.url} target="_blank" rel="noopener noreferrer" className="hover:underline">{a.title || a.url}</a> : (a.title || '—')}
                          </div>
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{a.source || a.category || ''}</span>
                            {a.saved_at && <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{new Date(a.saved_at).toLocaleDateString('zh-CN')}</span>}
                          </div>
                        </div>
                        <button
                          onClick={() => handleRemoveArticle(a.id)}
                          className="shrink-0 text-2xs px-1.5 py-0.5 rounded hover:bg-red-500/10 hover:text-red-400 transition-colors"
                          style={{ color: 'var(--text-muted)' }}
                        >
                          <Icon icon="fa-solid fa-xmark text-[8px]" />
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* ── RSS 订阅 ── */}
              <div>
                <h3 className="text-xs font-semibold mb-2" style={{ color: 'var(--text-primary)' }}>
                  <Icon icon="fa-solid fa-rss mr-1.5" style={{ color: '#f97316' }} />{t('info.rssFeeds')} ({rssFeeds.length})
                </h3>
                <div className="flex items-center gap-2 mb-2">
                  <input
                    type="url"
                    value={rssUrl}
                    onChange={(e) => setRssUrl(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleRssSubscribe() }}
                    placeholder={t('info.rssPlaceholder')}
                    className="flex-1 px-2.5 py-1.5 rounded-lg text-xs border outline-none"
                    style={{ background: 'var(--hover-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
                  />
                  <button
                    onClick={() => void handleRssSubscribe()}
                    disabled={rssSubscribing || !rssUrl.trim()}
                    className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
                    style={{ background: 'rgba(249,115,22,0.12)', color: '#f97316' }}
                  >
                    {rssSubscribing ? t('info.subscribing') : t('info.subscribe')}
                  </button>
                </div>
                {rssFeeds.length > 0 && (
                  <div className="space-y-1">
                    {rssFeeds.map((f) => (
                      <div key={f.id || f.url} className="flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs" style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg-solid)' }}>
                        <span className="flex-1 truncate" style={{ color: 'var(--text-primary)' }}>{f.name || f.url}</span>
                        {f.last_fetched_at && <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{new Date(f.last_fetched_at).toLocaleDateString('zh-CN')}</span>}
                        {f.last_error && <span className="text-2xs text-red-400 truncate max-w-[100px]">{f.last_error}</span>}
                        <button
                          onClick={() => handleRssUnsubscribe(f.id || '')}
                          className="text-2xs px-1 py-0.5 rounded hover:bg-red-500/10 hover:text-red-400 transition-colors"
                          style={{ color: 'var(--text-muted)' }}
                        >
                          {t('info.unsubscribe')}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}

      {/* D3: info library (items + sections management) — 合并后的「资讯」页 */}
      {tab === 'library' && (
        <div className="flex-1 overflow-y-auto min-h-0" data-testid="info-library-view">
          <InfoLibraryView
            revision={sectionRevision}
            onFetch={handleFetchLibrary}
            onOpenDetail={(it) => {
              // 2026-10-15（详情弹窗接线）：原 setDetailItem 只有 onClose 分支，
              // 弹窗永远不显示（死弹窗）。library 条目 → 构造 RawSourceItem 打开。
              setDetailItem({
                id: String(it.id ?? it.url ?? ''),
                title: it.title || '',
                url: it.url || '',
                source: it.source || '',
                summary: it.summary || '',
                tags: [],
                score: 0,
                publishedAt: 0,
              })
            }}
          />
        </div>
      )}

      {/* Detail modal */}
      <InfoDetailModal item={detailItem} onClose={() => setDetailItem(null)} />

      {/* 板块配置弹窗（设置入口） */}
      {showSectionConfig && (
        <InfoSectionConfig
          onClose={() => setShowSectionConfig(false)}
          onChanged={() => setSectionRevision(r => r + 1)}
        />
      )}
    </div>
  )
}

export default InfoModule