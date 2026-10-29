import { useCallback, useEffect, useState } from 'react'

import { getWsClient } from '../../utils/ai'

interface QueueTask {
  task_id: string
  queue_name?: string
  capability_id?: string
  status?: string
  priority?: number
  attempts?: number
  max_retries?: number
  created_at?: number
  updated_at?: number
  error?: string
}

const STATUS_COLORS: Record<string, string> = {
  pending: 'text-yellow-400', running: 'text-blue-400', completed: 'text-green-400',
  failed: 'text-red-400', dead_letter: 'text-purple-400', cancelled: 'text-gray-400',
}

function fmtTs(ts?: number): string {
  if (!ts) return '—'
  return new Date(ts * 1000).toLocaleString('zh-CN')
}

export function TaskQueueModule() {
  const [tasks, setTasks] = useState<QueueTask[]>([])
  const [dead, setDead] = useState<QueueTask[]>([])
  const [stats, setStats] = useState<Record<string, number> | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const [listRes, statRes, deadRes] = await Promise.all([
        client.call<{ success?: boolean; tasks?: QueueTask[] }>('task_queue.list', { limit: 100 }),
        client.call<{ success?: boolean; stats?: Record<string, number> }>('task_queue.stats', {}),
        client.call<{ success?: boolean; tasks?: QueueTask[] }>('task_queue.dead_letter.list', { limit: 50 }),
      ])
      setTasks(listRes?.tasks ?? [])
      setStats(statRes?.stats ?? null)
      setDead(deadRes?.tasks ?? [])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取任务队列失败')
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { void load() }, [load])

  const cancel = useCallback(async (taskId: string) => {
    setBusy(true)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      await client.call('task_queue.cancel', { task_id: taskId })
      void load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '取消失败')
    } finally { setBusy(false) }
  }, [load])

  const requeue = useCallback(async (taskId: string) => {
    setBusy(true)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      await client.call('task_queue.dead_letter.requeue', { task_id: taskId })
      void load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '重新入队失败')
    } finally { setBusy(false) }
  }, [load])

  const purge = useCallback(async () => {
    setBusy(true)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const res = await client.call<{ success?: boolean; purged?: number }>('task_queue.purge_completed', { older_than_seconds: 3600 })
      setError(res?.success ? `已清理 ${res.purged ?? 0} 条已完成任务` : '清理失败')
      void load()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '清理失败')
    } finally { setBusy(false) }
  }, [load])

  const statKeys = ['pending', 'running', 'completed', 'failed', 'dead_letter', 'cancelled']

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-auto bg-[var(--body-bg)] p-6" aria-labelledby="tq-heading" data-testid="taskqueue-workspace">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--separator)] pb-5">
        <div>
          <span className="agent-kicker">TASK QUEUE</span>
          <h1 id="tq-heading" className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">任务队列</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">持久任务队列（P1.2）：资讯抓取 / URL 学习等后台任务，崩溃自动恢复、失败指数退避重试。</p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => void purge()} disabled={busy} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">清理已完成</button>
          <button type="button" onClick={() => void load()} disabled={loading} className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50">{loading ? '读取中...' : '刷新'}</button>
        </div>
      </header>

      {error && <div role="alert" className="mt-5 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-4 text-sm text-[var(--danger)]">{error}</div>}

      <div className="mt-5 grid gap-3 md:grid-cols-3 lg:grid-cols-6">
        {statKeys.map((k) => (
          <div key={k} className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-3.5">
            <span className="block text-xs text-[var(--text-muted)]">{k === 'dead_letter' ? '死信' : k}</span>
            <strong className={`mt-1 block text-lg ${STATUS_COLORS[k] || 'text-[var(--text-primary)]'}`}>{stats?.[k] ?? 0}</strong>
          </div>
        ))}
      </div>

      <main className="mt-4 min-h-0 flex-1">
        {loading ? (
          <p className="py-12 text-center text-sm text-[var(--text-muted)]">正在读取任务队列...</p>
        ) : (
          <div className="overflow-auto rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)]">
            <table className="w-full min-w-[760px] text-xs">
              <thead>
                <tr className="border-b border-[var(--separator)]">
                  <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">任务 ID</th>
                  <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">能力</th>
                  <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">队列</th>
                  <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">状态</th>
                  <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">重试</th>
                  <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">创建时间</th>
                  <th className="px-3 py-2 text-right text-[var(--text-muted)] font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {tasks.length === 0 && (
                  <tr><td colSpan={7} className="px-3 py-10 text-center text-sm text-[var(--text-muted)]">队列为空</td></tr>
                )}
                {tasks.map((t) => {
                  const st = t.status ?? ''
                  return (
                    <tr key={t.task_id} className="border-b border-[var(--separator)] last:border-b-0">
                      <td className="px-3 py-2.5 font-mono text-xs text-[var(--text-secondary)]">{t.task_id.slice(0, 12)}</td>
                      <td className="px-3 py-2.5 text-[var(--text-primary)]">{t.capability_id || '—'}</td>
                      <td className="px-3 py-2.5 text-[var(--text-muted)]">{t.queue_name || 'default'}</td>
                      <td className="px-3 py-2.5"><span className={`text-xs font-medium ${STATUS_COLORS[st] || 'text-[var(--text-muted)]'}`}>{st || '—'}</span></td>
                      <td className="px-3 py-2.5 text-[var(--text-muted)]">{t.attempts ?? 0}/{t.max_retries ?? 0}</td>
                      <td className="px-3 py-2.5 text-[var(--text-muted)] text-xs">{fmtTs(t.created_at)}</td>
                      <td className="px-3 py-2.5 text-right">
                        {(st === 'pending' || st === 'running') && (
                          <button type="button" onClick={() => void cancel(t.task_id)} disabled={busy} className="rounded px-1.5 py-0.5 text-xs text-[var(--text-muted)] hover:text-[var(--danger)]">取消</button>
                        )}
                        {t.error && <span className="ml-1 text-xs text-[var(--text-muted)]" title={t.error}>⚠</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}

        {dead.length > 0 && (
          <div className="mt-4">
            <h2 className="mb-2 text-sm font-medium text-[var(--text-primary)]">死信队列（重试耗尽）</h2>
            <div className="overflow-auto rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)]">
              <table className="w-full min-w-[640px] text-xs">
                <thead>
                  <tr className="border-b border-[var(--separator)]">
                    <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">任务 ID</th>
                    <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">能力</th>
                    <th className="px-3 py-2 text-left text-[var(--text-muted)] font-medium">失败原因</th>
                    <th className="px-3 py-2 text-right text-[var(--text-muted)] font-medium">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {dead.map((t) => (
                    <tr key={t.task_id} className="border-b border-[var(--separator)] last:border-b-0">
                      <td className="px-3 py-2.5 font-mono text-xs text-[var(--text-secondary)]">{t.task_id.slice(0, 12)}</td>
                      <td className="px-3 py-2.5 text-[var(--text-primary)]">{t.capability_id || '—'}</td>
                      <td className="px-3 py-2.5 max-w-[360px] truncate text-xs text-[var(--text-muted)]" title={t.error}>{t.error || '—'}</td>
                      <td className="px-3 py-2.5 text-right">
                        <button type="button" onClick={() => void requeue(t.task_id)} disabled={busy} className="rounded px-1.5 py-0.5 text-xs text-fluent-blue hover:bg-[var(--hover-bg)]">重新入队</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </main>
    </section>
  )
}
