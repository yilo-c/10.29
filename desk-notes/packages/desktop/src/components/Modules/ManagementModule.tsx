import { useCallback, useEffect, useState } from 'react'

import { getWsClient } from '../../utils/ai'

/* ── 类型 ── */

interface EvolutionStatus {
  available?: boolean
  error?: string
  scanning?: boolean
  last_scan_at?: string
  pending_upgrades?: unknown[]
  upgrades?: { name?: string; status?: string; title?: string; description?: string }[]
}

interface AuditEntry {
  id?: string
  entry_id?: string
  action?: string
  resource_type?: string
  resource_id?: string
  created_at?: string
  ts?: string
  user?: string
  detail?: string
  status?: string
}

interface TimelineEntry {
  id?: string
  source_type?: string
  title?: string
  content?: string
  updated_at?: string
  created_at?: string
}

type MgmtTab = 'evolution' | 'audit' | 'timeline'

function fmtTs(v?: string): string {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString('zh-CN')
}

export function ManagementModule() {
  const [tab, setTab] = useState<MgmtTab>('evolution')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  /* 进化 */
  const [evoStatus, setEvoStatus] = useState<EvolutionStatus | null>(null)
  const [evoBriefing, setEvoBriefing] = useState<{ title?: string; content?: string } | null>(null)
  const [evoResults, setEvoResults] = useState<Record<string, unknown>[]>([])

  /* 审计 */
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([])
  const [auditStats, setAuditStats] = useState<Record<string, unknown> | null>(null)

  /* 时间线 */
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const [timelineCounts, setTimelineCounts] = useState<Record<string, number>>({})

  const loadEvolution = useCallback(async () => {
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const [st, br, rs] = await Promise.all([
        client.call<EvolutionStatus>('evolution.status', {}),
        client.call<{ briefing?: { title?: string; content?: string } | null }>('evolution.last_briefing', {}),
        client.call<Record<string, unknown>[]>('evolution.list_results', { limit: 10 }),
      ])
      setEvoStatus(st)
      setEvoBriefing(br?.briefing ?? null)
      setEvoResults(Array.isArray(rs) ? rs : [])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取进化状态失败')
    }
  }, [])

  const loadAudit = useCallback(async () => {
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const [ls, st] = await Promise.all([
        client.call<{ entries?: AuditEntry[] }>('audit.list', { limit: 50 }),
        client.call<Record<string, unknown>>('audit.stats', {}),
      ])
      setAuditEntries(ls?.entries ?? [])
      setAuditStats(st ?? null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取审计日志失败')
    }
  }, [])

  const loadTimeline = useCallback(async () => {
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<{ entries?: TimelineEntry[]; counts_by_type?: Record<string, number> }>('timeline.list', { limit: 100 })
      setTimeline(res?.entries ?? [])
      setTimelineCounts(res?.counts_by_type ?? {})
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取时间线失败')
    }
  }, [])

  useEffect(() => {
    setError(null)
    if (tab === 'evolution') void loadEvolution()
    if (tab === 'audit') void loadAudit()
    if (tab === 'timeline') void loadTimeline()
  }, [tab, loadEvolution, loadAudit, loadTimeline])

  const scanNow = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<{ error?: string }>('evolution.scan_and_report', {})
      if (res?.error) setError(res.error)
      else { setError('扫描完成'); void loadEvolution() }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '扫描失败')
    } finally { setBusy(false) }
  }, [loadEvolution])

  const evoAction = useCallback(async (kind: 'confirm' | 'final' | 'skip', name: string) => {
    setBusy(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const method = kind === 'confirm' ? 'evolution.confirm_upgrade' : kind === 'final' ? 'evolution.final_apply' : 'evolution.skip_upgrade'
      const res = await client.call<{ error?: string }>(method, { name })
      if (res?.error) setError(res.error)
      else { setError(`${kind} 完成: ${name}`); void loadEvolution() }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作失败')
    } finally { setBusy(false) }
  }, [loadEvolution])

  const pendingUpgrades = evoStatus?.upgrades ?? evoStatus?.pending_upgrades ?? []
  const auditCount = (auditStats as { total?: number } | null)?.total ?? auditEntries.length

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-auto bg-[var(--body-bg)] p-6" aria-labelledby="mgmt-heading" data-testid="management-workspace">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--separator)] pb-5">
        <div>
          <span className="agent-kicker">MANAGEMENT</span>
          <h1 id="mgmt-heading" className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">系统管理</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">受控进化 · 审计回滚 · 时间线（P4.1 / S15 / P2.4）</p>
        </div>
      </header>

      <nav className="mt-4 flex items-center gap-1 border-b border-[var(--separator)]" aria-label="管理功能">
        {([['evolution', '受控进化'], ['audit', '审计回滚'], ['timeline', '时间线']] as [MgmtTab, string][]).map(([k, label]) => (
          <button key={k} type="button" onClick={() => setTab(k)} className={`rounded-t-md px-4 py-2 text-sm transition-colors ${tab === k ? 'border-b-2 border-fluent-blue font-medium text-[var(--text-primary)]' : 'text-[var(--text-muted)] hover:text-[var(--text-primary)]'}`}>
            {label}
          </button>
        ))}
      </nav>

      {error && <div role="alert" className="mt-5 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-4 text-sm text-[var(--danger)]">{error}</div>}

      {/* ── 受控进化 ── */}
      {tab === 'evolution' && (
        <div className="mt-5 space-y-4">
          {evoStatus && evoStatus.available === false && (
            <div className="rounded-lg border border-[var(--warning)]/30 bg-[var(--warning-soft)] p-4 text-sm text-[var(--warning)]">进化编排器不可用（{evoStatus.error ?? '未初始化'}）</div>
          )}
          <div className="flex items-center gap-3">
            <button type="button" onClick={() => void scanNow()} disabled={busy} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">
              {busy ? '扫描中...' : '技术雷达扫描'}
            </button>
            {evoStatus?.last_scan_at && <span className="text-xs text-[var(--text-muted)]">上次扫描：{fmtTs(evoStatus.last_scan_at)}</span>}
          </div>

          {evoBriefing && (
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="mb-2 text-sm font-medium text-[var(--text-primary)]">{evoBriefing.title ?? '最近早报'}</h2>
              <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap text-xs leading-relaxed text-[var(--text-secondary)]">{evoBriefing.content ?? '（无内容）'}</pre>
            </div>
          )}

          {pendingUpgrades.length > 0 && (
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">待处理升级项</h2>
              <div className="space-y-2">
                {pendingUpgrades.map((u, i) => {
                  const name = typeof u === 'string' ? u : (u as { name?: string }).name ?? `item-${i}`
                  const title = typeof u === 'string' ? u : (u as { title?: string }).title ?? name
                  const desc = typeof u === 'string' ? '' : (u as { description?: string }).description ?? ''
                  const status = typeof u === 'string' ? 'pending' : (u as { status?: string }).status ?? 'pending'
                  return (
                    <div key={name} className="flex items-start justify-between gap-3 rounded-md border border-[var(--separator)] px-3 py-2.5">
                      <div className="min-w-0">
                        <div className="text-xs font-medium text-[var(--text-primary)]">{title}</div>
                        {desc && <div className="mt-0.5 truncate text-xs text-[var(--text-muted)]">{desc}</div>}
                        <div className="mt-0.5 text-xs text-[var(--text-muted)]">状态：{status}</div>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        <button type="button" onClick={() => void evoAction('confirm', name)} disabled={busy} className="rounded px-1.5 py-0.5 text-xs text-fluent-blue hover:bg-[var(--hover-bg)]">确认升级</button>
                        <button type="button" onClick={() => void evoAction('final', name)} disabled={busy} className="rounded px-1.5 py-0.5 text-xs text-green-400 hover:bg-[var(--hover-bg)]">最终应用</button>
                        <button type="button" onClick={() => void evoAction('skip', name)} disabled={busy} className="rounded px-1.5 py-0.5 text-xs text-[var(--text-muted)] hover:bg-[var(--hover-bg)]">跳过</button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {evoResults.length > 0 && (
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-5">
              <h2 className="mb-3 text-sm font-medium text-[var(--text-primary)]">升级历史</h2>
              <div className="space-y-1.5">
                {evoResults.map((r, i) => (
                  <div key={i} className="rounded border border-[var(--separator)] px-3 py-2 text-xs">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-[var(--text-primary)]">{String((r as { name?: unknown }).name ?? `result-${i}`)}</span>
                      <span className="text-[var(--text-muted)]">{String((r as { status?: unknown }).status ?? '')}</span>
                    </div>
                    {Boolean((r as { error?: unknown }).error) && <div className="mt-1 text-[var(--danger)]">{String((r as { error?: unknown }).error)}</div>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {pendingUpgrades.length === 0 && !evoBriefing && evoResults.length === 0 && (
            <p className="py-10 text-center text-sm text-[var(--text-muted)]">暂无进化数据，点击「技术雷达扫描」开始。</p>
          )}
        </div>
      )}

      {/* ── 审计回滚 ── */}
      {tab === 'audit' && (
        <div className="mt-5">
          <div className="mb-3 grid gap-3 md:grid-cols-3">
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3.5">
              <span className="text-xs text-[var(--text-muted)]">审计记录</span>
              <strong className="mt-1 block text-lg text-[var(--text-primary)]">{auditCount}</strong>
            </div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3.5">
              <span className="text-xs text-[var(--text-muted)]">回滚操作</span>
              <strong className="mt-1 block text-lg text-[var(--text-primary)]">{Number((auditStats as { rollbacks?: number } | null)?.rollbacks ?? 0)}</strong>
            </div>
            <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3.5">
              <span className="text-xs text-[var(--text-muted)]">资源类型</span>
              <strong className="mt-1 block text-sm text-[var(--text-primary)]">{Object.keys((auditStats as { by_resource?: Record<string, number> } | null)?.by_resource ?? {}).length || '—'}</strong>
            </div>
          </div>
          <div className="overflow-auto rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)]">
            <table className="w-full min-w-[720px] text-xs">
              <thead>
                <tr className="border-b border-[var(--separator)] text-left text-[var(--text-muted)]">
                  <th className="px-3 py-2 font-medium">操作</th>
                  <th className="px-3 py-2 font-medium">资源</th>
                  <th className="px-3 py-2 font-medium">ID</th>
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                </tr>
              </thead>
              <tbody>
                {auditEntries.length === 0 && (
                  <tr><td colSpan={5} className="px-3 py-10 text-center text-sm text-[var(--text-muted)]">暂无审计记录</td></tr>
                )}
                {auditEntries.map((e) => (
                  <tr key={e.id ?? e.entry_id ?? e.ts ?? Math.random()} className="border-b border-[var(--separator)] last:border-b-0">
                    <td className="px-3 py-2 text-[var(--text-primary)]">{e.action || '—'}</td>
                    <td className="px-3 py-2 text-[var(--text-secondary)]">{e.resource_type || '—'}</td>
                    <td className="px-3 py-2 font-mono text-xs text-[var(--text-muted)]">{e.resource_id || '—'}</td>
                    <td className="px-3 py-2 text-[var(--text-muted)] text-xs">{fmtTs(e.created_at ?? e.ts)}</td>
                    <td className="px-3 py-2 text-[var(--text-muted)]">{e.status || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── 时间线 ── */}
      {tab === 'timeline' && (
        <div className="mt-5">
          {Object.keys(timelineCounts).length > 0 && (
            <div className="mb-3 flex flex-wrap gap-2">
              {Object.entries(timelineCounts).map(([k, v]) => (
                <span key={k} className="rounded-full bg-fluent-blue/10 px-2.5 py-0.5 text-xs text-fluent-blue">{k}: {v}</span>
              ))}
            </div>
          )}
          <div className="space-y-2">
            {timeline.length === 0 && (
              <p className="py-10 text-center text-sm text-[var(--text-muted)]">暂无时间线数据</p>
            )}
            {timeline.map((t) => (
              <div key={t.id ?? `${t.source_type}-${t.updated_at ?? t.created_at ?? Math.random()}`} className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-xs font-medium text-[var(--text-primary)]">{t.title || '（无标题）'}</span>
                  <span className="shrink-0 text-xs text-[var(--text-muted)]">{fmtTs(t.updated_at ?? t.created_at)}</span>
                </div>
                <div className="mt-1 flex items-center gap-2">
                  <span className="rounded bg-fluent-blue/10 px-1.5 py-0.5 text-2xs text-fluent-blue">{t.source_type || 'unknown'}</span>
                  {t.content && <span className="truncate text-xs text-[var(--text-muted)]">{t.content}</span>}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
