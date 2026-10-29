import { useCallback, useEffect, useState } from 'react'
import { getWsClient } from '../../utils/ai'
import { useTranslation } from '../../i18n'

type TFn = ReturnType<typeof useTranslation>['t']

export interface CronJob {
  job_id: string
  name: string
  action_id: string
  schedule?: string
  repeat?: string
  run_at?: string
  enabled: boolean
  next_run_at?: string
  last_run?: string
  payload?: Record<string, unknown>
  history: unknown[]
  retry_count?: number
  max_retries?: number
}

interface CronListResponse {
  success: boolean
  jobs: CronJob[]
  count?: number
  reason?: string
}

interface CronStatusResponse {
  success: boolean
  status: { running: boolean; engine: string; job_count: number; last_error: string }
}

interface CronHistoryResponse {
  success: boolean
  job_id: string
  history: unknown[]
}

interface CronToggleResponse {
  success: boolean
  job_id: string
  enabled: boolean
}

interface CronRunNowResponse {
  success: boolean
  job_id: string
  result?: string
  reason?: string
}

interface CronToggleBatchResponse {
  success: boolean
  changed_count?: number
  not_found?: string[]
  enabled?: boolean
  error?: string
}

interface CronAddResponse {
  success: boolean
  job?: CronJob
  error?: string
}

interface CronAction {
  action_id: string
  description?: string
}

interface CronListActionsResponse {
  success: boolean
  actions: CronAction[]
  error?: string
}

function jobStatus(job: CronJob, t: TFn): string {
  return job.enabled ? t('automation.statusRunning') : t('automation.statusDisabled')
}

// U2（S3-5）：「自主任务」的前端分类——周期性自动运行的 job。
// 判据：repeat ∈ 周期集合，或带 cron 表达式 schedule（覆盖 repeat 缺省的
// 历史数据形状）。一次性任务（repeat=once/none 且无 schedule）不算。
// 后端只提供通用批量原语 cron.toggle_batch，"哪些算自主任务"是产品语义，
// 由前端挑选 job_ids。
const AUTONOMOUS_REPEATS = new Set(['cron', 'daily', 'weekly', 'monthly', 'interval'])
export function isAutonomousJob(job: CronJob): boolean {
  const repeat = String(job.repeat || '').toLowerCase()
  if (AUTONOMOUS_REPEATS.has(repeat)) return true
  return repeat === '' && Boolean(String(job.schedule || '').trim())
}

