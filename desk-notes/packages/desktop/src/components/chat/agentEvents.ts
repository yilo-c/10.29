import type { ChatStreamEvent } from './useChatStream'

export type AgentEventKind = 'plan' | 'tool' | 'worker' | 'summary'

export interface AgentEventView {
  id: string
  kind: AgentEventKind
  title: string
  detail: string
  status: 'running' | 'done' | 'attention'
  /** update_plan 计划的步骤清单（仅 kind==='plan' 且有结构化数据时存在） */
  steps?: { title: string; status: string }[]
}

const EVENT_LABELS: Record<ChatStreamEvent['type'], { kind: AgentEventKind; title: string }> = {
  planner: { kind: 'plan', title: '计划更新' },
  tool_progress: { kind: 'tool', title: '工具运行中' },
  tool_result: { kind: 'tool', title: '工具结果' },
  subagent_start: { kind: 'worker', title: '子 Agent 已启动' },
  subagent_done: { kind: 'worker', title: '子 Agent 已完成' },
  multi_agent_summary: { kind: 'summary', title: '协作汇总' },
  chat_resume_status: { kind: 'tool', title: '任务恢复' },
  tool: { kind: 'tool', title: '工具调用' },
  confirm: { kind: 'tool', title: '待确认操作' },
  notice: { kind: 'tool', title: '内核状态' },
}

let _eventSeq = 0

export function normalizeAgentEvents(events: ChatStreamEvent[]): AgentEventView[] {
  return events.map((event) => {
    const metadata = EVENT_LABELS[event.type]
    const payload = parseEventData(event.data)
    const detail = payload.detail
    const status = resolveStatus(event.type, payload.status, detail)
    const isPlan = payload.tool === 'update_plan' && !!payload.steps && payload.steps.length > 0
    return {
      id: `ev-${++_eventSeq}`,
      kind: isPlan ? 'plan' : metadata.kind,
      title: isPlan ? payload.goal || '任务计划' : metadata.title,
      detail: isPlan ? '' : detail,
      status,
      steps: isPlan ? payload.steps : undefined,
    }
  })
}

interface EventPayload {
  detail: string
  status?: string
  tool?: string
  goal?: string
  steps?: { title: string; status: string }[]
}

function parseEventData(data: string): EventPayload {
  const text = data.trim()
  if (!text) return { detail: '' }
  try {
    const parsed: unknown = JSON.parse(text)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return { detail: text }
    const record = parsed as Record<string, unknown>
    // 工具进度事件使用 result 键承载工具输出（如 {"tool": "...", "result": "...", "status": "done"}）
    const detail = firstText(record.output, record.message, record.error, record.step, record.detail, record.text, record.result)
    const tool = typeof record.tool === 'string' ? record.tool : undefined
    let goal: string | undefined
    let steps: { title: string; status: string }[] | undefined
    if (tool === 'update_plan' && record.args && typeof record.args === 'object' && !Array.isArray(record.args)) {
      const args = record.args as Record<string, unknown>
      if (typeof args.goal === 'string') goal = args.goal
      if (Array.isArray(args.steps)) {
        steps = args.steps
          .map((s) => (s && typeof s === 'object' ? (s as Record<string, unknown>) : null))
          .filter((s): s is Record<string, unknown> => !!s && typeof s.title === 'string')
          .map((s) => ({ title: String(s.title), status: String(s.status || 'pending') }))
      }
    }
    return { detail: detail || text, status: typeof record.status === 'string' ? record.status : undefined, tool, goal, steps }
  } catch {
    return { detail: text }
  }
}

function firstText(...values: unknown[]): string {
  return values.find((value): value is string => typeof value === 'string' && value.trim().length > 0)?.trim() ?? ''
}

function resolveStatus(type: ChatStreamEvent['type'], rawStatus: string | undefined, detail: string): AgentEventView['status'] {
  const status = rawStatus?.toLowerCase()
  if (status === 'error' || status === 'failed' || status === 'failure' || status === 'rejected') return 'attention'
  if (status === 'success' || status === 'completed' || status === 'done') return 'done'
  if (status === 'waiting' || status === 'pending' || status === 'approval_required') return 'running'

  const lowerDetail = detail.toLowerCase()
  if (/(^|[\s:])error\b|failed|failure|失败|错误|拒绝/.test(lowerDetail)) return 'attention'
  if (type === 'tool_result' || type === 'subagent_done' || type === 'multi_agent_summary') return 'done'
  return 'running'
}

export function summarizePlan(events: AgentEventView[]): { title: string; status: AgentEventView['status'] }[] {
  return events
    .filter((event) => event.kind === 'plan')
    .slice(-4)
    .map((event) => ({ title: event.detail, status: event.status }))
}