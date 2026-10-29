import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import Icon from '../Common/Icon'
import type { AgentEventView } from './agentEvents'

/** 工具/子任务/计划事件折叠时间线：AI 消息旁展示「使用了 N 步 · 12.4s」，点开回溯每步过程。 */
interface ToolTimelineProps {
  /** 已归一化的 agent 事件（本组件会过滤出工具类步骤） */
  events: AgentEventView[]
  /** AI 生成总耗时（秒） */
  durationSec?: number
}

const TOOL_ICON: Record<AgentEventView['kind'], string> = {
  plan: 'fa-solid fa-clipboard-list',
  tool: 'fa-solid fa-gear',
  worker: 'fa-solid fa-robot',
  summary: 'fa-solid fa-list-check',
}

function statusColor(status: AgentEventView['status']): string {
  if (status === 'attention') return 'bg-danger'
  if (status === 'done') return 'bg-success'
  return 'bg-brand animate-pulse'
}

function planStepColor(status: string): string {
  const s = status.toLowerCase()
  if (s === 'done') return 'bg-success'
  if (s === 'failed' || s === 'blocked') return 'bg-danger'
  if (s === 'running') return 'bg-brand animate-pulse'
  return 'bg-text-dim'
}

function formatDuration(sec?: number): string {
  if (!sec || sec <= 0) return ''
  if (sec < 60) return `${sec.toFixed(1)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}m${s > 0 ? ` ${s}s` : ''}`
}

export function ToolTimeline({ events, durationSec }: ToolTimelineProps) {
  const [open, setOpen] = useState(false)
  const steps = events.filter((e) => e.kind === 'tool')
  const planEvent = [...events].reverse().find((e) => e.kind === 'plan' && !!e.steps && e.steps.length > 0)
  if (steps.length === 0 && !planEvent) return null

  const duration = formatDuration(durationSec)
  const summary = steps.length
    ? `使用了 ${steps.length} 个工具${duration ? ` · ${duration}` : ''}`
    : planEvent?.title || ''

  return (
    <div className="pl-10">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-2xs text-text-muted hover:text-text-secondary transition-colors"
        aria-expanded={open}
      >
        <Icon icon={`fa-solid fa-chevron-${open ? 'down' : 'right'}`} className="text-2xs" />
        <span>{summary}</span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ type: 'tween', duration: 0.2, ease: 'easeOut' }}
            className="overflow-hidden"
          >
            {planEvent?.steps && (
              <div className="mt-1.5 max-w-[72%] rounded-lg bg-surface-elevated border border-border p-2">
                <div className="flex items-center gap-1.5 text-2xs font-medium text-text-primary mb-1">
                  <Icon icon="fa-solid fa-clipboard-list" className="text-text-muted" />
                  {planEvent.title}
                </div>
                <ol className="space-y-1">
                  {planEvent.steps.map((s, i) => (
                    <li key={i} className="flex items-start gap-2 text-2xs text-text-secondary leading-relaxed">
                      <span className={`mt-1 inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${planStepColor(s.status)}`} title={s.status} />
                      <span>{s.title}</span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {steps.length > 0 && (
            <ol className="mt-1.5 rounded-lg bg-surface-elevated border border-border py-1 pr-2 pl-3 space-y-1 max-w-[72%]">
              {steps.map((step) => (
                <li key={step.id} className="flex items-start gap-2 text-2xs text-text-secondary leading-relaxed">
                  <span className={`mt-1 inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${statusColor(step.status)}`} title={step.status} />
                  <Icon icon={TOOL_ICON[step.kind]} className="mt-0.5 text-text-muted flex-shrink-0" />
                  <span className="flex-shrink-0 font-medium text-text-primary">{step.title}</span>
                  {step.detail ? <span className="truncate break-all">{step.detail}</span> : null}
                </li>
              ))}
            </ol>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}