/**
 * 定时任务桥 — 封装桌面后端 `cron.*` RPC。
 *
 * 后端真源：`思忆集app/backend/handlers/_cron.py`
 * 方法：add / list / update / remove / toggle / toggle_batch /
 *      run_now / history / status
 *
 * 注意：cron 任务真正执行在桌面端（后端常驻进程），
 * 手机端只做查看与启停，不本地调度 —— 否则关掉 App 就漏执行。
 */
import { callRpc } from '../rpcClient'

export interface CronJob {
  id: string | number
  name?: string
  schedule?: string
  prompt?: string
  enabled?: boolean
  last_run?: string | number
  next_run?: string | number
  last_status?: string
  run_count?: number
  [key: string]: unknown
}

export interface CronHistoryEntry {
  id?: string | number
  job_id?: string | number
  started_at?: string | number
  finished_at?: string | number
  status?: string
  output?: string
  error?: string
  [key: string]: unknown
}

export interface CronStatus {
  total?: number
  enabled?: number
  running?: number
  next_run?: string | number
  [key: string]: unknown
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

export async function listJobs(): Promise<CronJob[]> {
  try {
    const res = await callRpc<unknown>('cron.list', {})
    return asArray<CronJob>(res, 'jobs', 'items', 'list')
  } catch {
    return []
  }
}

export async function addJob(job: {
  name?: string
  schedule: string
  prompt: string
  enabled?: boolean
}): Promise<CronJob | null> {
  try {
    return await callRpc<CronJob>('cron.add', job)
  } catch {
    return null
  }
}

export async function updateJob(
  jobId: string | number,
  patch: Partial<Pick<CronJob, 'name' | 'schedule' | 'prompt' | 'enabled'>>,
): Promise<boolean> {
  try {
    await callRpc('cron.update', { job_id: jobId, ...patch })
    return true
  } catch {
    return false
  }
}

export async function removeJob(jobId: string | number): Promise<boolean> {
  try {
    await callRpc('cron.remove', { job_id: jobId })
    return true
  } catch {
    return false
  }
}

export async function toggleJob(jobId: string | number, enabled?: boolean): Promise<boolean> {
  try {
    await callRpc('cron.toggle', { job_id: jobId, ...(enabled != null ? { enabled } : {}) })
    return true
  } catch {
    return false
  }
}

export async function toggleBatch(
  jobIds: Array<string | number>,
  enabled: boolean,
): Promise<boolean> {
  try {
    await callRpc('cron.toggle_batch', { job_ids: jobIds, enabled })
    return true
  } catch {
    return false
  }
}

/** 立即执行一次（不改变原有排期）。 */
export async function runNow(jobId: string | number): Promise<boolean> {
  try {
    await callRpc('cron.run_now', { job_id: jobId })
    return true
  } catch {
    return false
  }
}

export async function history(jobId?: string | number, limit = 50): Promise<CronHistoryEntry[]> {
  try {
    const res = await callRpc<unknown>('cron.history', {
      ...(jobId != null ? { job_id: jobId } : {}),
      limit,
    })
    return asArray<CronHistoryEntry>(res, 'history', 'items', 'runs')
  } catch {
    return []
  }
}

export async function status(): Promise<CronStatus | null> {
  try {
    return await callRpc<CronStatus>('cron.status', {})
  } catch {
    return null
  }
}
