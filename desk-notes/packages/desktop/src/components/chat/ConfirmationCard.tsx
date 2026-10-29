import { useState } from 'react'
import Icon from '../Common/Icon'
import type { ConfirmItem } from '../../hooks/useConfirmApprovals'

/**
 * F-P1-1（2026-09-17）队列2（butler CONFIRM）审批卡 —— 悬浮端接通用。
 *
 * 契约对照主窗 `components/chat/ChatView.tsx:673-727` 的内联审批浮出卡
 * （P1b），语义逐项同款、不另发明：
 * - 展示 action_id + 「▸ 查看参数」折叠 args JSON；
 * - 批准理由**前端必填**（H 项）——不填则「允许执行/始终允许」禁用
 *   （后端 `_confirmations.py:55-61` 同款校验，双保险）；
 * - 「始终允许」= B2 语义：批准后把该能力写入 assistant_confirm_always_allow
 *   名单（仍受白名单与沙箱约束、写入审计）；
 * - 拒绝不强制理由。
 * 已定案/过期条目收为静态行，不再出按钮。
 *
 * 与队列1 的 `ApprovalCard`（sandbox.approve {pending_id}）是两套独立契约，
 * id 空间不互通 —— 本卡只调 confirmation.resolve 链（经 useConfirmApprovals）。
 */
export interface ConfirmationCardProps {
  item: ConfirmItem
  /** 批准（宿主须带理由走 confirmation.resolve，成功后自动 chat.resume）。 */
  onApprove: (confirmationId: string, reason: string, alwaysAllow: boolean) => Promise<{ ok: boolean; expired?: boolean; error?: string }>
  /** 拒绝（reason 可选，如实传）。 */
  onReject: (confirmationId: string, reason?: string) => Promise<{ ok: boolean; expired?: boolean; error?: string }>
  /** compact=悬浮窗高密度形态（默认）；float=主窗浮出卡形态（入场动画归宿主）。 */
  variant?: 'compact' | 'float'
}

export function ConfirmationCard({ item, onApprove, onReject, variant = 'compact' }: ConfirmationCardProps) {
  const [reason, setReason] = useState('')
  const [showArgs, setShowArgs] = useState(false)
  const [busy, setBusy] = useState<'approve' | 'always' | 'reject' | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const reasonTrimmed = reason.trim()
  const argsEntries = Object.entries(item.args ?? {})

  const act = async (kind: 'approve' | 'always' | 'reject') => {
    setBusy(kind)
    setFeedback(null)
    const res = kind === 'reject'
      ? await onReject(item.confirmation_id, reasonTrimmed)
      : await onApprove(item.confirmation_id, reasonTrimmed, kind === 'always')
    setBusy(null)
    if (!res.ok) setFeedback(res.expired ? '确认已过期，请重新发起操作' : (res.error || '提交失败，请重试'))
  }

  if (item.status !== 'pending') {
    return (
      <div
        data-testid="confirmation-card"
        data-confirmation-id={item.confirmation_id}
        data-status={item.status}
        className={`${variant === 'float' ? 'max-w-[72%]' : 'w-full'} rounded-md border border-border bg-surface px-3 py-1.5 text-2xs text-text-muted`}
      >
        <Icon icon={`fa-solid fa-${item.status === 'approved' ? 'check' : 'xmark'}`} className="mr-1.5" />
        <code className="px-1 py-0.5 rounded bg-hover-bg text-text-secondary">{item.action_id}</code>
        {item.status === 'approved' ? ' 已批准并执行' : item.status === 'rejected' ? ' 已拒绝' : ' 已过期（未审批）'}
      </div>
    )
  }

  return (
    <div
      data-testid="confirmation-card"
      data-confirmation-id={item.confirmation_id}
      data-status="pending"
      className={`${variant === 'float' ? 'max-w-[72%]' : 'w-full'} rounded-md border border-warning/50 bg-surface px-3 py-2.5 text-2xs`}
      style={{ boxShadow: '0 0 0 1px rgba(245,158,11,0.12), 0 6px 18px rgba(0,0,0,0.18)' }}
    >
      <div className="flex items-start gap-2">
        <div className="w-6 h-6 rounded-md bg-warning-soft text-warning flex items-center justify-center flex-shrink-0 text-xs">
          <Icon icon="fa-solid fa-shield-halved" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-medium text-text-primary">
            小问请求执行需要确认的操作：<code className="text-2xs px-1 py-0.5 rounded bg-hover-bg">{item.action_id}</code>
          </div>
          {argsEntries.length > 0 && (
            <button
              type="button"
              data-testid="confirmation-toggle-args"
              onClick={() => setShowArgs((s) => !s)}
              className="mt-1 text-2xs text-text-muted hover:text-text-secondary transition-colors"
            >
              {showArgs ? '▾ 收起参数' : '▸ 查看参数'}
            </button>
          )}
          {showArgs && (
            <pre className="text-2xs text-text-secondary whitespace-pre-wrap break-all max-h-28 overflow-y-auto mt-1 mb-1 rounded bg-hover-bg p-2">
              {JSON.stringify(item.args, null, 2)}
            </pre>
          )}
        </div>
      </div>
      {/* 审批理由（前端必填）——与主窗同款：不带理由两个批准键禁用 */}
      <input
        type="text"
        data-testid="confirmation-reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="批准理由（必填，将写入审计）"
        className="w-full mt-2 mb-2 px-2 py-1.5 rounded-lg text-2xs bg-hover-bg border border-border text-text-primary outline-none focus:border-brand placeholder:text-text-muted"
        aria-label="批准理由"
      />
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          data-testid="confirmation-approve"
          disabled={!reasonTrimmed || busy !== null}
          onClick={() => { void act('approve') }}
          className="px-2.5 py-1 rounded-md text-2xs font-medium bg-brand text-white hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy === 'approve' ? '提交中…' : '允许执行'}
        </button>
        <button
          type="button"
          data-testid="confirmation-always"
          disabled={!reasonTrimmed || busy !== null}
          onClick={() => { void act('always') }}
          title="批准本次执行，并把该能力加入「始终允许」名单——今后同类操作不再询问（仍受白名单与沙箱约束，且写入审计）"
          className="px-2.5 py-1 rounded-md text-2xs font-medium border border-brand text-brand hover:bg-brand-soft transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {busy === 'always' ? '提交中…' : '始终允许'}
        </button>
        <button
          type="button"
          data-testid="confirmation-reject"
          disabled={busy !== null}
          onClick={() => { void act('reject') }}
          className="px-2.5 py-1 rounded-md text-2xs font-medium border border-border text-text-secondary hover:bg-hover-bg transition-colors disabled:opacity-50"
        >
          {busy === 'reject' ? '提交中…' : '拒绝'}
        </button>
        {feedback && <span className="text-danger break-all" role="alert">{feedback}</span>}
      </div>
    </div>
  )
}

export default ConfirmationCard
