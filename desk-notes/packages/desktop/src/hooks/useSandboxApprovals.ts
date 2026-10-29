/**
 * M2.2 审批双形态的数据层：sandbox 待审批队列 + 已批准/拒绝历史。
 *
 * 事实源与通道（实地核对后端，只读）：
 * - 入队：`method_registry.py:220-253` R4 方法 → `SandboxManager.add(kind="rpc")`，
 *   preview = `{action, args, summary, args_sha256}`（指纹在 `sandbox_manager.py:265` 增记）。
 * - 事件：`sandbox.pending_added / approved / rejected / evicted / timeout(_warning)`
 *   （`sandbox_manager.py:280/317/345/360/168`）。**注意事件名是 `pending_added`，
 *   不是 sandbox.requested**。
 * - 列表 RPC：`sandbox.list_pending`（`handlers/_sandbox.py:32/182`）**已存在**，
 *   返回 `{pending_id, action_id, status, created_at, preview}`；
 *   批准/拒绝：`sandbox.approve` / `sandbox.reject`（带 `pending_id`）。
 * - **缺口**：后端无 `sandbox.list_history` 之类历史 RPC（rpc 类条目批准后
 *   `mgr._pending.pop()` 直接消失，`_sandbox.py:105`）→ 历史侧只能在浏览器端
 *   由 approved/rejected/timeout 事件累积（见 `loadHistory`），并已登记为缺口。
 *
 * 参数指纹语义：前端**只展示与转发** `pending_id`，`args_sha256` 的绑定与一次性
 * 消费全在后端（`_require_approved` / `_handle_sandbox_approve`），故卡片把指纹
 * 摘要显示出来供用户核对，但绝不参与任何"批准判断"。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { onBackendEvent } from '@desk-notes/shared'
import { getWsClient } from '../utils/ai'

export interface ApprovalPreview {
  action?: string
  summary?: string
  title?: string
  detail?: string
  diff?: string
  args?: Record<string, unknown>
  args_sha256?: string
  [k: string]: unknown
}

export interface ApprovalItem {
  pending_id: string
  action_id: string
  status?: string
  created_at?: number
  kind?: string
  preview: ApprovalPreview
}

export type ApprovalDecision = 'approved' | 'rejected' | 'failed' | 'timed_out'

export interface ApprovalHistoryEntry {
  pending_id: string
  action_id: string
  decision: ApprovalDecision
  at: number
  summary: string
}

const HISTORY_KEY = 'siyi-approval-history'
const HISTORY_CAP = 50

/** 参数摘要截断口径：≤200 字符（M2.1 SEC-2 已批口径，后端 approval_preview 同档）。 */
export const APPROVAL_SUMMARY_MAX = 200

const TARGET_ARG_KEYS = ['path', 'file_path', 'target', 'dest', 'destination', 'source', 'url', 'note_id', 'task_id', 'id', 'key', 'app_name', 'command']

export interface ApprovalSummary {
  /** 工具名 / RPC 方法名。 */
  tool: string
  /** 目标路径（args 里第一个命中的路径/标识类参数），缺列为空串。 */
  target: string
  /** 参数摘要，≤200 字符，单行化防伪造多行。 */
  summary: string
  /** 参数指纹短串（后端算，前端只显示）。 */
  fingerprint: string
}

function oneLine(text: string): string {
  return text.replace(/\r?\n/g, ' ').trim()
}

/** 从待审批条目派生卡片展示字段（纯函数，便于单测）。 */
export function summarizeApproval(item: ApprovalItem): ApprovalSummary {
  const preview = item.preview ?? {}
  const args = (preview.args ?? {}) as Record<string, unknown>
  let target = ''
  for (const key of TARGET_ARG_KEYS) {
    const value = args[key]
    if (typeof value === 'string' && value.trim()) { target = oneLine(value).slice(0, 160); break }
  }
  const rawSummary = oneLine(
    preview.detail
      || preview.summary
      || (Object.keys(args).length ? `${item.action_id} ${JSON.stringify(args)}` : item.action_id),
  )
  const head = preview.title ? `${preview.title}: ` : ''
  const summary = `${head}${rawSummary}`.slice(0, APPROVAL_SUMMARY_MAX)
  const sha = typeof preview.args_sha256 === 'string' ? preview.args_sha256 : ''
  return { tool: item.action_id, target, summary, fingerprint: sha.slice(0, 12) }
}

function readStoredHistory(): ApprovalHistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((e): e is ApprovalHistoryEntry => !!e && typeof e === 'object'
      && typeof (e as ApprovalHistoryEntry).pending_id === 'string'
      && typeof (e as ApprovalHistoryEntry).action_id === 'string')
  } catch {
    return []
  }
}

