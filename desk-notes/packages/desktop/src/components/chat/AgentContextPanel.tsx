import { useEffect, useState } from 'react'
import type { AgentEventView } from './agentEvents'
import Icon from '../Common/Icon'
import { getWsClient } from '../../utils/ai'
import { useTranslation } from '../../i18n'

/** 内核策略选项（与后端 agent_kernel.type 对应） */
export const KERNEL_POLICY_OPTIONS: Array<{ value: string; label: string }> = [
  { value: 'auto', label: '自动（探测+切换）' },
  { value: 'dsh', label: 'DSH' },
  { value: 'claude_code', label: 'Claude Code' },
  { value: 'codex', label: 'Codex' },
  { value: 'none', label: '关闭内核' },
]

interface AgentContextPanelProps {
  events: AgentEventView[]
  connected: boolean
  streaming: boolean
  onCollapse?: () => void
}

/** kernel.status 返回结构（与 StatusBar 一致） */
export interface KernelStatus {
  route: string
  effective_engine: string
  degraded: boolean
  gateway_present: boolean
  gateway_name: string
  kernel_type: string
  message: string
}

/** agent.list 返回结构 */
export interface AgentInfo {
  agent_id: string
  agent_type?: string
  display_name: string
  description?: string
  capabilities?: string[]
  is_available?: boolean
  status?: string
  cost_rank?: number
  last_error?: string
  invocation_count?: number
}

/** trace.stats 返回结构 */
export interface TraceStats {
  days?: number
  total_runs?: number
  by_type?: Record<string, number>
  by_status?: Record<string, number>
  success_rate?: number
}

/** usage.today 返回结构 */
export interface UsageToday {
  enabled?: boolean
  calls?: number
  tokens?: number
  cost?: number
}

function statusLabel(status: AgentEventView['status']): string {
  if (status === 'done') return '完成'
  if (status === 'attention') return '需关注'
  return '进行中'
}

