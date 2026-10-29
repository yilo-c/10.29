import React, { useState, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'
import Skeleton from '../Common/Skeleton'
import InfoSectionConfig from './InfoSectionConfig'

import { getWsClient } from '../../utils/ai'
import { formatRelative } from '@desk-notes/shared'
import { swallowError } from '../../utils/swallowError'

/**
 * D3（前后端对齐规划）：资讯库管理视图。
 *
 * 数据源（后端早已就绪、此前前端零接入）：
 * - info.items.list/toggle_read/toggle_saved/save_to_note/stats
 *   （backend/handlers/_info_items.py）
 * - info.sections.list/create/delete（backend/handlers/_info_proxy.py）
 *
 * i18n 说明：按用户确认不做英文界面，本组件直接使用中文文案。
 */

interface InfoItem {
  id: string | number
  title?: string
  url?: string
  source?: string
  category?: string
  summary?: string
  read?: boolean
  saved?: boolean
  collected_at?: number | string
}

interface InfoSection {
  id: string
  name: string
  keywords?: string[]
  emoji?: string
  enabled?: boolean
  is_default?: boolean
}

type ItemFilter = 'all' | 'unread' | 'saved'

const FILTERS: Array<{ key: ItemFilter; label: string }> = [
  { key: 'all', label: '全部' },
  { key: 'unread', label: '未读' },
  { key: 'saved', label: '收藏' },
]

interface Props {
  /** 板块配置变更后由父层自增，触发本视图刷新（用于感知弹窗内的改动）。 */
  revision?: number
  /** 抓取更新回调：由父层触发采集（feeed→全库），完成后重载条目。可空（独立使用场景）。 */
  onFetch?: () => void | Promise<void>
  /**
   * 2026-10-15（详情弹窗接线）：条目"查看详情"回调——父层据此打开
   * InfoDetailModal（原先 setDetailItem 永远为 null，弹窗是死的）。
   */
  onOpenDetail?: (item: InfoItem) => void
}

function toTs(v?: number | string): number | undefined {
  if (v === undefined || v === null) return undefined
  const n = typeof v === 'number' ? v : Number(v)
  if (!Number.isFinite(n)) return undefined
  // 兼容秒/毫秒
  return n < 10_000_000_000 ? n * 1000 : n
}

const InfoLibraryView: React.FC<Props> = ({ revision = 0, onFetch, onOpenDetail }) => {
  const [items, setItems] = useState<InfoItem[]>([])
  const [total, setTotal] = useState(0)
  const [filter, setFilter] = useState<ItemFilter>('all')
  const [search, setSearch] = useState('')
  const [category, setCategory] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  // 来源过滤（默认 null = 全库）
  const [sourceFilter, setSourceFilter] = useState<string | null>(null)
  const [availableSources, setAvailableSources] = useState<{ name: string; display: string }[]>([])

  const [sections, setSections] = useState<InfoSection[]>([])
  const [newSectionName, setNewSectionName] = useState('')
  const [busy, setBusy] = useState(false)

  // 板块详细编辑弹窗
  const [showSectionConfig, setShowSectionConfig] = useState(false)

  const flash = useCallback((kind: 'ok' | 'err', text: string) => {
    setMessage({ kind, text })
    setTimeout(() => setMessage(null), 3500)
  }, [])

  const loadItems = useCallback(async (opts?: { q?: string; cat?: string | null; flt?: ItemFilter; src?: string | null }) => {
    setLoading(true)
    try {
      const client = getWsClient()
      if (client.status !== 'connected') return
      const q = opts?.q !== undefined ? opts.q : search
      const cat = opts?.cat !== undefined ? opts.cat : category
      const flt = opts?.flt !== undefined ? opts.flt : filter
      const src = opts?.src !== undefined ? opts.src : sourceFilter
      const params: Record<string, unknown> = { limit: 50 }
      if (flt === 'unread') params.read = false
      if (flt === 'saved') params.saved = true
      if (cat) params.category = cat
      if (src) params.source = src
      if (q.trim()) params.search = q.trim()
      const res = await client.call<{ items?: InfoItem[]; total?: number; error?: string }>('info.items.list', params)
      if (res?.error) {
        flash('err', `加载失败: ${res.error}`)
      }
      setItems(res?.items ?? [])
      setTotal(res?.total ?? 0)
    } catch (e) {
      swallowError('InfoLibrary:items', e)
    } finally {
      setLoading(false)
    }
  }, [filter, category, search, sourceFilter])

  const loadSections = useCallback(async () => {
    try {
      const client = getWsClient()
      if (client.status !== 'connected') return
      const res = await client.call<{ effective?: InfoSection[]; sections?: InfoSection[] }>('info.sections.list')
      // 兼容后端返回 effective（真实契约）与简化返回 sections（测试/兼容层）
      setSections(res?.sections ?? res?.effective ?? [])
    } catch (e) {
      swallowError('InfoLibrary:sections', e)
      setSections([])
    }
  }, [])

  const loadAvailableSources = useCallback(async () => {
    try {
      const client = getWsClient()
      if (client.status !== 'connected') return
      const res = await client.call<{ sources?: { name: string; display: string }[] }>('info.sources.list')
      setAvailableSources(res?.sources ?? [])
    } catch (e) {
      swallowError('InfoLibrary:sources', e)
    }
  }, [])

  useEffect(() => {
    loadSections()
    loadAvailableSources()
    loadItems()
  }, [loadSections, loadAvailableSources, loadItems, revision])

  // 触发抓取更新：完成后重载条目
  const handleFetch = useCallback(async () => {
    if (!onFetch) return
    setMessage({ kind: 'ok', text: '正在抓取更新…' })
    try {
      await onFetch()
      await loadItems({})
      flash('ok', '抓取完成，已更新')
    } catch (e) {
      flash('err', '抓取失败')
      swallowError('InfoLibrary:fetch', e)
    }
  }, [onFetch, loadItems, flash])

  // 搜索框防抖：停顿 350ms 后再请求，避免逐键触发
  useEffect(() => {
    const t = window.setTimeout(() => { void loadItems({ q: search }) }, 350)
    return () => window.clearTimeout(t)
  }, [search]) // eslint-disable-line react-hooks/exhaustive-deps

  // filter / category / source 变化立即刷新
  useEffect(() => {
    void loadItems()
  }, [filter, category, sourceFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  const toggleRead = async (item: InfoItem) => {
    try {
      const client = getWsClient()
      await client.call('info.items.toggle_read', { item_id: item.id })
      setItems(prev => prev.map(i => i.id === item.id ? { ...i, read: !i.read } : i))
    } catch (e) {
      flash('err', '操作失败')
      swallowError('InfoLibrary:toggleRead', e)
    }
  }

  const toggleSaved = async (item: InfoItem) => {
    try {
      const client = getWsClient()
      await client.call('info.items.toggle_saved', { item_id: item.id })
      setItems(prev => prev.map(i => i.id === item.id ? { ...i, saved: !i.saved } : i))
    } catch (e) {
      flash('err', '操作失败')
      swallowError('InfoLibrary:toggleSaved', e)
    }
  }

  const saveToNote = async (item: InfoItem) => {
    try {
      const client = getWsClient()
      const res = await client.call<{ success?: boolean; note_id?: string; error?: string }>('info.items.save_to_note', { item_id: item.id })
      if (res?.success === false) {
        flash('err', res.error ?? '保存失败')
      } else {
        flash('ok', '已保存到笔记')
      }
    } catch (e) {
      flash('err', '保存失败')
      swallowError('InfoLibrary:saveToNote', e)
    }
  }

  const createSection = async (name?: string) => {
    const sectionName = (name || newSectionName).trim()
    if (!sectionName || busy) return
    setBusy(true)
    try {
      const client = getWsClient()
      const res = await client.call<{ success?: boolean; error?: string }>('info.sections.create', { name: sectionName, keywords: [] })
      if (res?.success === false) {
        flash('err', res.error ?? '创建失败')
      } else {
        setNewSectionName('')
        flash('ok', `板块「${sectionName}」已创建`)
        await loadSections()
      }
    } catch (e) {
      flash('err', '创建失败')
      swallowError('InfoLibrary:createSection', e)
    } finally {
      setBusy(false)
    }
  }

  const deleteSection = async (section: InfoSection) => {
    try {
      const client = getWsClient()
      const res = await client.call<{ success?: boolean; error?: string }>('info.sections.delete', { section_id: section.id })
      if (res?.success === false) {
        flash('err', res.error ?? '删除失败')
      } else {
        flash('ok', `板块「${section.name}」已删除`)
        await loadSections()
      }
    } catch (e) {
      flash('err', '删除失败')
      swallowError('InfoLibrary:deleteSection', e)
    }
  }

  return (
    <>
    <div className="space-y-4">
      {/* ── 板块过滤 + 管理（合并） ── */}
      <div>
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>资讯板块</span>
          <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>（点击筛选 · 双击编辑 · 删除）</span>
        </div>
        <div className="flex items-center gap-1.5 flex-wrap">
          <button
            onClick={() => setCategory(null)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium transition-all ${
              category === null ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            全板块
          </button>
          {sections.map(s => {
            const isActive = category === s.id
            return (
              <span
                key={s.id}
                className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs font-medium transition-all border ${
                  isActive
                    ? 'bg-fluent-blue/15 text-fluent-blue border-fluent-blue/30'
                    : 'text-text-muted hover:text-text-secondary border-white/10'
                }`}
                title={s.keywords?.length ? `关键词：${s.keywords.join('、')}` : undefined}
              >
                <span
                  className="cursor-pointer"
                  onClick={() => setCategory(s.id)}
                  onDoubleClick={() => setShowSectionConfig(true)}
                >
                  {s.emoji || ''} {s.name}
                </span>
                {s.is_default ? (
                  <span className="text-[8px] px-1 py-0.5 rounded bg-white/[0.06] text-text-muted leading-none">默认</span>
                ) : (
                  <button
                    onClick={() => deleteSection(s)}
                    className="hover:text-red-400 transition-colors ml-0.5"
                    style={{ color: 'var(--text-dim)' }}
                    title="删除板块"
                  >
                    ✕
                  </button>
                )}
              </span>
            )
          })}
          <div className="flex items-center gap-1">
            <input
              value={newSectionName}
              onChange={e => setNewSectionName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); createSection(e.currentTarget.value) } }}
              placeholder="新板块名…"
              className="px-2 py-1 rounded-lg text-xs outline-none border w-24"
              style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
            />
            <button
              onClick={() => createSection()}
              disabled={!newSectionName.trim() || busy}
              className="px-2 py-1 rounded-lg text-xs text-fluent-blue disabled:opacity-30 transition-all"
            >
              添加
            </button>
          </div>
        </div>
      </div>

      {/* ── 条目管理 ── */}
        <div>
          {/* 来源过滤（全库默认）+ 抓取更新 */}
          <div className="flex items-center gap-1.5 flex-wrap mb-2">
            <span className="text-2xs mr-0.5" style={{ color: 'var(--text-dim)' }}>来源</span>
            <button
              onClick={() => setSourceFilter(null)}
              className={`px-2 py-0.5 rounded-full text-2xs font-medium transition-all ${
                sourceFilter === null ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              全库
            </button>
            {availableSources.map(src => (
              <button
                key={src.name}
                onClick={() => setSourceFilter(sourceFilter === src.name ? null : src.name)}
                className={`px-2 py-0.5 rounded-full text-2xs font-medium transition-all ${
                  sourceFilter === src.name ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-text-muted hover:text-text-secondary'
                }`}
                title={src.name}
              >
                {src.display}
              </button>
            ))}
            {onFetch && (
              <button
                onClick={() => void handleFetch()}
                className="ml-auto flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
                style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}
                disabled={loading}
              >
                <Icon icon="fa-solid fa-rotate text-2xs" />
                抓取更新
              </button>
            )}
          </div>
          <div className="flex items-center gap-1.5 mb-2">
            {FILTERS.map(f => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`px-2.5 py-1 rounded-full text-xs font-medium transition-all ${
                  filter === f.key ? 'bg-fluent-blue/15 text-fluent-blue' : 'text-text-muted hover:text-text-secondary'
                }`}
              >
                {f.label}
              </button>
            ))}
            <div className="ml-2 flex-1 flex items-center gap-1.5 min-w-0">
              <div
                className="flex items-center gap-1.5 px-2 py-1 rounded-lg border flex-1 min-w-0"
                style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)' }}
              >
                <Icon icon="fa-solid fa-magnifying-glass text-2xs" style={{ color: 'var(--text-dim)' }} />
                <input
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="关键词搜索…"
                  className="flex-1 min-w-0 bg-transparent outline-none text-xs"
                  style={{ color: 'var(--text-primary)' }}
                />
                {search && (
                  <button onClick={() => setSearch('')} className="text-2xs hover:opacity-70" style={{ color: 'var(--text-dim)' }}>
                    <Icon icon="fa-solid fa-xmark" />
                  </button>
                )}
              </div>
            </div>
            <span className="ml-auto text-2xs" style={{ color: 'var(--text-dim)' }}>
              共 {total} 条
            </span>
          </div>

        {loading && (
          <div className="space-y-2">
            {[0, 1, 2].map(i => (
              <div key={i} className="space-y-1">
                <Skeleton className="h-3 w-3/4 rounded" />
                <Skeleton className="h-2 w-1/3 rounded" />
              </div>
            ))}
          </div>
        )}

        {!loading && items.length === 0 && (
          <div className="py-6 text-center text-xs" style={{ color: 'var(--text-dim)' }}>
            暂无资讯条目。先在「AI 简报」页抓取一次资讯。
          </div>
        )}

        {!loading && items.length > 0 && (
          <div className="space-y-1">
            {items.map(item => {
              const ts = toTs(item.collected_at)
              return (
                <div
                  key={String(item.id)}
                  className="flex items-center gap-2 px-2 py-1.5 rounded-lg group transition-colors hover:bg-hover-bg"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      {!item.read && <span className="w-1.5 h-1.5 rounded-full bg-fluent-blue flex-shrink-0" />}
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className={`text-xs truncate ${item.read ? 'text-text-muted' : 'text-text-primary'}`}
                        onClick={e => { if (!item.read) { e.persist?.(); toggleRead(item) } }}
                      >
                        {item.title || item.url || '(无标题)'}
                      </a>
                    </div>
                    <div className="flex items-center gap-1.5 text-2xs mt-0.5" style={{ color: 'var(--text-dim)' }}>
                      {item.source && <span>{item.source}</span>}
                      {item.category && <span>· {item.category}</span>}
                      {ts && <span>· {formatRelative(ts)}</span>}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
                    {onOpenDetail && (
                      <button
                        onClick={() => onOpenDetail(item)}
                        className="w-6 h-6 flex items-center justify-center rounded transition-colors hover:bg-white/[0.06]"
                        title="查看详情"
                        style={{ color: 'var(--text-dim)' }}
                      >
                        <Icon icon="fa-regular fa-window-maximize text-xs" />
                      </button>
                    )}
                    <button
                      onClick={() => toggleSaved(item)}
                      className="w-6 h-6 flex items-center justify-center rounded transition-colors hover:bg-white/[0.06]"
                      title={item.saved ? '取消收藏' : '收藏'}
                      style={{ color: item.saved ? '#fbbf24' : 'var(--text-dim)' }}
                    >
                      <Icon icon={`${item.saved ? 'fa-solid' : 'fa-regular'} fa-star text-xs`} />
                    </button>
                    <button
                      onClick={() => saveToNote(item)}
                      className="w-6 h-6 flex items-center justify-center rounded transition-colors hover:bg-white/[0.06]"
                      title="保存到笔记"
                      style={{ color: 'var(--text-dim)' }}
                    >
                      <Icon icon="fa-regular fa-note-sticky text-xs" />
                    </button>
                    {!item.read && (
                      <button
                        onClick={() => toggleRead(item)}
                        className="w-6 h-6 flex items-center justify-center rounded transition-colors hover:bg-white/[0.06]"
                        title="标记已读"
                        style={{ color: 'var(--text-dim)' }}
                      >
                        <Icon icon="fa-solid fa-check text-xs" />
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* 反馈消息（消息存续期间保持渲染） */}
      {message && (
        <div className={`text-xs ${message.kind === 'ok' ? 'text-fluent-green' : 'text-red-400'}`}>
          {message.text}
        </div>
      )}
    </div>

    {/* 板块详细编辑弹窗 */}
    {showSectionConfig && (
      <InfoSectionConfig
        onClose={() => setShowSectionConfig(false)}
        onChanged={() => { void loadSections(); void loadAvailableSources() }}
      />
    )}
    </>
  )
}

export default InfoLibraryView
