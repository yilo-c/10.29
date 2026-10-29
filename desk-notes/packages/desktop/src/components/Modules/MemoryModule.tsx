import { useCallback, useEffect, useRef, useState } from 'react'

import { getWsClient } from '../../utils/ai'

/* ── 类型（对齐后端 memory.list 已入库契约，U3 批1，2026-09-16）── */

interface MemoryEntry {
  entry_id?: string
  source_type?: string
  source_id?: string | number
  title?: string
  snippet?: string
  updated_at?: string
  metadata?: { revoked?: boolean; [key: string]: unknown }
}

interface MemoryListResult {
  success?: boolean
  items?: MemoryEntry[]
  total?: number
  total_revoked?: number
  error?: string
}

interface MemoryMutateResult {
  success?: boolean
  entry_id?: string
  mode?: string
  error?: string
}

type Tab = 'active' | 'revoked'

const PAGE = 50

function fmtTime(v?: string): string {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('zh-CN')
}

/**
 * 记忆管理面板（U3 批2）。
 *
 * 后端契约（主仓 _chat_memory.py，已入库）：
 * - memory.list {limit, offset, include_revoked} → {success, items, total, total_revoked}
 * - memory.delete {entry_id, reason} → 软删（revoked），可恢复
 * - memory.restore {entry_id, reason} → 恢复；失败码 memory.entry_not_recoverable
 *
 * 已撤销 tab 注意：include_revoked=true 为后端**混排语义**（返回全部条目，
 * 含未撤销项），revoked 判定在前端按 `metadata.revoked === true` 过滤。
 */
