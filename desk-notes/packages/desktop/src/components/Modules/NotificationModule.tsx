import { useCallback, useEffect, useMemo, useState } from 'react'
import { getWsClient } from '../../utils/ai'

export interface NotificationItem {
  id: string
  event_type: string
  title: string
  body: string
  priority: string
  created_at: string
  read: boolean
}

export interface NotificationListResponse {
  items?: NotificationItem[]
  error?: string
}

export interface NotificationStats {
  total: number
  unread: number
  by_event: Record<string, number>
  by_priority: Record<string, number>
  channels: { name: string; healthy: boolean }[]
  subscribed_events: string[]
  enabled: boolean
}

/** 与后端 _EVENT_TEMPLATES 对齐的事件中文名。 */
const EVENT_LABELS: Readonly<Record<string, string>> = {
  'inbox.delivered': '新收件箱条目',
  'proactive.triggered': '主动建议',
  'vocabulary.due_reminder': '单词到期复习',
  'task.due': '任务到期',
  'sync.failed': '同步失败',
  'job_hunter.new_jobs': '招聘新岗位',
  'ai.usage_alert': 'AI 用量告警',
}

/** 与后端 _PRIORITY_RANK 对齐的优先级排序。 */
const PRIORITY_RANK: Readonly<Record<string, number>> = { low: 0, normal: 1, high: 2, urgent: 3 }

export function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? eventType
}

/** 优先级是否达到 high / urgent（后端 toast 阈值）。 */
export function isHighPriority(priority: string): boolean {
  const rank = PRIORITY_RANK[priority]
  return (rank ?? PRIORITY_RANK.normal) >= PRIORITY_RANK.high
}

export function formatRelativeTime(iso: string, now = Date.now()): string {
  const time = new Date(iso).getTime()
  if (Number.isNaN(time)) return ''
  const minutes = Math.max(0, Math.floor((now - time) / 60000))
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return new Date(iso).toLocaleDateString()
}

type FilterMode = 'all' | 'unread'

