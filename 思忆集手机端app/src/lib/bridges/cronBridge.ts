/**
 * 定时任务桥 — 封装桌面后端 `cron.*` RPC。
 *
 * 后端真源：`思忆集app/backend/handlers/_cron.py`
 *
 * ⚠️ 参数签名已逐一对齐后端（2026-10-29 核验）：
 *  - `cron.add` 用 **action_id + payload** 的动作模型，**没有 prompt 字段**。
 *  - `repeat` 取值：none / daily / weekly / monthly / interval / cron
 *  - `cron.history` 的 job_id **必填**。
 *  - 所有方法返回 `{success, ...}` 信封，失败带 `error`。
 *
 * 语义：cron 任务在桌面端常驻进程里执行，手机端只做查看/增删/启停。
 */

import { callRpc } from '../rpcClient'

export interface CronJob {
  id: string
  name: string
  action_id: string
  payload?: Record<string, unknown>
  schedule?: string
  run_at?: string
  repeat?: CronRepeat
  enabled: boolean
  max_retries?: number
  retry_backoff?: number
  run_every_days?: number
  last_run?: number | string
  next_run?: number | string
  run_count?: number
  [key: string]: unknown
}

export type CronRepeat = 'none' | 'daily' | 'weekly' | 'monthly' | 'interval' | 'cron'

export interface CronHistoryEntry {
  job_id?: string
  started_at?: number | string
  finished_at?: number | string
  success?: boolean
  error?: string
  [key: string]: unknown
}

export interface CronStatusSnapshot {
  running?: boolean
  job_count?: number
  next_run_at?: number | string
  [key: string]: unknown
}

/** 后端统一返回 {success, error?, ...}，这里抽出成功/失败的判定。 */
interface Envelope {
  success?: boolean
  error?: string
  [key: string]: unknown
}

function ok(res: Envelope | null): boolean {
  return res?.success === true
}

export interface AddJobArgs {
  /** 任务显示名称（必填） */
  name: string
  /** 要执行的动作 ID，如 "tool.zcode.open"（必填） */
  actionId: string
  /** 动作参数 */
  payload?: Record<string, unknown>
  /** cron 表达式（repeat='cron' 时必填，5 字段：分 时 日 月 周） */
  schedule?: string
  /** 一次性时间，或 daily/weekly/monthly 的执行时刻（HH:MM） */
  runAt?: string
  /** 重复模式 */
  repeat?: CronRepeat
  /** interval 模式下的间隔天数 */
  runEveryDays?: number
  maxRetries?: number
  retryBackoff?: number
}

export async function listJobs(includeDisabled = true): Promise<CronJob[]> {
  try {
    const res = await callRpc<Envelope & { jobs?: CronJob[] }>('cron.list', {
      include_disabled: includeDisabled,
    })
    return Array.isArray(res?.jobs) ? res.jobs : []
  } catch {
    return []
  }
}

/** 添加定时任务。成功返回 job，失败返回 null（原因见 isJobError）。 */
export async function addJob(args: AddJobArgs): Promise<{ job: CronJob | null; error?: string }> {
  try {
    const res = await callRpc<Envelope & { job?: CronJob }>('cron.add', {
      name: args.name,
      action_id: args.actionId,
      payload: args.payload ?? {},
      schedule: args.schedule ?? '',
      run_at: args.runAt ?? '',
      repeat: args.repeat ?? 'none',
      run_every_days: args.runEveryDays ?? 0,
      max_retries: args.maxRetries ?? 0,
      retry_backoff: args.retryBackoff ?? 60,
    })
    if (!ok(res)) return { job: null, error: res?.error ?? '添加失败' }
    return { job: res?.job ?? null }
  } catch (e) {
    return { job: null, error: e instanceof Error ? e.message : String(e) }
  }
}

export async function updateJob(
  jobId: string,
  patch: Partial<Omit<AddJobArgs, 'actionId'>> & { actionId?: string },
): Promise<boolean> {
  try {
    const res = await callRpc<Envelope>('cron.update', {
      job_id: jobId,
      ...(patch.name != null ? { name: patch.name } : {}),
      ...(patch.actionId != null ? { action_id: patch.actionId } : {}),
      ...(patch.payload != null ? { payload: patch.payload } : {}),
      ...(patch.schedule != null ? { schedule: patch.schedule } : {}),
      ...(patch.runAt != null ? { run_at: patch.runAt } : {}),
      ...(patch.repeat != null ? { repeat: patch.repeat } : {}),
      ...(patch.runEveryDays != null ? { run_every_days: patch.runEveryDays } : {}),
      ...(patch.maxRetries != null ? { max_retries: patch.maxRetries } : {}),
      ...(patch.retryBackoff != null ? { retry_backoff: patch.retryBackoff } : {}),
    })
    return ok(res)
  } catch {
    return false
  }
}

export async function removeJob(jobId: string): Promise<boolean> {
  try {
    const res = await callRpc<Envelope>('cron.remove', { job_id: jobId })
    return ok(res)
  } catch {
    return false
  }
}

/** 启用/禁用。注意后端 `enabled` 默认 True，这里显式传值避免误启用。 */
export async function toggleJob(jobId: string, enabled: boolean): Promise<boolean> {
  try {
    const res = await callRpc<Envelope>('cron.toggle', { job_id: jobId, enabled })
    return ok(res)
  } catch {
    return false
  }
}

export async function toggleBatch(jobIds: string[], enabled: boolean): Promise<boolean> {
  try {
    const res = await callRpc<Envelope>('cron.toggle_batch', { job_ids: jobIds, enabled })
    return ok(res)
  } catch {
    return false
  }
}

/** 立即执行一次（不改变原有排期）。 */
export async function runNow(jobId: string): Promise<boolean> {
  try {
    const res = await callRpc<Envelope>('cron.run_now', { job_id: jobId })
    return ok(res)
  } catch {
    return false
  }
}

/**
 * 执行历史。
 * ⚠️ 后端 `cron.history` 的 job_id **必填**，不传会直接返回 `{success:false, error}`，
 * 所以这里把 jobId 做成必选参数，避免调用方误传空值拿到永远为空的历史。
 */
export async function history(jobId: string, limit = 20): Promise<CronHistoryEntry[]> {
  if (!jobId) return []
  try {
    const res = await callRpc<Envelope & { history?: CronHistoryEntry[] }>('cron.history', {
      job_id: jobId,
      limit,
    })
    return Array.isArray(res?.history) ? res.history : []
  } catch {
    return []
  }
}

export async function status(): Promise<CronStatusSnapshot | null> {
  try {
    const res = await callRpc<Envelope & { status?: CronStatusSnapshot }>('cron.status', {})
    if (!ok(res)) return null
    return res?.status ?? null
  } catch {
    return null
  }
}
