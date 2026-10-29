/**
 * F-P1-1（2026-09-17）队列2（butler CONFIRM 审批链）的数据层 —— 悬浮端接通。
 *
 * 与队列1（`useSandboxApprovals`，R4 RPC → sandbox.approve {pending_id}）是
 * **两套互不相通的审批队列**，id 空间不得混用（跨用 → expired_or_not_found 假象）：
 *
 * 事实源与通道（实地核对后端，只读）：
 * - 发起：`services/confirmation_flow.py:create_and_emit_confirmation` →
 *   `confirmation_store.create`（TTL 300s，`_CONFIRM_TTL_SECONDS`）+ 发射
 *   `action.confirm_required`，payload = `{confirmation_id, action_id, args, source, reason}`。
 *   source ∈ butler（批准后 chat.resume 从 checkpoint 续跑）/ mcp:kernel（DSH，
 *   批准后走「重新下发」路径，属主窗内核分支）。
 * - 裁决：`confirmation.resolve {confirmation_id, approved, reason, always_allow}`
 *   （`backend/handlers/_confirmations.py:82-102`）。批准必带 reason（前端必填 +
 *   后端校验 `:55-61` → `{success:false, reason:'approval_reason_required'}`）；
 *   拒绝不强制理由。`always_allow=true` 时后端把 action_id 写入
 *   `assistant_confirm_always_allow` 名单（B2）。
 * - 过期/重复消费：`services/confirmation_flow.py:162` resolve 返回
 *   `{success:false, reason:'confirmation_not_found_or_expired'}`（一次性消费 +
 *   锁内 TTL 检查 `confirmation_store.py:148-152`），**不发** `action.confirm_resolved`
 *   → 前端必须本地把卡片翻成 expired 态并撤下按钮，否则死循环弹回。
 * - 定案事件：resolve 成功后编排层发射 `action.confirm_resolved`
 *   （`confirmation_flow.py:171-180`），两窗都经 event bus 收到 → 卡片翻
 *   approved/rejected（主窗 ChatView 同款，不乐观翻转）。
 *
 * 与主窗（`components/chat/ChatView.tsx:209-271`）的行为对齐点：
 * ① 去重：内核重试会对同一 (action_id, args) 重复发起，pending 只保留最新一条；
 * ② 批准成功后才自动续跑（拒绝不 resume）；③ 理由必填只在批准侧强制；
 * ④ 不做 tool 帧 `pending_confirmation` 兜底提取 —— 主窗没有这条路，两窗对齐。
 *
 * 续跑（chat.resume）不在本 hook 内做流消费：批准成功后回调
 * `onApprovedResume(source)`，由宿主（FloatingChatApp）以**共享 session_id**
 * 消费 `chat.resume` 流 —— 会话 id 属宿主真源，hook 不持有。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { onBackendEvent } from '@desk-notes/shared'
import { getWsClient } from '../utils/ai'

/** 队列2 条目（action.confirm_required 的形状，confirmation_store 条目契约）。 */
export interface ConfirmItem {
  confirmation_id: string
  action_id: string
  args: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected' | 'expired'
  /** 发起来源：butler（批准后可 checkpoint 续跑）| mcp:kernel（内核重新下发分支） */
  source: string
}

export interface ConfirmResolveResult {
  ok: boolean
  /** resolve 得到 expired/not_found 类结果 → 卡片已本地翻 expired。 */
  expired?: boolean
  error?: string
}

export interface UseConfirmApprovalsState {
  /** 全部条目（含已定案/过期，渲染侧自行过滤展示密度）。 */
  items: ConfirmItem[]
  /** 仅 pending（卡片队列口径，与 useSandboxApprovals.pending 对仗）。 */
  pending: ConfirmItem[]
  approve: (confirmationId: string, reason: string, alwaysAllow?: boolean) => Promise<ConfirmResolveResult>
  reject: (confirmationId: string, reason?: string) => Promise<ConfirmResolveResult>
}

/** expired/not_found 判定（后端 marker 为英文；中文串兜 RPC 层转译文案）。 */
export function isExpiredOutcome(text: string): boolean {
  return /expired|not_found|已过期|不存在/i.test(text || '')
}

/** 从 resolve 的 `{success:false, reason|message}` 返回体提取过期信号。 */
export function resolveFailureSignal(result: Record<string, unknown> | null | undefined):
  { expired: true } | { expired: false; error: string } {
  const res = result && typeof result === 'object' ? result : {}
  const reason = typeof res.reason === 'string' ? res.reason : ''
  const message = typeof res.message === 'string' ? res.message : ''
  if (isExpiredOutcome(reason) || isExpiredOutcome(message)) return { expired: true }
  return { expired: false, error: message || reason || '审批提交失败' }
}