function writeStoredHistory(entries: ApprovalHistoryEntry[]): void {
  try { localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, HISTORY_CAP))) } catch { /* 配额/隐私模式：历史仅内存可见 */ }
}

/** 追加一条历史（按 pending_id 去重、新旧倒序、上限 50）。纯函数便于单测。 */
export function appendHistory(
  entries: ApprovalHistoryEntry[],
  next: ApprovalHistoryEntry,
  cap: number = HISTORY_CAP,
): ApprovalHistoryEntry[] {
  const rest = entries.filter((e) => e.pending_id !== next.pending_id)
  return [next, ...rest].slice(0, cap)
}

export interface SandboxApprovalsState {
  pending: ApprovalItem[]
  history: ApprovalHistoryEntry[]
  /** 批准：sandbox.approve(pending_id)，返回后端结果（含 error 时视为失败）。 */
  approve: (pendingId: string) => Promise<{ ok: boolean; error?: string }>
  reject: (pendingId: string) => Promise<{ ok: boolean; error?: string }>
  refresh: () => Promise<void>
  clearHistory: () => void
}

/**
 * 订阅 sandbox 事件通道并暴露待审批队列 + 历史。
 * 事件驱动为主（pending_added/approved/rejected/evicted/timeout → 重拉 list_pending），
 * 挂载时拉一次快照 + 事件后 300ms 合并刷新，不新增常驻轮询器
 * （ApprovalInbox 的 useSandbox 已保留 30s 兜底轮询）。
 */
export function useSandboxApprovals(): SandboxApprovalsState {
  const [pending, setPending] = useState<ApprovalItem[]>([])
  const [history, setHistory] = useState<ApprovalHistoryEntry[]>(() => readStoredHistory())
  const refreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const refresh = useCallback(async () => {
    const client = getWsClient()
    if (client.status !== 'connected') return
    try {
      const list = await client.call('sandbox.list_pending', {})
      setPending(Array.isArray(list) ? (list as ApprovalItem[]) : [])
    } catch { /* 后端不可用时保留上次快照，不打断 UI */ }
  }, [])

  const scheduleRefresh = useCallback(() => {
    if (refreshTimerRef.current) clearTimeout(refreshTimerRef.current)
    refreshTimerRef.current = setTimeout(() => { refreshTimerRef.current = null; void refresh() }, 300)
  }, [refresh])

  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const record = (decision: ApprovalDecision) => (payload: Record<string, unknown>) => {
      const pendingId = String(payload.pending_id ?? '')
      if (!pendingId) return
      const actionId = String(payload.action_id ?? '')
      const entry: ApprovalHistoryEntry = {
        pending_id: pendingId,
        action_id: actionId,
        decision,
        at: Date.now(),
        summary: typeof payload.error === 'string' ? payload.error : '',
      }
      setHistory((prev) => {
        const next = appendHistory(prev, entry)
        writeStoredHistory(next)
        return next
      })
      scheduleRefresh()
    }
    const offs = [
      onBackendEvent('sandbox.pending_added', scheduleRefresh),
      onBackendEvent('sandbox.approved', record('approved')),
      onBackendEvent('sandbox.rejected', record('rejected')),
      onBackendEvent('sandbox.evicted', scheduleRefresh),
      onBackendEvent('sandbox.timeout', record('timed_out')),
    ]
    return () => {
      for (const off of offs) off()
      if (refreshTimerRef.current) { clearTimeout(refreshTimerRef.current); refreshTimerRef.current = null }
    }
  }, [refresh, scheduleRefresh])

  const callResolve = useCallback(async (method: 'sandbox.approve' | 'sandbox.reject', pendingId: string) => {
    const client = getWsClient()
    if (client.status !== 'connected') return { ok: false, error: '后端未连接' }
    try {
      const result = await client.call<Record<string, unknown>>(method, { pending_id: pendingId })
      const error = result && typeof result.error === 'string' ? result.error : undefined
      setPending((prev) => (error ? prev : prev.filter((p) => p.pending_id !== pendingId)))
      scheduleRefresh()
      return { ok: !error, error }
    } catch (err) {
      return { ok: false, error: err instanceof Error ? err.message : String(err) }
    }
  }, [scheduleRefresh])

  const approve = useCallback((pendingId: string) => callResolve('sandbox.approve', pendingId), [callResolve])
  const reject = useCallback((pendingId: string) => callResolve('sandbox.reject', pendingId), [callResolve])
  const clearHistory = useCallback(() => {
    setHistory([])
    writeStoredHistory([])
  }, [])

  return { pending, history, approve, reject, refresh, clearHistory }
}