export function MemoryModule() {
  const [tab, setTab] = useState<Tab>('active')
  const [items, setItems] = useState<MemoryEntry[]>([])
  const [total, setTotal] = useState(0)
  const [totalRevoked, setTotalRevoked] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [offline, setOffline] = useState(false)

  // 竞态守卫（仿 MemoryReplayModal 风格）：cancelled 标志 + 请求代次
  const seqRef = useRef(0)
  const cancelledRef = useRef(false)
  // 生效中 tab 的翻页游标（已加载条数）
  const offsetRef = useRef(0)

  const fetchPage = useCallback(async (target: Tab, append: boolean) => {
    const client = getWsClient()
    if (!client) {
      setOffline(true)
      return
    }
    setOffline(false)
    const seq = ++seqRef.current
    setLoading(true)
    setError(null)
    try {
      const params =
        target === 'active'
          ? { limit: PAGE, offset: append ? offsetRef.current : 0, include_revoked: false }
          : // 已撤销 tab：一次拉全量（后端 limit≤200），混排结果前端过滤 revoked
            { limit: 200, offset: 0, include_revoked: true }
      const res = await client.call<MemoryListResult>('memory.list', params)
      if (cancelledRef.current || seq !== seqRef.current) return
      if (!res?.success) {
        setError(res?.error || '记忆列表读取失败')
        return
      }
      const raw = Array.isArray(res.items) ? res.items : []
      const list = target === 'revoked' ? raw.filter((it) => it.metadata?.revoked === true) : raw
      offsetRef.current = append ? offsetRef.current + list.length : list.length
      setItems((prev) => (append ? [...prev, ...list] : list))
      setTotal(typeof res.total === 'number' ? res.total : list.length)
      if (typeof res.total_revoked === 'number') setTotalRevoked(res.total_revoked)
    } catch (reason) {
      if (cancelledRef.current || seq !== seqRef.current) return
      setError(reason instanceof Error ? reason.message : '记忆列表读取失败')
    } finally {
      if (!cancelledRef.current && seq === seqRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    cancelledRef.current = false
    return () => {
      cancelledRef.current = true
    }
  }, [])

  useEffect(() => {
    void fetchPage(tab, false)
  }, [tab, fetchPage])

  const refresh = useCallback(() => void fetchPage(tab, false), [tab, fetchPage])

  const revokeEntry = useCallback(
    async (it: MemoryEntry) => {
      const entryId = String(it.entry_id || '')
      if (!entryId) return
      const preview = String(it.title || it.snippet || '').slice(0, 60)
      if (!window.confirm(`确定撤销这条记忆？\n${preview}\n（可在「已撤销」tab 恢复）`)) return
      const client = getWsClient()
      if (!client) {
        setOffline(true)
        return
      }
      try {
        const res = await client.call<MemoryMutateResult>('memory.delete', { entry_id: entryId, reason: 'memory_panel' })
        if (cancelledRef.current) return
        if (!res?.success) {
          setError(res?.error || '撤销失败')
          return
        }
        refresh() // 刷新当前 tab 并重取 total_revoked
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '撤销失败')
      }
    },
    [refresh],
  )

  const restoreEntry = useCallback(
    async (it: MemoryEntry) => {
      const entryId = String(it.entry_id || '')
      if (!entryId) return
      const client = getWsClient()
      if (!client) {
        setOffline(true)
        return
      }
      try {
        // 软恢复低风险：无二次确认
        const res = await client.call<MemoryMutateResult>('memory.restore', { entry_id: entryId, reason: 'memory_panel_restore' })
        if (cancelledRef.current) return
        if (!res?.success) {
          setError(res?.error || '恢复失败')
          return
        }
        refresh()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '恢复失败')
      }
    },
    [refresh],
  )

  const canLoadMore = tab === 'active' && !loading && items.length < total

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-auto bg-[var(--body-bg)] p-6" aria-labelledby="memory-heading" data-testid="memory-workspace">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--separator)] pb-5">
        <div>
          <span className="agent-kicker">MEMORY</span>
          <h1 id="memory-heading" className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">记忆管理</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">查看与撤销自动沉淀的记忆条目（软删除，可在「已撤销」中恢复）</p>
        </div>
      </header>

      <nav className="mt-4 flex items-center gap-1 border-b border-[var(--separator)]" aria-label="记忆管理功能">
        <button
          type="button"
          data-testid="memory-tab-active"
          onClick={() => setTab('active')}
          className={`rounded-t-md px-4 py-2 text-sm transition-colors ${tab === 'active' ? 'border-b-2 border-fluent-blue font-medium text-[var(--text-primary)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}
        >
          生效中
        </button>
        <button
          type="button"
          data-testid="memory-tab-revoked"
          onClick={() => setTab('revoked')}
          className={`rounded-t-md px-4 py-2 text-sm transition-colors ${tab === 'revoked' ? 'border-b-2 border-fluent-blue font-medium text-[var(--text-primary)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}
        >
          {`已撤销 (${totalRevoked})`}
        </button>
        <button type="button" onClick={refresh} data-testid="memory-refresh" className="ml-auto rounded-md border border-[var(--separator)] px-3 py-1.5 text-xs text-[var(--text-secondary)]">
          刷新
        </button>
      </nav>

      {offline && (
        <div data-testid="memory-offline" className="mt-5 rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3 text-sm text-[var(--text-muted)]">
          后端未连接
        </div>
      )}
      {error && <div role="alert" className="mt-5 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-4 text-sm text-[var(--danger)]">{error}</div>}

      <div className="mt-5 space-y-2.5">
        {loading && items.length === 0 && <p className="py-10 text-center text-sm text-[var(--text-muted)]">加载中...</p>}

        {!loading && tab === 'active' && items.length === 0 && (
          <p className="py-10 text-center text-sm text-[var(--text-muted)]">暂无生效中的记忆条目</p>
        )}
        {!loading && tab === 'revoked' && items.length === 0 && (
          <p className="py-10 text-center text-sm text-[var(--text-muted)]">{totalRevoked === 0 ? '没有已撤销的记忆' : '暂无已撤销记忆'}</p>
        )}

        {items.map((it, idx) => {
          const entryId = String(it.entry_id || '')
          const title = String(it.title || '').trim() || String(it.snippet || '').slice(0, 40) || '（无标题）'
          return (
            <div key={entryId || `row-${idx}`} data-testid={entryId ? `memory-row-${entryId}` : undefined} className="flex items-start gap-3 rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] px-4 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="truncate text-sm font-medium text-[var(--text-primary)]">{title}</span>
                  {it.source_type && <span className="rounded bg-fluent-blue/10 px-1.5 py-0.5 text-2xs text-fluent-blue">{it.source_type}</span>}
                  <span className="text-xs text-[var(--text-muted)]">{fmtTime(it.updated_at)}</span>
                </div>
                {it.snippet && <p className="mt-1 truncate text-xs text-[var(--text-secondary)]">{it.snippet}</p>}
              </div>
              {tab === 'active' ? (
                <button
                  type="button"
                  disabled={!entryId}
                  onClick={() => void revokeEntry(it)}
                  className="flex-shrink-0 rounded-md border border-[var(--separator)] px-2.5 py-1 text-xs text-[var(--danger)] hover:bg-[var(--hover-bg)] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  撤销
                </button>
              ) : (
                <button
                  type="button"
                  disabled={!entryId}
                  onClick={() => void restoreEntry(it)}
                  className="flex-shrink-0 rounded-md border border-[var(--separator)] px-2.5 py-1 text-xs text-green-400 hover:bg-[var(--hover-bg)] disabled:cursor-not-allowed disabled:opacity-40"
                >
                  恢复
                </button>
              )}
            </div>
          )
        })}

        {canLoadMore && (
          <button
            type="button"
            data-testid="memory-load-more"
            onClick={() => void fetchPage('active', true)}
            className="w-full rounded-md border border-[var(--separator)] py-2 text-xs text-[var(--text-secondary)] hover:bg-[var(--hover-bg)]"
          >
            {`加载更多（已加载 ${items.length} / ${total}）`}
          </button>
        )}
      </div>
    </section>
  )
}
