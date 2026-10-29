import { useCallback, useEffect, useState } from 'react'

import { getWsClient } from '../../utils/ai'

interface UsageStat {
  total_calls?: number
  total_tokens?: number
  total_cost?: number
  by_provider?: { provider?: string; calls?: number; tokens?: number; cost?: number }[]
  by_model?: { model?: string; calls?: number; tokens?: number; cost?: number }[]
  by_source?: { source?: string; calls?: number; tokens?: number; cost?: number }[]
}

interface DashboardData {
  enabled?: boolean
  days?: number
  error?: string
  today?: { calls?: number; tokens?: number; cost?: number; by_provider?: { provider?: string; cost?: number }[] }
  stats?: UsageStat
  budget?: { daily_token_limit?: number | null; daily_cost_limit?: number | null; token_usage?: number; cost_usage?: number; exceeded?: boolean } | null
  provider_health?: { provider?: string; healthy?: boolean; last_check_at?: number }[]
  tier_breakdown?: { today_cost?: number; today_calls?: number; tier_breakdown?: Record<string, number>; vs_pure_cloud_saving?: number } | null
  alert_state_today?: Record<string, boolean>
}

function fmtCost(v?: number): string {
  return `¥${(v ?? 0).toFixed(4)}`
}
function fmtTokens(v?: number): string {
  const n = v ?? 0
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`
  return String(n)
}

export function UsageDashboardModule() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [budget, setBudget] = useState<DashboardData['budget']>(null)
  const [days, setDays] = useState(7)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const [dashRes, budRes] = await Promise.all([
        client.call<DashboardData>('usage.dashboard', { days }),
        client.call<DashboardData['budget']>('usage.budget.get', {}),
      ])
      setData(dashRes)
      setBudget(budRes)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取用量数据失败')
    } finally { setLoading(false) }
  }, [days])

  useEffect(() => { void load() }, [load])

  const exportCsv = useCallback(async () => {
    setExporting(true)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const csv = await client.call<string>('usage.export', { days })
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `usage-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(url)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '导出失败')
    } finally { setExporting(false) }
  }, [days])

  const today = data?.today
  const stats = data?.stats
  const tier = data?.tier_breakdown?.tier_breakdown ?? {}
  const budgetStatus = data?.budget

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-auto bg-[var(--body-bg)] p-6" aria-labelledby="usage-heading" data-testid="usage-workspace">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--separator)] pb-5">
        <div>
          <span className="agent-kicker">USAGE</span>
          <h1 id="usage-heading" className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">AI 用量看板</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">调用 / Token / 成本监控与预算（P2.7）——欠费、限额一目了然。</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="rounded-md border border-[var(--separator)] bg-[var(--input-bg)] px-2.5 py-2 text-xs text-[var(--text-secondary)] outline-none">
            {[1, 7, 30].map((d) => <option key={d} value={d}>近 {d} 天</option>)}
          </select>
          <button type="button" onClick={() => void exportCsv()} disabled={exporting} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">{exporting ? '导出中...' : '导出 CSV'}</button>
          <button type="button" onClick={() => void load()} disabled={loading} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">{loading ? '读取中...' : '刷新'}</button>
        </div>
      </header>

      {error && <div role="alert" className="mt-5 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-4 text-sm text-[var(--danger)]">{error}</div>}
      {data && data.enabled === false && (
        <div role="alert" className="mt-5 rounded-lg border border-[var(--warning)]/30 bg-[var(--warning-soft)] p-4 text-sm text-[var(--warning)]">用量追踪器未初始化（{data.error ?? '未知原因'}）</div>
      )}

      <div className="mt-5 grid gap-4 md:grid-cols-4">
        <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
          <span className="text-xs text-[var(--text-muted)]">今日调用</span>
          <strong className="mt-2 block text-lg text-[var(--text-primary)]">{today?.calls ?? 0}</strong>
          <span className="mt-1 block text-xs text-[var(--text-muted)]">近 {data?.days ?? days} 天：{stats?.total_calls ?? 0}</span>
        </div>
        <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
          <span className="text-xs text-[var(--text-muted)]">今日 Token</span>
          <strong className="mt-2 block text-lg text-[var(--text-primary)]">{fmtTokens(today?.tokens)}</strong>
          <span className="mt-1 block text-xs text-[var(--text-muted)]">近 {data?.days ?? days} 天：{fmtTokens(stats?.total_tokens)}</span>
        </div>
        <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
          <span className="text-xs text-[var(--text-muted)]">今日成本</span>
          <strong className={`mt-2 block text-lg ${(today?.cost ?? 0) > 0 ? 'text-[var(--warning)]' : 'text-[var(--text-primary)]'}`}>{fmtCost(today?.cost)}</strong>
          <span className="mt-1 block text-xs text-[var(--text-muted)]">近 {data?.days ?? days} 天：{fmtCost(stats?.total_cost)}</span>
        </div>
        <div className={`rounded-lg border p-4 ${budgetStatus?.exceeded ? 'border-[var(--danger)]/40 bg-[var(--danger-soft)]' : 'border-[var(--separator)] bg-[var(--panel-bg-solid)]'}`}>
          <span className="text-xs text-[var(--text-muted)]">预算状态</span>
          {budgetStatus?.exceeded
            ? <strong className="mt-2 block text-lg text-[var(--danger)]">已超额</strong>
            : <strong className="mt-2 block text-lg text-[var(--text-primary)]">正常</strong>}
          <span className="mt-1 block text-xs text-[var(--text-muted)]">
            {budget?.daily_token_limit != null ? `Token 上限 ${fmtTokens(Number(budget.daily_token_limit))}` : '未设 Token 上限'}
            {budget?.daily_cost_limit != null ? ` · 成本上限 ${fmtCost(Number(budget.daily_cost_limit))}` : ''}
          </span>
        </div>
      </div>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
          <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">按 Provider</h2>
          {(!stats?.by_provider || stats.by_provider.length === 0) ? (
            <p className="py-6 text-center text-sm text-[var(--text-muted)]">暂无数据</p>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-[var(--separator)] text-left text-[var(--text-muted)]">
                  <th className="py-1.5 font-medium">服务商</th>
                  <th className="py-1.5 text-right font-medium">调用</th>
                  <th className="py-1.5 text-right font-medium">Token</th>
                  <th className="py-1.5 text-right font-medium">成本</th>
                </tr>
              </thead>
              <tbody>
                {stats.by_provider.map((p) => (
                  <tr key={p.provider ?? '?'} className="border-b border-[var(--separator)] last:border-b-0">
                    <td className="py-2 text-[var(--text-primary)]">{p.provider ?? '未知'}</td>
                    <td className="py-2 text-right text-[var(--text-secondary)]">{p.calls ?? 0}</td>
                    <td className="py-2 text-right text-[var(--text-secondary)]">{fmtTokens(p.tokens)}</td>
                    <td className="py-2 text-right text-[var(--text-secondary)]">{fmtCost(p.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
          <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">Tier 成本维度</h2>
          {!data?.tier_breakdown ? (
            <p className="py-6 text-center text-sm text-[var(--text-muted)]">暂无数据</p>
          ) : (
            <>
              <div className="mb-3 flex gap-6 text-xs">
                <div><span className="text-[var(--text-muted)]">今日成本 </span><span className="font-medium text-[var(--text-primary)]">{fmtCost(data.tier_breakdown.today_cost)}</span></div>
                <div><span className="text-[var(--text-muted)]">纯云端节省 </span><span className="font-medium text-green-400">{fmtCost(data.tier_breakdown.vs_pure_cloud_saving)}</span></div>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-[var(--separator)] text-left text-[var(--text-muted)]">
                    <th className="py-1.5 font-medium">层级</th>
                    <th className="py-1.5 text-right font-medium">成本</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(tier).map(([k, v]) => (
                    <tr key={k} className="border-b border-[var(--separator)] last:border-b-0">
                      <td className="py-2 text-[var(--text-primary)]">{k}</td>
                      <td className="py-2 text-right text-[var(--text-secondary)]">{fmtCost(Number(v))}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
        <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">Provider 健康</h2>
        {(!data?.provider_health || data.provider_health.length === 0) ? (
          <p className="py-4 text-center text-sm text-[var(--text-muted)]">暂无健康数据</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {data.provider_health.map((p) => (
              <span key={p.provider ?? '?'} className={`rounded-full px-3 py-1 text-xs ${p.healthy === false ? 'bg-[var(--danger-soft)] text-[var(--danger)]' : 'bg-green-500/10 text-green-400'}`}>
                {p.provider ?? '未知'} {p.healthy === false ? '异常' : '健康'}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="mt-4 rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
        <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">按来源 (Source)</h2>
        {(!stats?.by_source || stats.by_source.length === 0) ? (
          <p className="py-6 text-center text-sm text-[var(--text-muted)]">暂无数据</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-[var(--separator)] text-left text-[var(--text-muted)]">
                <th className="py-1.5 font-medium">来源</th>
                <th className="py-1.5 text-right font-medium">调用</th>
                <th className="py-1.5 text-right font-medium">Token</th>
                <th className="py-1.5 text-right font-medium">成本</th>
              </tr>
            </thead>
            <tbody>
              {stats.by_source.map((s) => (
                <tr key={s.source ?? '?'} className="border-b border-[var(--separator)] last:border-b-0">
                  <td className="py-2 text-[var(--text-primary)]">{s.source ?? '未知'}</td>
                  <td className="py-2 text-right text-[var(--text-secondary)]">{s.calls ?? 0}</td>
                  <td className="py-2 text-right text-[var(--text-secondary)]">{fmtTokens(s.tokens)}</td>
                  <td className="py-2 text-right text-[var(--text-secondary)]">{fmtCost(s.cost)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}
