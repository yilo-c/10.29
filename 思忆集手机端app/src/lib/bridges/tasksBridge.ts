/**
 * 任务桥 — 桌面 `tasks.*` RPC 的薄封装。
 *
 * 注意：`contentSync.ts` 已经在做"拉桌面 → 合并 store"的批量同步，这里只补足
 * 单条 CRUD/toggle 这类按需操作。
 */
import { callRpc } from '../rpcClient'

export interface RemoteTask {
  id: string | number
  title?: string
  text?: string
  done?: boolean
  completed?: boolean
  priority?: number
  category?: string
  due_date?: string | number
  dueDate?: number
  created_at?: string | number
  updated_at?: string | number
}

export async function listTasks(limit = 200): Promise<RemoteTask[]> {
  try {
    const res = await callRpc<unknown>('tasks.list', { limit })
    if (Array.isArray(res)) return res as RemoteTask[]
    if (res && typeof res === 'object' && Array.isArray((res as { items?: unknown[] }).items)) {
      return (res as { items: RemoteTask[] }).items
    }
    return []
  } catch {
    return []
  }
}

export async function getTask(taskId: string | number): Promise<RemoteTask | null> {
  try {
    return await callRpc<RemoteTask>('tasks.get', { task_id: taskId })
  } catch {
    return null
  }
}

export async function createTask(args: {
  title: string
  priority?: number
  due_date?: string | number
  category?: string
}): Promise<RemoteTask | null> {
  try {
    return await callRpc<RemoteTask>('tasks.create', args as Record<string, unknown>)
  } catch {
    return null
  }
}

export async function updateTask(taskId: string | number, patch: Partial<RemoteTask>): Promise<boolean> {
  try {
    await callRpc('tasks.update', { task_id: taskId, ...patch })
    return true
  } catch {
    return false
  }
}

export async function deleteTask(taskId: string | number): Promise<boolean> {
  try {
    await callRpc('tasks.delete', { task_id: taskId })
    return true
  } catch {
    return false
  }
}

export async function toggleTask(taskId: string | number, completed: boolean): Promise<boolean> {
  try {
    await callRpc('tasks.toggle', { task_id: taskId, completed })
    return true
  } catch {
    return false
  }
}

export async function scheduleTaskReminder(
  taskId: string,
  options: {
    schedule?: string
    runAt?: string
    repeat?: 'none' | 'daily' | 'weekly' | 'monthly' | 'cron'
    reminderText?: string
    maxRetries?: number
    retryBackoff?: number
  } = {},
): Promise<{ success?: boolean; error?: string; job_id?: string } | null> {
  try {
    return await callRpc<{ success?: boolean; error?: string; job_id?: string }>('tasks.schedule', {
      task_id: taskId,
      schedule: options.schedule ?? '',
      run_at: options.runAt ?? '',
      repeat: options.repeat ?? 'none',
      reminder_text: options.reminderText ?? '',
      max_retries: options.maxRetries ?? 0,
      retry_backoff: options.retryBackoff ?? 60,
    })
  } catch { return null }
}

export async function unscheduleTaskReminder(taskId: string): Promise<{ success?: boolean; error?: string } | null> {
  try {
    return await callRpc<{ success?: boolean; error?: string }>('tasks.unschedule', { task_id: taskId })
  } catch { return null }
}
