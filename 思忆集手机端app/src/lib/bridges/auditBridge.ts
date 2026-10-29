/**
 * 审计桥 — 封装桌面后端 `audit.*` RPC。
 *
 * 后端真源：`思忆集app/backend/handlers/_audit.py`
 *
 * ⚠️ 已逐一对齐后端（2026-10-29 核验）。重要修正：
 *  1. `audit.list` 参数是 `{limit, action_id, status}`，**没有 offset / rollbackable_only**。
 *     后端 limit 上限 500（默认 50，因单条约 7KB）。
 *  2. `audit.rollback` 返回 **`{rolled_back: bool, reason?}`**，不是 `{success, error}`。
 *  3. `audit.can_rollback` 返回 `{can_rollback: bool, reason?}`。
 *  4. `audit.batch_rollback` 返回 `{rolled_back: number, total: number, reason?}`（计数，不是布尔）。
 *  5. 无 `success` 信封 —— 失败以 `error` 字段表示，条目列表在 `entries`。
 *
 * 用途：桌面 Agent 的每个写操作留审计，支持单条/批量回滚。这是 AI 代操作的安全兜底。
 */

import { callRpc } from '../rpcClient'

export interface AuditEntry {
  /** 审计条目 id，回滚时必须用它 */
  id: string
  action_id?: string
  operation?: string
  target_type?: string
  target_id?: string
  status?: string
  actor?: string
  /** 列表接口会把它压成预览，get 接口返回完整快照 */
  request?: unknown
  result?: unknown
  created_at?: number | string
  rollbackable?: boolean
  rolled_back?: boolean
  [key: string]: unknown
}

export interface AuditStats {
  total?: number
  by_status?: Record<string, number>
  by_operation?: Record<string, number>
  by_target_type?: Record<string, number>
  rollbackable?: number
  [key: string]: unknown
}

export interface AuditListArgs {
  limit?: number
  /** 按动作 ID 过滤（后端字段名是 action_id，不是 action） */
  actionId?: string
  /** 按状态过滤，如 success / failed / pending */
  status?: string
}

interface Envelope {
  error?: string
  entries?: AuditEntry[]
  count?: number
  [key: string]: unknown
}

export async function list(args: AuditListArgs = {}): Promise<AuditEntry[]> {
  try {
    const res = await callRpc<Envelope>('audit.list', {
      limit: args.limit ?? 50,
      action_id: args.actionId ?? '',
      status: args.status ?? '',
    })
    return Array.isArray(res?.entries) ? res.entries : []
  } catch {
    return []
  }
}

export async function get(entryId: string): Promise<AuditEntry | null> {
  if (!entryId) return null
  try {
    const res = await callRpc<AuditEntry | null>('audit.get', { entry_id: entryId })
    return res ?? null
  } catch {
    return null
  }
}

export async function stats(): Promise<AuditStats | null> {
  try {
    const res = await callRpc<AuditStats & { error?: string }>('audit.stats', {})
    if (res && typeof res === 'object' && res.error) return null
    return res ?? null
  } catch {
    return null
  }
}

/**
 * 判断某条审计能否回滚。
 * 后端返回 `{can_rollback: bool, reason?}`。
 */
export async function canRollback(entryId: string): Promise<{ canRollback: boolean; reason?: string }> {
  if (!entryId) return { canRollback: false, reason: 'entry_id 不能为空' }
  try {
    const res = await callRpc<{ can_rollback?: boolean; reason?: string }>('audit.can_rollback', {
      entry_id: entryId,
    })
    return { canRollback: res?.can_rollback === true, reason: res?.reason }
  } catch (e) {
    return { canRollback: false, reason: e instanceof Error ? e.message : String(e) }
  }
}

/**
 * 回滚单条审计记录。
 *
 * ⚠️ 后端返回 `{rolled_back: bool, reason?}` —— **不是** `{success, error}`。
 * 判定成功必须看 `rolled_back`，看 `success` 会永远得到 undefined → 误判失败。
 */
export async function rollback(entryId: string): Promise<{ rolledBack: boolean; reason?: string }> {
  if (!entryId) return { rolledBack: false, reason: 'entry_id 不能为空' }
  try {
    const res = await callRpc<{ rolled_back?: boolean; reason?: string }>('audit.rollback', {
      entry_id: entryId,
    })
    return { rolledBack: res?.rolled_back === true, reason: res?.reason }
  } catch (e) {
    return { rolledBack: false, reason: e instanceof Error ? e.message : String(e) }
  }
}

/** 可回滚条目列表（后端 limit 上限 200）。 */
export async function listRollbackable(limit = 50): Promise<AuditEntry[]> {
  try {
    const res = await callRpc<Envelope>('audit.list_rollbackable', { limit })
    return Array.isArray(res?.entries) ? res.entries : []
  } catch {
    return []
  }
}

/**
 * 批量回滚。
 * 后端返回 `{rolled_back: number, total: number, reason?}` —— 是**成功条数**，
 * 不是布尔值。这里同时返回两个数，UI 可显示「3/5 成功」。
 */
export async function batchRollback(
  entryIds: string[],
): Promise<{ rolledBack: number; total: number; reason?: string }> {
  if (!entryIds || entryIds.length === 0) {
    return { rolledBack: 0, total: 0, reason: 'entry_ids 不能为空' }
  }
  try {
    const res = await callRpc<{ rolled_back?: number; total?: number; reason?: string }>(
      'audit.batch_rollback',
      { entry_ids: entryIds },
    )
    return {
      rolledBack: typeof res?.rolled_back === 'number' ? res.rolled_back : 0,
      total: typeof res?.total === 'number' ? res.total : entryIds.length,
      reason: res?.reason,
    }
  } catch (e) {
    return { rolledBack: 0, total: entryIds.length, reason: e instanceof Error ? e.message : String(e) }
  }
}
