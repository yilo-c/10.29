/**
 * Agent 桥 — 封装桌面后端 `agent.*` RPC。
 *
 * 后端真源：`思忆集app/backend/handlers/_agent_tasks.py`
 *
 * ⚠️ 已逐一对齐后端（2026-10-29 核验）。重要修正：
 *  1. **`agent.delegate` 这个 RPC 并不存在**（早期版本臆造）。真正执行任务是
 *     `agent.run_now(agent_id, task, context_data, timeout)`。
 *  2. `agent.list` 返回的是**已注册的 Agent 目录**（`{agents: [...]}`），不是任务列表。
 *  3. 调用历史在 `agent.history(limit)` → `{invocations: [...]}`。
 *  4. 全部返回 `{success, error?}` 信封。
 *
 * 语义：Agent 在桌面端执行，手机端负责触发与查看。桌面不可达时后端返回
 * `agent.directory_unavailable`，UI 据此提示而不是静默失败。
 */

import { callRpc } from '../rpcClient'

/** 后端返回的 Agent 目录项（`info.to_dict()`）。 */
export interface AgentInfo {
  id: string
  name?: string
  description?: string
  available?: boolean
  driver?: string
  [key: string]: unknown
}

/** 一次 Agent 调用的历史记录（`get_recent_invocations`）。 */
export interface AgentInvocation {
  agent_id?: string
  task?: string
  success?: boolean
  result?: string
  error?: string
  started_at?: number | string
  finished_at?: number | string
  duration_ms?: number
  [key: string]: unknown
}

export interface AgentError {
  code: 'directory_unavailable' | 'driver_missing' | 'missing_param' | 'unknown'
  message: string
}

interface Envelope {
  success?: boolean
  error?: string
  [key: string]: unknown
}

/** 把后端错误文案归一化成 UI 可直接展示的说明。 */
export function describeAgentError(err: unknown): AgentError {
  const msg = err instanceof Error ? err.message : String(err ?? '')
  const lower = msg.toLowerCase()
  if (lower.includes('directory_unavailable') || lower.includes('directory')) {
    return { code: 'directory_unavailable', message: '桌面端 Agent 目录不可用，请确认桌面端已启动' }
  }
  if (lower.includes('missing_param') || lower.includes('missing')) {
    return { code: 'missing_param', message: '参数不完整，请选择 Agent 并填写任务内容' }
  }
  if (lower.includes('driver')) {
    return { code: 'driver_missing', message: '该 Agent 未注册驱动，无法执行' }
  }
  return { code: 'unknown', message: msg || 'Agent 调用失败' }
}

/** 列出已注册的 Agent（不是任务）。 */
export async function listAgents(): Promise<AgentInfo[]> {
  try {
    const res = await callRpc<Envelope & { agents?: AgentInfo[] }>('agent.list', {})
    return Array.isArray(res?.agents) ? res.agents : []
  } catch {
    return []
  }
}

/** 最近调用历史。 */
export async function history(limit = 20): Promise<AgentInvocation[]> {
  try {
    const res = await callRpc<Envelope & { invocations?: AgentInvocation[] }>('agent.history', { limit })
    return Array.isArray(res?.invocations) ? res.invocations : []
  } catch {
    return []
  }
}

/**
 * 立即执行一次 Agent 任务（同步等待结果）。
 *
 * ⚠️ 这是替代臆造的 `agent.delegate` 的正确接口。
 * 错误向上抛：桌面离线 / Agent 未注册是用户必须知道的信息。
 */
export async function runNow(args: {
  agentId: string
  task: string
  contextData?: Record<string, unknown>
  timeout?: number
}): Promise<{ success: boolean; result?: unknown; error?: string }> {
  const res = await callRpc<Envelope & { result?: unknown }>('agent.run_now', {
    agent_id: args.agentId,
    task: args.task,
    context_data: args.contextData ?? null,
    timeout: args.timeout ?? 300,
  })
  if (res?.success !== true) {
    return { success: false, error: res?.error ?? 'Agent 执行失败' }
  }
  return { success: true, result: res.result }
}

/**
 * 把 Agent 任务排进 cron 定时执行。
 * 后端会用 `{agent_id, task, timeout}` 派发 `agent.run_now`。
 */
export async function schedule(args: {
  agentId: string
  task: string
  /** cron 表达式或与 repeat 搭配使用 */
  schedule?: string
  runAt?: string
  repeat?: string
  name?: string
  timeout?: number
}): Promise<{ success: boolean; job?: unknown; error?: string }> {
  const res = await callRpc<Envelope & { job?: unknown }>('agent.schedule', {
    agent_id: args.agentId,
    task: args.task,
    schedule: args.schedule ?? '',
    run_at: args.runAt ?? '',
    repeat: args.repeat ?? 'none',
    name: args.name ?? '',
    timeout: args.timeout ?? 300,
  })
  if (res?.success !== true) {
    return { success: false, error: res?.error ?? '排期失败' }
  }
  return { success: true, job: res.job }
}

/**
 * 取消已排期的 Agent 任务。
 *
 * ⚠️ 注意：后端参数名是 **job_id**（它本质上取消的是 cron job），不是 task_id。
 */
export async function cancel(jobId: string): Promise<boolean> {
  try {
    const res = await callRpc<Envelope>('agent.cancel', { job_id: jobId })
    return res?.success === true
  } catch {
    return false
  }
}
