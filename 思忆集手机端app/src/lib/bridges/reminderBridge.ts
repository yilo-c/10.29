/**
 * 提醒桥 — 把手机端创建的提醒同步到桌面 `reminder.*` RPC。
 *
 * 桌面端 reminder 不仅是本地通知，还有 `reminder.fired` 事件、统一管理列表，
 * 收口到这里后所有调用走 `callRpc`（自动 WS/HTTP 双通道）。
 */
import { callRpc } from '../rpcClient'

export interface ReminderRecord {
  id: string
  title: string
  time: string  // ISO
  todo_id?: string
  note_id?: string
  meta?: Record<string, unknown>
}

export async function createReminder(args: {
  title: string
  time: string | number | Date
  todo_id?: string
  note_id?: string
  meta?: Record<string, unknown>
}): Promise<{ id?: string } & Record<string, unknown>> {
  const iso = args.time instanceof Date
    ? args.time.toISOString()
    : typeof args.time === 'number'
      ? new Date(args.time).toISOString()
      : args.time
  return callRpc<{ id?: string } & Record<string, unknown>>('reminder.create', {
    title: args.title,
    time: iso,
    todo_id: args.todo_id,
    note_id: args.note_id,
    meta: args.meta,
  })
}

export async function listReminders(): Promise<ReminderRecord[]> {
  try {
    const res = await callRpc<unknown>('reminder.list', {})
    if (Array.isArray(res)) return res as ReminderRecord[]
    if (res && typeof res === 'object' && Array.isArray((res as { items?: unknown[] }).items)) {
      return (res as { items: ReminderRecord[] }).items
    }
    return []
  } catch {
    return []
  }
}

export async function deleteReminder(reminderId: string): Promise<boolean> {
  try {
    await callRpc('reminder.delete', { reminder_id: reminderId })
    return true
  } catch {
    return false
  }
}