export function NotificationModule() {
  const [items, setItems] = useState<NotificationItem[]>([])
  const [stats, setStats] = useState<NotificationStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<FilterMode>('all')
  const [acting, setActing] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      const [listResult, statsResult] = await Promise.all([
        client.call<NotificationListResponse>('notification.list', { unread_only: false, limit: 100 }),
        client.call<NotificationStats>('notification.stats', {}),
      ])
      if (listResult?.error) throw new Error(listResult.error)
      setItems(Array.isArray(listResult?.items) ? listResult.items : [])
      setStats(statsResult ?? null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取通知失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 实时：后端通过 notification.delivered 推送新通知
  useEffect(() => {
    const client = getWsClient()
    if (!client) return
    return client.on('notification.delivered', () => {
      void load()
    })
  }, [load])

  const applyMutation = useCallback(
    async (fn: () => Promise<unknown>) => {
      setError(null)
      try {
        await fn()
        await load()
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '操作失败')
      }
    },
    [load],
  )

  const markRead = useCallback(
    (id: string) => {
      setActing(id)
      return applyMutation(async () => {
        const client = getWsClient()
        if (!client) throw new Error('后端未连接')
        await client.call('notification.mark_read', { notification_id: id, read: true })
      }).finally(() => setActing(null))
    },
    [applyMutation],
  )

  const markAllRead = useCallback(() => {
    return applyMutation(async () => {
      const client = getWsClient()
      if (!client) throw new Error('后端未连接')
      await client.call('notification.mark_all_read', {})
    })
  }, [applyMutation])

  const remove = useCallback(
    (id: string) => {
      setActing(id)
      return applyMutation(async () => {
        const client = getWsClient()
        if (!client) throw new Error('后端未连接')
        await client.call('notification.delete', { notification_id: id })
      }).finally(() => setActing(null))
    },
    [applyMutation],
  )

  const visible = useMemo(
    () => (filter === 'unread' ? items.filter((n) => !n.read) : items),
    [filter, items],
  )
  const unreadCount = stats?.unread ?? items.filter((n) => !n.read).length
  const busy = acting !== null

  return (
    <section className="flex min-h-0 flex-1 flex-col overflow-auto bg-[var(--body-bg)] p-6" aria-labelledby="notification-heading" data-testid="notification-workspace">
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--separator)] pb-5">
        <div>
          <span className="agent-kicker">NOTIFICATION CENTER</span>
          <h1 id="notification-heading" className="mt-1 text-2xl font-semibold text-[var(--text-primary)]">通知中心</h1>
          <p className="mt-2 text-sm text-[var(--text-muted)]">来自收件箱、任务、单词与同步事件的通知。</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void markAllRead()}
            disabled={busy || unreadCount === 0}
            className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50"
          >
            全部已读
          </button>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="rounded-md border border-[var(--separator)] px-3 py-2 text-xs text-[var(--text-secondary)] disabled:opacity-50"
          >
            {loading ? '读取中...' : '刷新'}
          </button>
        </div>
      </header>

      {error && (
        <div role="alert" className="mt-5 rounded-lg border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-4 text-sm text-[var(--danger)]">
          {error}
        </div>
      )}

      <div className="mt-5 grid gap-4 md:grid-cols-3">
        <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
          <span className="text-xs text-[var(--text-muted)]">通知总数</span>
          <strong className="mt-2 block text-lg text-[var(--text-primary)]">{stats?.total ?? items.length}</strong>
        </div>
        <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
          <span className="text-xs text-[var(--text-muted)]">未读</span>
          <strong className="mt-2 block text-lg text-[var(--text-primary)]">{unreadCount}</strong>
        </div>
        <div className="rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4">
          <span className="text-xs text-[var(--text-muted)]">推送通道</span>
          <strong className="mt-2 block text-sm text-[var(--text-secondary)]">
            {(stats?.channels ?? []).map((ch) => ch.name).join(' / ') || '未连接'}
          </strong>
        </div>
      </div>

      <div className="mt-5 flex items-center gap-1">
        {(['all', 'unread'] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            onClick={() => setFilter(mode)}
            className={`rounded-md px-3 py-1.5 text-xs transition-colors ${
              filter === mode
                ? 'text-fluent-blue bg-fluent-blue/10'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--hover-bg)]'
            }`}
          >
            {mode === 'all' ? '全部' : '未读'}
          </button>
        ))}
      </div>

      <main className="mt-4 min-h-0 flex-1">
        {loading ? (
          <p className="py-12 text-center text-sm text-[var(--text-muted)]">正在读取通知...</p>
        ) : visible.length === 0 ? (
          <p className="py-12 text-center text-sm text-[var(--text-muted)]">
            {filter === 'unread' ? '没有未读通知' : '暂无通知'}
          </p>
        ) : (
          <ul className="space-y-2">
            {visible.map((n) => (
              <li
                key={n.id}
                className={`flex items-start gap-3 rounded-lg border border-[var(--separator)] bg-[var(--panel-bg-solid)] p-4 ${!n.read ? 'border-l-2 border-l-fluent-blue' : ''}`}
                data-testid={`notification-item-${n.id}`}
              >
                <span
                  aria-hidden="true"
                  className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${n.read ? 'bg-transparent' : 'bg-fluent-blue'}`}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-semibold text-[var(--text-primary)]">{eventLabel(n.event_type)}</span>
                    {isHighPriority(n.priority) && (
                      <span className="rounded bg-[var(--danger-soft)] px-1.5 py-0.5 text-xs font-medium text-[var(--danger)]">
                        {n.priority === 'urgent' ? '紧急' : '高优先级'}
                      </span>
                    )}
                    <span className="ml-auto text-xs text-[var(--text-muted)]">{formatRelativeTime(n.created_at)}</span>
                  </div>
                  {n.title && <p className="mt-1 text-sm font-medium text-[var(--text-primary)]">{n.title}</p>}
                  {n.body && <p className="mt-0.5 text-xs leading-relaxed text-[var(--text-secondary)]">{n.body}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {!n.read && (
                    <button
                      type="button"
                      onClick={() => void markRead(n.id)}
                      disabled={busy}
                      aria-label={`标记 ${n.title || '通知'} 已读`}
                      className="rounded-md border border-[var(--separator)] px-2 py-1 text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-50"
                    >
                      已读
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => void remove(n.id)}
                    disabled={busy}
                    aria-label={`删除 ${n.title || '通知'}`}
                    className="rounded-md border border-[var(--separator)] px-2 py-1 text-xs text-[var(--text-muted)] hover:text-[var(--danger)] disabled:opacity-50"
                  >
                    删除
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </main>
    </section>
  )
}