export function AgentContextPanel({ events, connected, streaming, onCollapse }: AgentContextPanelProps) {
  const { t } = useTranslation()
  const [kernelStatus, setKernelStatus] = useState<KernelStatus | null>(null)
  const [agents, setAgents] = useState<AgentInfo[]>([])
  const [traceStats, setTraceStats] = useState<TraceStats | null>(null)
  const [usage, setUsage] = useState<UsageToday | null>(null)
  // 内核策略（当前配置 type + 保存状态）
  const [kernelPolicy, setKernelPolicy] = useState<string>('')
  const [policyLoaded, setPolicyLoaded] = useState(false)
  const [policySaving, setPolicySaving] = useState(false)
  const [policyFeedback, setPolicyFeedback] = useState<'saved' | 'failed' | null>(null)
  const plan = events.filter((event) => event.kind === 'plan').slice(-4)

  // 观测数据轮询（连接时拉取，30s 刷新）
  useEffect(() => {
    if (!connected) {
      setKernelStatus(null)
      setAgents([])
      setTraceStats(null)
      setUsage(null)
      setKernelPolicy('')
      setPolicyLoaded(false)
      return
    }
    const fetchAll = async () => {
      const client = getWsClient()
      try {
        const ks = await client.call<KernelStatus>('kernel.status')
        setKernelStatus(ks)
      } catch { /* 内核状态非关键 */ }
      try {
        const r = await client.call<{ agents?: AgentInfo[] }>('agent.list', {})
        setAgents(Array.isArray(r?.agents) ? r.agents : [])
      } catch { /* agent 列表非关键 */ }
      try {
        const r = await client.call<TraceStats>('trace.stats', { days: 7 })
        if (r && typeof r === 'object') setTraceStats(r)
      } catch { /* 执行统计非关键 */ }
      try {
        const r = await client.call<UsageToday>('usage.today', {})
        if (r && typeof r === 'object') setUsage(r)
      } catch { /* 用量非关键 */ }
      // 内核策略：读取 agent_kernel 配置（JSON 字符串或 null）
      try {
        const raw = await client.call<string | null>('settings.get', { key: 'agent_kernel' })
        let type = ''
        if (typeof raw === 'string' && raw.trim()) {
          try { type = (JSON.parse(raw) as { type?: string })?.type ?? '' } catch { type = '' }
        }
        setKernelPolicy(type)
        setPolicyLoaded(true)
      } catch { /* 策略非关键 */ }
    }
    void fetchAll()
    const interval = setInterval(fetchAll, 30000)
    return () => clearInterval(interval)
  }, [connected])

  const applyKernelPolicy = async (type: string) => {
    if (type === kernelPolicy || policySaving) return
    setPolicySaving(true)
    setPolicyFeedback(null)
    try {
      const client = getWsClient()
      // 保留原配置子字段（dsh.profile / auto.candidates 等），仅改 type
      let cfg: Record<string, unknown> = {}
      try {
        const raw = await client.call<string | null>('settings.get', { key: 'agent_kernel' })
        if (typeof raw === 'string' && raw.trim()) {
          const parsed = JSON.parse(raw) as Record<string, unknown>
          if (parsed && typeof parsed === 'object') cfg = parsed
        }
      } catch { /* 读取失败则用纯 type 配置 */ }
      cfg.type = type
      const ok = await client.call<boolean>('settings.set', {
        key: 'agent_kernel',
        value: JSON.stringify(cfg),
      })
      if (ok === false) throw new Error('rejected')
      setKernelPolicy(type)
      setPolicyFeedback('saved')
      // 立即刷新内核状态（热换已生效）
      try {
        const ks = await client.call<KernelStatus>('kernel.status')
        setKernelStatus(ks)
      } catch { /* 刷新非关键 */ }
    } catch {
      setPolicyFeedback('failed')
    } finally {
      setPolicySaving(false)
      window.setTimeout(() => setPolicyFeedback(null), 2500)
    }
  }

  const availableAgents = agents.filter((a) => a.is_available !== false)

  return (
    <aside className="agent-context-panel" aria-label="任务执行上下文" data-testid="agent-context-panel">
      <div className="agent-panel-heading">
        <div><span className="agent-kicker">任务执行上下文</span><h2>执行上下文</h2></div>
        <div className="flex items-center gap-2">
          <span className={`agent-live-dot${connected ? '' : ' agent-live-dot-off'}`} aria-label={connected ? '后端已连接' : '后端未连接'} />
          {onCollapse && (
            <button
              onClick={onCollapse}
              className="w-6 h-6 flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-colors text-xs"
              title="折叠执行上下文"
            >
              <Icon icon="fa-solid fa-chevron-right" />
            </button>
          )}
        </div>
      </div>
      {/* 内核状态徽章 */}
      {kernelStatus && (
        <section className="agent-context-section">
          <div className="agent-section-title"><strong>执行内核</strong></div>
          <div className={`agent-kernel-badge${kernelStatus.degraded ? ' agent-kernel-badge--degraded' : ''}`} title={kernelStatus.message}>
            <span className="agent-kernel-icon">
              <Icon icon={`fa-solid fa-${kernelStatus.degraded ? 'triangle-exclamation' : 'microchip'}`} />
            </span>
            <span className="agent-kernel-name">{kernelStatus.gateway_name || kernelStatus.effective_engine || 'none'}</span>
            <span className="agent-kernel-state">{kernelStatus.degraded ? '降级' : '就绪'}</span>
          </div>
        </section>
      )}
      {/* 内核策略（热换入口） */}
      <section className="agent-context-section">
        <div className="agent-section-title"><strong>{t('chat.kernelPolicy')}</strong>{policyFeedback === 'saved' && <span className="agent-policy-feedback">{t('chat.kernelPolicySaved')}</span>}{policyFeedback === 'failed' && <span className="agent-policy-feedback agent-policy-feedback--error">{t('chat.kernelPolicyFailed')}</span>}</div>
        <div className="agent-kernel-policy">
          <select
            className="agent-policy-select"
            value={policyLoaded ? kernelPolicy : ''}
            disabled={!connected || policySaving}
            onChange={(e) => { void applyKernelPolicy(e.target.value) }}
            aria-label={t('chat.kernelPolicy')}
            data-testid="kernel-policy-select"
          >
            {!policyLoaded && <option value="">—</option>}
            {KERNEL_POLICY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          {policySaving && <span className="agent-policy-saving">{t('chat.kernelPolicySaving')}</span>}
        </div>
        <p className="agent-policy-hint">{t('chat.kernelPolicyHint')}</p>
      </section>
      {/* Agent 清单（指挥对象） */}
      {availableAgents.length > 0 && (
        <section className="agent-context-section">
          <div className="agent-section-title"><strong>可指挥 Agent</strong><span>{availableAgents.length}</span></div>
          <div className="agent-list">
            {availableAgents.map((agent) => (
              <div className="agent-list-item" key={agent.agent_id} title={agent.description || agent.display_name}>
                <span className="agent-list-icon"><Icon icon="fa-solid fa-robot" /></span>
                <span className="agent-list-copy">
                  <strong>{agent.display_name || agent.agent_id}</strong>
                  <small>{agent.capabilities?.slice(0, 2).join(' · ') || agent.agent_type || ''}</small>
                </span>
                <span className={`agent-list-state${agent.status === 'busy' ? ' agent-list-state--busy' : ''}`}>
                  {agent.status === 'busy' ? '执行中' : '空闲'}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}
      {/* 执行统计 + 用量 */}
      {(traceStats || usage) && (
        <section className="agent-context-section">
          <div className="agent-section-title"><strong>运行概况</strong></div>
          <div className="agent-stats-grid">
            {traceStats && (
              <>
                <div className="agent-stat"><span className="agent-stat-value">{traceStats.total_runs ?? 0}</span><span className="agent-stat-label">近 7 天运行</span></div>
                <div className="agent-stat"><span className="agent-stat-value">{Math.round((traceStats.success_rate ?? 0) * 100)}%</span><span className="agent-stat-label">成功率</span></div>
              </>
            )}
            {usage && usage.calls !== undefined && (
              <div className="agent-stat"><span className="agent-stat-value">{usage.calls}</span><span className="agent-stat-label">今日调用</span></div>
            )}
            {usage && usage.tokens !== undefined && (
              <div className="agent-stat"><span className="agent-stat-value">{(usage.tokens / 1000).toFixed(1)}k</span><span className="agent-stat-label">今日 Token</span></div>
            )}
          </div>
        </section>
      )}
      <section className="agent-context-section">
        <div className="agent-section-title"><strong>当前状态</strong><span>{streaming ? '实时' : '已暂停'}</span></div>
        <p className="agent-context-copy">{connected ? '本地后端已连接，事件会实时显示在这里。' : '等待本地后端连接。'}</p>
      </section>
      <section className="agent-context-section">
        <div className="agent-section-title"><strong>计划</strong><span>{plan.length} 项</span></div>
        {plan.length === 0 ? <p className="agent-empty">发送任务后，计划步骤会出现在这里。</p> : <ol className="agent-plan-list">{plan.map((item) => <li key={item.id}><span className={`agent-status-mark agent-status-${item.status}`} /> <span>{item.detail}</span><small>{statusLabel(item.status)}</small></li>)}</ol>}
      </section>
      <section className="agent-context-section agent-activity-section">
        <div className="agent-section-title"><strong>最近活动</strong><span>{events.length}</span></div>
        <div className="agent-event-list">{events.slice(-6).reverse().map((event) => <article className="agent-event-card" key={event.id}><div className="agent-event-card-top"><strong>{event.title}</strong><span className={`agent-event-status agent-status-${event.status}`}>{statusLabel(event.status)}</span></div><p>{event.detail}</p></article>)}{events.length === 0 && <p className="agent-empty">暂无执行活动。</p>}</div>
      </section>
      <p className="agent-security-note">高风险操作需要确认，并继续经过 sandbox 审批。</p>
    </aside>
  )
}