/**
 * 订阅队列2 事件（action.confirm_required / action.confirm_resolved，
 * 经 backendEventBus 通配分发 —— 与 useSandboxApprovals 同一条订阅通道），
 * 暴露待批卡片队列与 resolve 动作。批准成功后回调 onApprovedResume（宿主续跑）。
 */
export function useConfirmApprovals(options: {
  /** 批准且 resolve 成功后触发（宿主以共享 session_id 走 chat.resume）。 */
  onApprovedResume?: (source: string) => Promise<void> | void
} = {}): UseConfirmApprovalsState {
  const [items, setItems] = useState<ConfirmItem[]>([])
  const itemsRef = useRef(items)
  itemsRef.current = items
  const resumeRef = useRef(options.onApprovedResume)
  resumeRef.current = options.onApprovedResume

  useEffect(() => {
    const offReq = onBackendEvent('action.confirm_required', (payload) => {
      const confirmation_id = String(payload.confirmation_id || '')
      if (!confirmation_id) return
      const action_id = String(payload.action_id || '')
      const args = (payload.args as Record<string, unknown>) || {}
      const argsKey = JSON.stringify(args)
      setItems((list) => [
        // 去重（主窗同款）：同 (action_id, args) 的旧 pending 被新发起顶替
        ...list.filter((c) => c.status !== 'pending' || c.action_id !== action_id || JSON.stringify(c.args || {}) !== argsKey),
        { confirmation_id, action_id, args, status: 'pending', source: String(payload.source || '') },
      ])
    })
    const offRes = onBackendEvent('action.confirm_resolved', (payload) => {
      const confirmation_id = String(payload.confirmation_id || '')
      if (!confirmation_id) return
      const approved = payload.approved === true
      // 已定案/过期条目不再翻转（防过期后迟到的 resolved 事件复活卡片）
      setItems((list) => list.map((c) => (c.confirmation_id === confirmation_id && c.status === 'pending'
        ? { ...c, status: approved ? 'approved' : 'rejected' }
        : c)))
    })
    return () => { offReq(); offRes() }
  }, [])

  const markExpired = useCallback((confirmationId: string) => {
    setItems((list) => list.map((c) => (c.confirmation_id === confirmationId && c.status === 'pending'
      ? { ...c, status: 'expired' }
      : c)))
  }, [])

  const resolve = useCallback(async (
    confirmationId: string,
    approved: boolean,
    reason: string,
    alwaysAllow = false,
  ): Promise<ConfirmResolveResult> => {
    const client = getWsClient()
    if (!client || client.status !== 'connected') return { ok: false, error: '后端未连接' }
    const source = itemsRef.current.find((c) => c.confirmation_id === confirmationId)?.source ?? ''
    let result: Record<string, unknown> | undefined
    try {
      // 契约（_confirmations.py:82-129）：confirmation_id 必填；
      // reason/always_allow 走 _resolve_with_always_allow 分支。
      result = await client.call<Record<string, unknown>>('confirmation.resolve', {
        confirmation_id: confirmationId,
        approved,
        reason,
        always_allow: alwaysAllow,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      // JSON-RPC 层把 not_found/expired 以异常形态抛回时同样收敛为 expired 态
      if (isExpiredOutcome(message)) {
        markExpired(confirmationId)
        return { ok: false, expired: true }
      }
      return { ok: false, error: message }
    }
    if (result && typeof result === 'object' && result.success === false) {
      const signal = resolveFailureSignal(result)
      if (signal.expired) {
        markExpired(confirmationId)
        return { ok: false, expired: true }
      }
      // approval_reason_required / args_sha256 不符等：卡片留在 pending，如实反馈
      return { ok: false, error: signal.error }
    }
    // 成功：pending → approved/rejected 由 action.confirm_resolved 事件驱动翻转
    // （本连接发起的 resolve 后端也会广播，主窗同款），不乐观改状态。
    if (approved) {
      try {
        await resumeRef.current?.(source)
      } catch { /* 续跑失败由宿主自行提示，不影响已生效的批准 */ }
    }
    return { ok: true }
  }, [markExpired])

  const approve = useCallback((confirmationId: string, reason: string, alwaysAllow = false) => {
    // H 项：批准理由必填（前端闸 + 后端校验双保险，与主窗同款）
    const trimmed = (reason || '').trim()
    if (!trimmed) return Promise.resolve<ConfirmResolveResult>({ ok: false, error: '批准操作必须填写理由' })
    return resolve(confirmationId, true, trimmed, alwaysAllow)
  }, [resolve])

  const reject = useCallback((confirmationId: string, reason = '') => {
    // 拒绝不强制理由（后端向后兼容缺省空串）；填了就如实传（审计标记）
    return resolve(confirmationId, false, (reason || '').trim(), false)
  }, [resolve])

  return {
    items,
    pending: items.filter((c) => c.status === 'pending'),
    approve,
    reject,
  }
}