function formatNextRun(iso: string | undefined, t: TFn): string {
  if (!iso) return t('automation.notScheduled')
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

export function AutomationModule() {
  const { t } = useTranslation()
  const [jobs, setJobs] = useState<CronJob[]>([])
  const [status, setStatus] = useState<CronStatusResponse['status'] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [acting, setActing] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  // 每个 job 的独立 history 缓存
  const [histories, setHistories] = useState<Record<string, unknown[]>>({})

  // U2 修复：cron.list 必须带 include_disabled=true，否则后端只返回启用项，
  // 「恢复自主任务」拿不到停用 job，静音后永远无法恢复（P0）。
  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error(t('automation.backendDisconnected'))
      const [listResult, statusResult] = await Promise.all([
        client.call<CronListResponse>('cron.list', { include_disabled: true }),
        client.call<CronStatusResponse>('cron.status', {}),
      ])
      if (!listResult?.success) throw new Error(listResult?.reason || t('automation.listUnavailable'))
      setJobs(Array.isArray(listResult.jobs) ? listResult.jobs : [])
      if (statusResult?.success) setStatus(statusResult.status)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('automation.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => { void load() }, [load])

  const applyMutation = useCallback(
    async (id: string, fn: () => Promise<unknown>) => {
      setActing(id)
      setError(null)
      setNotice(null)
      try {
        await fn()
        await load()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : t('automation.opFailed'))
      } finally {
        setActing(null)
      }
    },
    [load, t],
  )

  const toggle = useCallback(
    (jid: string, enabled: boolean) => {
      return applyMutation(jid, async () => {
        const client = getWsClient()
        if (!client) throw new Error(t('automation.backendDisconnected'))
        const result = await client.call<CronToggleResponse>('cron.toggle', {
          job_id: jid,
          enabled,
        })
        if (!result?.success) throw new Error(t('automation.toggleFailed', { jobId: jid }))
      })
    },
    [applyMutation, t],
  )

  const runNow = useCallback(
    (jid: string) => {
      return applyMutation(jid, async () => {
        const client = getWsClient()
        if (!client) throw new Error(t('automation.backendDisconnected'))
        const result = await client.call<CronRunNowResponse>('cron.run_now', {
          job_id: jid,
        })
        if (!result?.success) throw new Error(result?.reason || t('automation.runNowFailed', { jobId: jid }))
      })
    },
    [applyMutation, t],
  )

  // U2（S3-5）：一键静音/恢复全部自主任务（周期性自动运行的 job）。
  // 走后端 cron.toggle_batch（一次锁一次落盘），非前端循环 N 次 toggle。
  // 静音 targets = enabled 的周期 job；恢复 targets = 停用的周期 job
  // （依赖 load() 带 include_disabled 拉全量）。
  const [batchActing, setBatchActing] = useState(false)
  const muteAllAutonomous = useCallback(
    async (enabled: boolean) => {
      const targets = jobs.filter(isAutonomousJob).filter((j) => j.enabled === !enabled)
      if (targets.length === 0) {
        setNotice(null)
        setError(enabled ? t('automation.allActive') : t('automation.allMuted'))
        return
      }
      if (!window.confirm(enabled ? t('automation.restoreConfirm', { count: targets.length }) : t('automation.muteConfirm', { count: targets.length }))) return
      setBatchActing(true)
      setError(null)
      setNotice(null)
      try {
        const client = getWsClient()
        if (!client) throw new Error(t('automation.backendDisconnected'))
        const result = await client.call<CronToggleBatchResponse>('cron.toggle_batch', {
          job_ids: targets.map((j) => j.job_id),
          enabled,
        })
        // not_found 非空但 success=true 时仍按成功处理，仅在提示中附注数量。
        if (!result?.success) throw new Error(result?.error || (enabled ? t('automation.restoreFailed') : t('automation.muteFailed')))
        const changed = typeof result.changed_count === 'number' ? result.changed_count : targets.length
        let message = enabled ? t('automation.restoredCount', { count: changed }) : t('automation.mutedCount', { count: changed })
        const missing = Array.isArray(result.not_found) ? result.not_found.length : 0
        if (missing > 0) message += ` ${t('automation.notFoundCount', { count: missing })}`
        setNotice(message)
        await load()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : (enabled ? t('automation.restoreFailed') : t('automation.muteFailed')))
      } finally {
        setBatchActing(false)
      }
    },
    [jobs, load, t],
  )

  const loadHistory = useCallback(async (jid: string) => {
    try {
      const client = getWsClient()
      if (!client) return
      const result = await client.call<CronHistoryResponse>('cron.history', {
        job_id: jid,
        limit: 20,
      })
      if (result?.success) {
        setHistories((prev) => ({ ...prev, [jid]: Array.isArray(result.history) ? result.history : [] }))
      }
    } catch {
      // 静默失败，不影响主列表
    }
  }, [])

  const toggleExpand = useCallback(
    (jid: string) => {
      setExpanded((prev) => {
        const next = new Set(prev)
        if (next.has(jid)) {
          next.delete(jid)
        } else {
          next.add(jid)
          void loadHistory(jid)
        }
        return next
      })
    },
    [loadHistory],
  )

  // 新建任务
  const [showCreate, setShowCreate] = useState(false)

  // ESC 关闭新建任务弹窗（2026-10-16 E2E sweep 缺陷修复）
  useEffect(() => {
    if (!showCreate) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setShowCreate(false)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [showCreate])
  const [actions, setActions] = useState<CronAction[]>([])
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({
    name: '',
    action_id: '',
    repeat: 'interval',
    run_at: '08:00',
    run_every_days: '3',
    schedule: '0 8 * * *',
    max_retries: '2',
    retry_backoff: '60',
    payload: '{"max_sessions":30,"since_hours":72}',
  })

  const setField = useCallback(<K extends keyof typeof form>(key: K, value: (typeof form)[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }, [])

  const openCreate = useCallback(() => {
    setActions([])
    setForm((prev) => ({ ...prev, name: '', action_id: '' }))
    const client = getWsClient()
    if (!client) return
    client
      .call<CronListActionsResponse>('ai_chat.list_actions', {})
      .then((res) => {
        setActions(Array.isArray(res?.actions) ? res.actions : [])
        // 默认选中 AI 平台聊天同步动作
        setForm((prev) => ({
          ...prev,
          action_id: res?.actions?.find((a) => a.action_id === 'ai_chat.collect')?.action_id ?? res?.actions?.[0]?.action_id ?? '',
        }))
      })
      .catch(() => setActions([]))
    setShowCreate(true)
  }, [])

  const createJob = useCallback(async () => {
    setCreating(true)
    setError(null)
    try {
      const name = form.name.trim()
      const actionId = form.action_id.trim()
      if (!name) throw new Error(t('automation.nameRequired'))
      if (!actionId) throw new Error(t('automation.actionRequired'))
      const repeat = form.repeat
      const runAt = repeat === 'cron' ? '' : form.run_at.trim()
      let payload: Record<string, unknown> = {}
      try {
        payload = form.payload && form.payload.trim() ? JSON.parse(form.payload) : {}
      } catch {
        throw new Error(t('automation.payloadInvalid'))
      }
      const client = getWsClient()
      if (!client) throw new Error(t('automation.backendDisconnected'))
      const result = await client.call<CronAddResponse>('cron.add', {
        name,
        action_id: actionId,
        payload,
        schedule: repeat === 'cron' ? form.schedule.trim() : '',
        run_at: runAt,
        repeat,
        max_retries: Number(form.max_retries) || 0,
        retry_backoff: Number(form.retry_backoff) || 60,
        run_every_days: repeat === 'interval' ? Number(form.run_every_days) || 1 : 0,
      })
      if (!result?.success) throw new Error(result?.error || t('automation.createFailed'))
      setShowCreate(false)
      await load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('automation.createFailed'))
    } finally {
      setCreating(false)
    }
  }, [form, load, t])

  const busy = acting !== null

  return (
    <section
      className="automation-workspace"
      aria-labelledby="automation-heading"
      data-testid="automation-workspace"
    >
      <header className="automation-header">
        <div>
          <span className="agent-kicker">AUTOMATION</span>
          <h1 id="automation-heading">{t('automation.title')}</h1>
          <p>{t('automation.subtitle')}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="automation-refresh"
            onClick={() => void muteAllAutonomous(false)}
            disabled={batchActing || busy || loading}
            aria-label={t('automation.muteAllAria')}
            data-testid="automation-mute-all"
          >
            {batchActing ? t('automation.batchProcessing') : t('automation.muteAll')}
          </button>
          <button
            type="button"
            className="automation-refresh"
            onClick={() => void muteAllAutonomous(true)}
            disabled={batchActing || busy || loading}
            aria-label={t('automation.restoreAllAria')}
            data-testid="automation-restore-all"
          >
            {t('automation.restoreAll')}
          </button>
          <button
            type="button"
            className="automation-refresh"
            onClick={openCreate}
          >
            {t('automation.createTask')}
          </button>
          <button
            type="button"
            className="automation-refresh"
            onClick={() => void load()}
            disabled={loading}
          >
            {loading ? t('automation.refreshing') : t('automation.refresh')}
          </button>
        </div>
      </header>

      {error && <div className="automation-error" role="alert">{error}</div>}
      {notice && !error && <div className="automation-notice" role="status" data-testid="automation-notice">{notice}</div>}

      <div className="automation-summary" aria-label={t('automation.summaryAria')}>
        <div>
          <span>{t('automation.totalJobs')}</span>
          <strong>{jobs.length}</strong>
        </div>
        <div>
          <span>{t('automation.enabledJobs')}</span>
          <strong>{jobs.filter((j) => j.enabled).length}</strong>
        </div>
        <div>
          <span>{t('automation.engineLabel')}</span>
          <strong>{status?.engine || '—'}</strong>
        </div>
        <div>
          <span>{t('automation.engineStatus')}</span>
          <strong className={status?.running ? 'automation-status' : 'automation-status-off'}>
            {status?.running ? t('automation.engineRunning') : t('automation.engineStopped')}
          </strong>
        </div>
      </div>

      <main className="automation-table-wrap">
        {loading ? (
          <p className="automation-empty">{t('automation.loadingList')}</p>
        ) : jobs.length === 0 ? (
          <p className="automation-empty">{t('automation.empty')}</p>
        ) : (
          <table className="automation-table">
            <thead>
              <tr>
                <th>{t('automation.colTask')}</th>
                <th>{t('automation.colAction')}</th>
                <th>{t('automation.colSchedule')}</th>
                <th>{t('automation.colStatus')}</th>
                <th>{t('automation.colNextRun')}</th>
                <th>{t('automation.colOps')}</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => {
                const jid = job.job_id
                const isExpanded = expanded.has(jid)
                return (
                  <tr
                    key={jid}
                    data-testid={`cron-row-${jid}`}
                    data-disabled={job.enabled ? undefined : 'true'}
                    className={job.enabled ? undefined : 'automation-row-disabled'}
                  >
                    <td>
                      <strong>{job.name || t('automation.unnamed')}</strong>
                      {!job.enabled && (
                        <span className="automation-disabled-badge" data-testid={`cron-disabled-badge-${jid}`}>
                          {t('automation.disabledBadge')}
                        </span>
                      )}
                      <small>{jid}</small>
                    </td>
                    <td>
                      <span>{job.action_id || t('automation.unspecified')}</span>
                      {job.retry_count !== undefined && job.max_retries && job.retry_count > 0 && (
                        <small className="text-[var(--text-muted)]">
                          {t('automation.retryFmt', { count: job.retry_count, max: job.max_retries })}
                        </small>
                      )}
                    </td>
                    <td>{job.schedule || job.repeat || t('automation.onDemand')}</td>
                    <td>
                      <span
                        className={job.enabled ? 'automation-status' : 'automation-status automation-status-off'}
                      >
                        {jobStatus(job, t)}
                      </span>
                    </td>
                    <td>{formatNextRun(job.next_run_at, t)}</td>
                    <td>
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => void toggle(jid, !job.enabled)}
                          disabled={busy}
                          className="automation-action-btn"
                          aria-label={job.enabled ? t('automation.disableAria', { name: job.name }) : t('automation.enableAria', { name: job.name })}
                        >
                          {job.enabled ? t('automation.disable') : t('automation.enable')}
                        </button>
                        <button
                          type="button"
                          onClick={() => void runNow(jid)}
                          disabled={busy}
                          className="automation-action-btn"
                          aria-label={t('automation.runNowAria', { name: job.name })}
                        >
                          {t('automation.runNow')}
                        </button>
                        <button
                          type="button"
                          onClick={() => toggleExpand(jid)}
                          className="automation-action-btn"
                          aria-expanded={isExpanded}
                        >
                          {t('automation.history')}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}

        {/* 展开的执行历史区域 */}
        {[...expanded].map((jid) => {
          const hist = histories[jid] ?? []
          return (
            <div key={`${jid}-history`} className="automation-history" data-testid={`cron-history-${jid}`}>
              <div className="automation-history-header">
                <span>{t('automation.historyTitle', { jobId: jid })}</span>
                <button
                  type="button"
                  onClick={() => toggleExpand(jid)}
                  className="text-xs text-[var(--text-muted)]"
                >
                  {t('automation.collapse')}
                </button>
              </div>
              {hist.length === 0 ? (
                <p className="automation-empty">{t('automation.noHistory')}</p>
              ) : (
                <ul className="automation-history-list">
                  {hist.map((entry, idx) => {
                    const e = (entry ?? {}) as Record<string, unknown>
                    const ts = e.timestamp
                    const status = String(e.status ?? '')
                    const ok = e.success === true
                    const err = typeof e.error === 'string' ? e.error : ''
                    const out = typeof e.output === 'string' ? e.output : ''
                    // AC-2：dispatched 是"已入队未执行"的中性态（非失败），单独标注；
                    // 其余按 success 布尔渲染成功（绿）/ 失败（红 + error 摘要）。
                    let tone: 'ok' | 'err' | 'pending'
                    let label: string
                    if (status === 'dispatched') {
                      tone = 'pending'
                      label = t('automation.histDispatched')
                    } else if (ok) {
                      tone = 'ok'
                      label = t('automation.histSuccess')
                    } else {
                      tone = 'err'
                      label = t('automation.histFailed')
                    }
                    const detail = err || out
                    return (
                      <li key={idx} className="automation-history-item">
                        <span className="automation-history-time">
                          {ts ? new Date(String(ts)).toLocaleString('zh-CN') : `#${idx + 1}`}
                        </span>
                        <span
                          className={`automation-history-status automation-history-status--${tone}`}
                          title={detail || undefined}
                        >
                          {label}
                        </span>
                        {detail && (
                          <span className="automation-history-detail" title={detail}>
                            {detail}
                          </span>
                        )}
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          )
        })}
      </main>

      {/* 新建任务弹窗 */}
      {showCreate && (
        <div className="automation-modal" role="dialog" aria-modal="true" aria-labelledby="create-cron-heading" data-testid="create-cron-modal">
          <div className="automation-modal-body">
            <div className="automation-modal-title">
              <span id="create-cron-heading">{t('automation.createTitle')}</span>
              <button type="button" className="automation-modal-close" onClick={() => setShowCreate(false)} aria-label={t('automation.close')}>
                ×
              </button>
            </div>

            <label className="automation-form-label">
              {t('automation.nameLabel')}
              <input
                className="automation-input"
                value={form.name}
                onChange={(e) => setField('name', e.target.value)}
                placeholder={t('automation.namePlaceholder')}
              />
            </label>

            <label className="automation-form-label">
              {t('automation.actionLabel')}
              <select
                className="automation-input"
                value={form.action_id}
                onChange={(e) => setField('action_id', e.target.value)}
              >
                <option value="" disabled>{t('automation.selectAction')}</option>
                {actions.map((a) => (
                  <option key={a.action_id} value={a.action_id}>
                    {a.action_id}{a.description ? ` — ${a.description}` : ''}
                  </option>
                ))}
              </select>
            </label>

            <div className="automation-form-row">
              <label className="automation-form-label">
                {t('automation.repeatLabel')}
                <select
                  className="automation-input"
                  value={form.repeat}
                  onChange={(e) => setField('repeat', e.target.value)}
                >
                  <option value="interval">{t('automation.repeatInterval')}</option>
                  <option value="daily">{t('automation.repeatDaily')}</option>
                  <option value="weekly">{t('automation.repeatWeekly')}</option>
                  <option value="monthly">{t('automation.repeatMonthly')}</option>
                  <option value="cron">{t('automation.repeatCron')}</option>
                  <option value="none">{t('automation.repeatOnce')}</option>
                </select>
              </label>
              <label className="automation-form-label">
                {t('automation.runAtLabel')}
                <input
                  className="automation-input"
                  type="time"
                  value={form.run_at}
                  disabled={form.repeat === 'cron'}
                  onChange={(e) => setField('run_at', e.target.value)}
                />
              </label>
            </div>

            {form.repeat === 'interval' && (
              <label className="automation-form-label">
                {t('automation.intervalDaysLabel')}
                <input
                  className="automation-input"
                  type="number"
                  min={1}
                  value={form.run_every_days}
                  onChange={(e) => setField('run_every_days', e.target.value)}
                />
              </label>
            )}

            {form.repeat === 'cron' && (
              <label className="automation-form-label">
                {t('automation.cronLabel')} <span className="automation-hint">{t('automation.cronHint')}</span>
                <input
                  className="automation-input"
                  value={form.schedule}
                  onChange={(e) => setField('schedule', e.target.value)}
                  placeholder="0 8 * * *"
                />
              </label>
            )}

            <div className="automation-form-row">
              <label className="automation-form-label">
                {t('automation.maxRetriesLabel')}
                <input
                  className="automation-input"
                  type="number"
                  min={0}
                  value={form.max_retries}
                  onChange={(e) => setField('max_retries', e.target.value)}
                />
              </label>
              <label className="automation-form-label">
                {t('automation.backoffLabel')}
                <input
                  className="automation-input"
                  type="number"
                  min={0}
                  value={form.retry_backoff}
                  onChange={(e) => setField('retry_backoff', e.target.value)}
                />
              </label>
            </div>

            <label className="automation-form-label">
              {t('automation.payloadLabel')}
              <textarea
                className="automation-input automation-textarea"
                rows={3}
                value={form.payload}
                onChange={(e) => setField('payload', e.target.value)}
                spellCheck={false}
              />
            </label>

            <div className="automation-modal-actions">
              <button type="button" className="automation-action-btn" onClick={() => setShowCreate(false)} disabled={creating}>
                {t('automation.cancel')}
              </button>
              <button type="button" className="automation-create-btn" onClick={() => void createJob()} disabled={creating}>
                {creating ? t('automation.creating') : t('automation.createSubmit')}
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        .automation-action-btn {
          padding: 0.25rem 0.5rem;
          border: 1px solid var(--panel-border, rgba(0,0,0,0.08));
          border-radius: 4px;
          color: var(--text-secondary);
          background: var(--panel-bg-solid);
          font-size: 0.62rem;
          cursor: pointer;
          transition: color 0.15s, background 0.15s;
        }
        .automation-action-btn:hover:not(:disabled) {
          color: var(--text-primary);
          background: var(--hover-bg);
        }
        .automation-action-btn:disabled {
          cursor: wait;
          opacity: 0.5;
        }
        .automation-row-disabled {
          opacity: 0.6;
        }
        .automation-disabled-badge {
          display: inline-block;
          margin-left: 0.4rem;
          padding: 0.05rem 0.35rem;
          border-radius: 4px;
          border: 1px solid var(--panel-border, rgba(0,0,0,0.1));
          background: var(--hover-bg, rgba(0,0,0,0.04));
          color: var(--text-muted);
          font-size: 0.58rem;
          vertical-align: middle;
        }
        .automation-notice {
          margin: 0.5rem 0;
          padding: 0.5rem 0.75rem;
          border-radius: 6px;
          border: 1px solid var(--panel-border, rgba(0,0,0,0.08));
          background: var(--panel-bg-solid);
          color: var(--success, #2e9e5b);
          font-size: 0.72rem;
        }
        .automation-history {
          padding: 0.8rem;
          border-top: 1px solid var(--panel-border, rgba(0,0,0,0.04));
          background: var(--panel-bg-solid);
        }
        .automation-history-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding-bottom: 0.5rem;
          color: var(--text-muted);
          font-size: 0.68rem;
        }
        .automation-history-list {
          list-style: none;
          margin: 0;
          padding: 0;
        }
        .automation-history-item {
          padding: 0.35rem 0;
          border-bottom: 1px solid var(--panel-border, rgba(0,0,0,0.02));
          font-size: 0.65rem;
          color: var(--text-secondary);
        }
        .automation-modal {
          position: fixed;
          inset: 0;
          z-index: 100;
          display: flex;
          align-items: center;
          justify-content: center;
          background: rgba(0, 0, 0, 0.35);
          padding: 1rem;
        }
        .automation-modal-body {
          width: min(480px, 100%);
          max-height: 90vh;
          overflow: auto;
          padding: 1rem 1.1rem;
          border-radius: 10px;
          border: 1px solid var(--panel-border, rgba(0,0,0,0.1));
          background: var(--panel-bg-solid);
          box-shadow: 0 12px 40px rgba(0,0,0,0.25);
          color: var(--text-primary);
        }
        .automation-modal-title {
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-weight: 600;
          font-size: 0.95rem;
          margin-bottom: 0.8rem;
        }
        .automation-modal-close {
          border: none;
          background: transparent;
          color: var(--text-muted);
          font-size: 1.2rem;
          line-height: 1;
          cursor: pointer;
        }
        .automation-modal-close:hover {
          color: var(--text-primary);
        }
        .automation-form-label {
          display: block;
          margin-bottom: 0.6rem;
          font-size: 0.7rem;
          color: var(--text-secondary);
        }
        .automation-input {
          display: block;
          width: 100%;
          margin-top: 0.3rem;
          padding: 0.4rem 0.55rem;
          font-size: 0.8rem;
          color: var(--text-primary);
          background: var(--input-bg, var(--panel-bg-solid));
          border: 1px solid var(--panel-border, rgba(0,0,0,0.15));
          border-radius: 6px;
          outline: none;
        }
        .automation-input:focus {
          border-color: var(--accent, #4f8cff);
        }
        .automation-input:disabled {
          opacity: 0.5;
        }
        .automation-textarea {
          resize: vertical;
          font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
          font-size: 0.72rem;
        }
        .automation-form-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 0.6rem;
        }
        .automation-hint {
          color: var(--text-muted);
          font-weight: 400;
          font-size: 0.62rem;
        }
        .automation-modal-actions {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 0.5rem;
          margin-top: 1rem;
        }
        .automation-create-btn {
          padding: 0.35rem 0.9rem;
          border: none;
          border-radius: 6px;
          color: #fff;
          background: var(--accent, #4f8cff);
          font-size: 0.72rem;
          cursor: pointer;
          transition: opacity 0.15s;
        }
        .automation-create-btn:hover:not(:disabled) {
          opacity: 0.9;
        }
        .automation-create-btn:disabled {
          cursor: wait;
          opacity: 0.6;
        }
      `}</style>
    </section>
  )
}
