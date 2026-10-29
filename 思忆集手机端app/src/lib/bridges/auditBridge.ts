/**
 * 审计桥 — 封装桌面后端 `audit.*` RPC。
 *
 * 后端真源：`思忆集app/backend/handlers/_audit.py`
 * 方法：list / get / stats / rollback / can_rollback / list_rollbackable /
 *      batch_rollback
 *
 * 用途：桌面端 Agent 执行的每个写操作都会留审计记录，支持回滚。
 * 手机端在这里提供「看历史 + 一键撤销」，这是 AI 代操作的安全兜底。
 */
import { callRpc } from '../rpcClient'

export interface AuditEntry {
  id: string | number
  action?: string
  target?: string
  target_type?: string
  status?: string
  actor?: string
  detail?: Record<string, unknown> | string
  created_at?: string | number
  rollbackable?: boolean
  rolled_back?: boolean
  [key: string]: unknown
}

export interface AuditStats {
  total?: number
  rollbackable?: number
  rolled_back?: number
  by_action?: Record<string, number>
  [key: string]: unknown
}

export interface AuditListArgs {
  limit?: number
  offset?: number
  action?: string
  target_type?: string
  rollbackable_only?: boolean
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

export async function list(args: AuditListArgs = {}): Promise<AuditEntry[]> {
  try {
    const res = await callRpc<unknown>('audit.list', {
      limit: args.limit,
      offset: args.offset,
      action: args.action,
      target_type: args.target_type,
      rollbackable_only: args.rollbackable_only,
    })
    return asArray<AuditEntry>(res, 'entries', 'items', 'list', 'logs')
  } catch {
    return []
  }
}

export async function get(entryId: string | number): Promise<AuditEntry | null> {
  try {
    return await callRpc<AuditEntry>('audit.get', { entry_id: entryId })
  } catch {
    return null
  }
}

export async function stats(): Promise<AuditStats | null> {
  try {
    return await callRpc<AuditStats>('audit.stats', {})
  } catch {
    return null
  }
}

export async function canRollback(entryId: string | number): Promise<boolean> {
  try {
    const res = await callRpc<{ can_rollback?: boolean; rollbackable?: boolean } | boolean>(
      'audit.can_rollback',
      { entry_id: entryId },
    )
    if (typeof res === 'boolean') return res
    return res?.can_rollback === true || res?.rollbackable === true
  } catch {
    return false
  }
}

/** 回滚单条审计记录。 */
export async function rollback(entryId: string | number): Promise<{ success: boolean; error?: string }> {
  try {
    const res = await callRpc<{ success?: boolean; error?: string }>('audit.rollback', {
      entry_id: entryId,
    })
    return { success: res?.success !== false, error: res?.error }
  } catch (e) {
    return { success: false, error: e instanceof Error ? e.message : String(e) }
  }
}

export async function listRollbackable(limit = 50): Promise<AuditEntry[]> {
  try {
    const res = await callRpc<unknown>('audit.list_rollbackable', { limit })
    return asArray<AuditEntry>(res, 'entries', 'items', 'list')
  } catch {
    return []
  }
}

/** 批量回滚（谨慎使用，会一次性撤销多条改动）。 */
export async function batchRollback(entryIds: Array<string | number>): Promise<{ success: boolean; error?: string }> {
  try {
    const res = await callRpc<{ success?: boolean; error?: string }>('audit.batch_rollback', {
      entry_ids: entryIds,
    })
    return { success: res?.success !== false, error: res?.error }
  } catch (e) {
    return { success: false, error: e instanceof Error ? e.message : String(e) }
  }
}
