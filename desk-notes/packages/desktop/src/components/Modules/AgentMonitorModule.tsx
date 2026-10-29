import React, { useState, useEffect, useMemo, useCallback } from 'react'
import Icon from '../Common/Icon'

import { getWsClient } from '../../utils/ai'
import { useChainProgress } from '../../hooks/useChainProgress'
import { useTranslation } from '../../i18n'
import SystemHealthPanel from './SystemHealthPanel'
import PageHeader from '../Common/PageHeader'
import StatusPill, { type StatusTone } from '../Common/StatusPill'
import TabbedOutputPanel from '../Common/TabbedOutputPanel'
import TracePanel from '../Common/TracePanel'
import EmptyState from '../Common/EmptyState'

interface SystemStatus {
  mode: string
  services: Record<string, boolean>
  methods: string[]
}

interface SystemInfo {
  version: string
  name: string
  ws_port: number
}

interface ActiveTask {
  task_id: string
  goal: string
  status: string
  progress: string
  created_at: string
}

// Service label keys resolved via i18n inside the component.
const SERVICE_LABEL_KEY: Record<string, string> = {
  ai: 'AI',
  memory: 'agentMonitor.serviceMemory',
  knowledge_graph: 'agentMonitor.serviceKnowledgeGraph',
}

type MethodGroup = 'chat' | 'notes' | 'tasks' | 'memory' | 'web' | 'system' | 'other'

function groupMethods(methods: string[]): Record<MethodGroup, string[]> {
  const groups: Record<MethodGroup, string[]> = {
    chat: [], notes: [], tasks: [], memory: [], web: [], system: [], other: [],
  }
  for (const m of methods) {
    const prefix = m.split('.')[0] as MethodGroup
    if (groups[prefix]) groups[prefix].push(m)
    else groups.other.push(m)
  }
  return groups
}

/** 任务状态 → StatusPill 色调映射（Quest 式状态胶囊） */
function taskTone(status: string): StatusTone {
  switch (status) {
    case 'executing': return 'running'
    case 'completed': return 'ok'
    case 'pending': return 'warn'
    case 'failed': return 'error'
    default: return 'idle'
  }
}

