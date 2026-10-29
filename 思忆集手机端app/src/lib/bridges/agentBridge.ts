/**
 * Agent 桥 — 封装桌面后端 `agent.*` RPC。
 *
 * 后端真源：`思忆集app/backend/handlers/_agent_tasks.py`
 * 方法：list / history / delegate / schedule / run_now / cancel
 *
 * 语义：
 *  - `delegate` 把一个任务交给桌面端的 agent 执行，返回 task_id；
 *  - `agent.*` 在桌面端不可达时后端会直接抛错（见 `agent.unavailable`、
 *    `agent.not_registered`、`agent.directory_unavailable`），
 *    手机端据此给出「桌面未连接」的明确提示，而不是静默失败。
 */
import { callRpc } from '../rpcClient'

export interface AgentTask {
  id: string | number
  type?: string
  status?: string
  prompt?: string
  result?: string
  error?: string
  created_at?: string | number
  updated_at?: string | number
  [key: string]: unknown
}

export type AgentStatus = 'pending' | 'running' | 'done' | 'failed' | 'cancelled' | string

export interface AgentError {
  code: 'unavailable' | 'not_registered' | 'directory_unavailable' | 'unknown'
  message: string
}

function asArray<T>(res: unknown, ...keys: string[]): T[] {
  if (Array.isArray(res)) return res as T[]
  if (res && typeof res === 'object') {
    for (const k of keys) {
      const v = (res as Record<string, unknown>)[k]
      if (Array.isArray(v)) return v as T[]
    }
    for (const v of Object.values(res as Record<string, unknown>)) {
      if (Array.isArray(v)) return v as T[]
    }
  }
  return []
}

/** 把后端错误码归一化成 UI 能直接展示的说明。 */
export function describeAgentError(err: unknown): AgentError {
  const msg = err instanceof Error ? err.message : String(err ?? '')
  const lower = msg.toLowerCase()
  if (lower.includes('unavailable') || lower.includes('not connected') || lower.includes('offline')) {
    return { code: 'unavailable', message: '桌面端未连接，Agent 任务需要桌面端在线才能执行' }
  }
  if (lower.includes('not_registered') || lower.includes('not registered')) {
    return { code: 'not_registered', message: '该 Agent 未注册，请先在桌面端启用' }
  }
  if (lower.includes('directory')) {
    return { code: 'directory_unavailable', message: 'Agent 目录不可用，请检查桌面端配置' }
  }
  return { code: 'unknown', message: msg || 'Agent 调用失败' }
}

export async function listAgents(): Promise<AgentTask[]> {
  try {
    const res = await callRpc<unknown>('agent.list', {})
    return asArray<AgentTask>(res, 'agents', 'tasks', 'items', 'list')
  } catch {
    return []
  }
}

export async function history(limit = 50): Promise<AgentTask[]> {
  try {
    const res = await callRpc<unknown>('agent.history', { limit })
    return asArray<AgentTask>(res, 'history', 'tasks', 'items')
  } catch {
    return []
  }
}

/**
 * 委派任务给桌面 Agent。
 * 与其它桥不同，这里把错误向上抛 —— Agent 失败原因（桌面离线 / 未注册）
 * 是用户必须知道的信息，吞掉会让 UI 显示「成功但没结果」。
 */
export async function delegate(args: {
  prompt: string
  agent?: string
  context?: Record<string, unknown>
}): Promise<{ task_id?: string | number } & Record<string, unknown>> {
  return callRpc<{ task_id?: string | number } & Record<string, unknown>>('agent.delegate', args)
}

export async function schedule(args: {
  prompt: string
  schedule: string
  agent?: string
}): Promise<{ task_id?: string | number } & Record<string, unknown>> {
  return callRpc<{ task_id?: string | number } & Record<string, unknown>>('agent.schedule', args)
}

export async function runNow(taskId: string | number): Promise<boolean> {
  try {
    await callRpc('agent.run_now', { task_id: taskId })
    return true
  } catch {
    return false
  }
}

export async function cancel(taskId: string | number): Promise<boolean> {
  try {
    await callRpc('agent.cancel', { task_id: taskId })
    return true
  } catch {
    return false
  }
}