const AgentMonitorModule: React.FC = () => {
  const { t } = useTranslation()
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [activeTasks, setActiveTasks] = useState<ActiveTask[]>([])
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(true)

  // Real-time chain/workflow progress from backend events
  const { chain, workflow } = useChainProgress()
  const hasChain = chain && chain.status === 'running'
  const hasWorkflow = workflow && workflow.status === 'running'

  const poll = useCallback(() => {
    const client = getWsClient()
    const isConnected = client.status === 'connected'
    setConnected(isConnected)
    if (isConnected) {
      Promise.all([
        client.call<SystemStatus>('system.status'),
        client.call<SystemInfo>('system.info'),
        client.call<ActiveTask[]>('system.active_tasks').catch(() => [] as ActiveTask[]),
      ]).then(([s, i, tasks]) => {
        setStatus(s)
        setInfo(i)
        setActiveTasks(tasks || [])
      }).catch(() => {}).finally(() => setLoading(false))
    } else {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    poll()
    // Poll every 30s as fallback (events handle real-time updates)
    const interval = setInterval(poll, 30000)
    const client = getWsClient()
    const cleanup = client.onStatusChange(() => poll())
    return () => { clearInterval(interval); cleanup?.() }
  }, [poll])

  const methodGroups = useMemo(() => {
    if (!status?.methods) return null
    return groupMethods(status.methods)
  }, [status])

  const serviceCount = useMemo(() => {
    if (!status?.services) return { online: 0, total: 0 }
    const entries = Object.entries(status.services)
    return {
      online: entries.filter(([, v]) => v).length,
      total: entries.length,
    }
  }, [status])

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
      </div>
    )
  }

  if (!connected) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="text-2xl opacity-25"><Icon icon="fa-solid fa-plug" /></div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('agentMonitor.backendDisconnected')}</div>
      </div>
    )
  }

  const anyExecuting = activeTasks.some(tk => tk.status === 'executing')

  return (
    <div className="agent-workbench">
      {/* 左栏：任务列表（Quest 式） */}
      <aside className="agent-task-rail">
        <div className="agent-rail-heading">
          <span>{t('agentMonitor.taskList')}</span>
          <StatusPill tone={anyExecuting ? 'running' : 'idle'} label={activeTasks.length} pulse={anyExecuting} />
        </div>
        {activeTasks.length === 0 ? (
          <EmptyState
            compact
            icon="fa-solid fa-list-check"
            title={t('agentMonitor.noActiveTasks')}
          />
        ) : (
          <div className="flex flex-col gap-2">
            {activeTasks.map(task => (
              <div key={task.task_id} className="agent-task-item">
                <span className="agent-task-goal">{task.goal?.slice(0, 60) || task.task_id}</span>
                <div className="flex items-center gap-2">
                  <StatusPill tone={taskTone(task.status)} label={task.status} pulse={task.status === 'executing'} />
                  <span className="agent-task-time">
                    {new Date(task.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </div>
                {task.progress && <span className="agent-task-progress">{task.progress}</span>}
              </div>
            ))}
          </div>
        )}
      </aside>

      {/* 中栏：执行概览 */}
      <section className="agent-workbench-main">
        <PageHeader
          title={t('agentMonitor.systemStatus')}
          subtitle={info ? `${info.name} v${info.version} · ws:${info.ws_port}` : undefined}
        />

        {/* System health overview */}
        <SystemHealthPanel />

        {/* Service cards */}
        {status?.services && (
          <div className="grid grid-cols-3 gap-2">
            {Object.entries(status.services).map(([key, available]) => (
              <div
                key={key}
                className="rounded-xl border p-3 flex flex-col items-center gap-1.5"
                style={{
                  borderColor: available ? 'rgba(52,211,153,0.2)' : 'var(--panel-border)',
                  background: available ? 'rgba(52,211,153,0.04)' : undefined,
                }}
              >
                <div className={`w-2 h-2 rounded-full ${available ? 'bg-green-400' : 'bg-gray-500'}`} />
                <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                  {SERVICE_LABEL_KEY[key] ? t(SERVICE_LABEL_KEY[key]) : key}
                </span>
                <span className="text-xs" style={{ color: available ? 'var(--text-muted)' : 'var(--text-dim)' }}>
                  {available ? t('agentMonitor.available') : t('agentMonitor.unavailable')}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Stats row */}
        <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
          <span>{t('agentMonitor.statRegisteredMethods')}: <strong style={{ color: 'var(--text-primary)' }}>{status?.methods?.length || 0}</strong></span>
          <span className="text-white/[0.06]">|</span>
          <span>{t('agentMonitor.statServices')}: <strong style={{ color: 'var(--text-primary)' }}>{serviceCount.online}/{serviceCount.total}</strong></span>
          {status && <span className="text-white/[0.06]">|</span>}
          {status && <span>{t('agentMonitor.statRunMode')}: <strong style={{ color: 'var(--text-primary)' }}>{status.mode}</strong></span>}
          <span className="text-white/[0.06]">|</span>
          <span>{t('agentMonitor.statActiveTasks')}: <strong style={{ color: 'var(--text-primary)' }}>{activeTasks.length}</strong></span>
        </div>
      </section>

      {/* 右栏：输出区（Quest Spec/Files/Preview 模式） */}
      <TabbedOutputPanel
        defaultTab="execution"
        tabs={[
          {
            key: 'execution',
            label: t('agentMonitor.tabExecution'),
            render: () => (hasChain || hasWorkflow) ? (
              <div className="flex flex-col gap-3">
                {hasChain && (
                  <>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-1.5 text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                        <Icon icon="fa-solid fa-circle-notch text-xs animate-spin" style={{ color: 'var(--brand)' }} />
                        {chain!.goal?.slice(0, 60) || chain!.chain_id}
                      </div>
                      <span className="text-xs flex-shrink-0 ml-2" style={{ color: 'var(--text-dim)' }}>
                        {t('agentMonitor.stepCount', { done: chain!.completed + chain!.failed, total: chain!.total_steps })}
                      </span>
                    </div>
                    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--panel-bg)' }}>
                      <div className="h-full rounded-full transition-all duration-500"
                        style={{
                          width: `${chain!.total_steps > 0 ? Math.round((chain!.completed + chain!.failed) / chain!.total_steps * 100) : 0}%`,
                          background: 'var(--brand-gradient)',
                        }}
                      />
                    </div>
                    {chain!.nodes.length > 0 && (
                      <div className="space-y-1">
                        {chain!.nodes.map(n => (
                          <div key={n.node_id} className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                            {n.status === 'running' && <Icon icon="fa-solid fa-spinner text-[8px] animate-spin" style={{ color: 'var(--brand)' }} />}
                            {n.status === 'completed' && <Icon icon="fa-solid fa-check text-[8px]" style={{ color: 'var(--success)' }} />}
                            {n.status === 'failed' && <Icon icon="fa-solid fa-xmark text-[8px]" style={{ color: 'var(--danger)' }} />}
                            <span className="truncate flex-1">{n.description}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                )}
                {hasWorkflow && (
                  <div className="flex items-center gap-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                    <Icon icon="fa-solid fa-spinner text-[8px] animate-spin" style={{ color: 'var(--brand)' }} />
                    <span className="truncate">{workflow!.label} ({workflow!.current}/{workflow!.total})</span>
                  </div>
                )}
              </div>
            ) : (
              <EmptyState compact icon="fa-solid fa-terminal" title={t('agentMonitor.noOutput')} />
            ),
          },
          {
            key: 'trace',
            label: t('agentMonitor.tabTrace'),
            render: () => <TracePanel />,
          },
          {
            key: 'api',
            label: t('agentMonitor.tabApi'),
            render: () => methodGroups ? (
              <div className="flex flex-col gap-4">
                {(Object.entries(methodGroups) as [MethodGroup, string[]][]).map(([group, methods]) =>
                  methods.length > 0 && (
                    <div key={group}>
                      <span className="text-xs uppercase tracking-wider font-semibold" style={{ color: 'var(--text-muted)' }}>
                        {group}
                      </span>
                      <div className="mt-1.5 space-y-1">
                        {methods.map(m => (
                          <div key={m} className="text-xs font-mono" style={{ color: 'var(--text-dim)' }}>
                            <Icon icon="fa-solid fa-caret-right mr-1 text-[8px]" />{m}
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                )}
              </div>
            ) : (
              <EmptyState compact icon="fa-solid fa-plug" title={t('agentMonitor.noOutput')} />
            ),
          },
        ]}
      />
    </div>
  )
}

export default AgentMonitorModule
