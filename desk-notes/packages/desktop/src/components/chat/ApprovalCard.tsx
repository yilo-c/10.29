import { useState } from 'react'
import Icon from '../Common/Icon'
import { summarizeApproval, type ApprovalItem } from '../../hooks/useSandboxApprovals'

/**
 * M2.2 审批卡（聊天内嵌形态 + 收件箱形态共用）。
 *
 * 展示口径（照 M2.2 方案裁定，不发明）：工具名 / 目标路径 / 参数摘要 ≤200 字符
 * （`summarizeApproval`，与后端 `mcp_server/desktop.py:50 approval_preview` 同档），
 * 外加参数指纹短串用于**人工核对**——指纹的计算与绑定校验全在后端
 * （`sandbox_manager.py:265` 增记 args_sha256、`mcp_server/desktop.py:66 _require_approved`
 * 比对），前端只是展示与转发 `pending_id`，绝不做「参数变了还算批准」这类判断。
 *
 * 批准/拒绝按钮直连 `sandbox.approve` / `sandbox.reject`（带 pending_id）。
 */
export interface ApprovalCardProps {
  item: ApprovalItem
  onApprove: (pendingId: string) => Promise<{ ok: boolean; error?: string }>
  onReject: (pendingId: string) => Promise<{ ok: boolean; error?: string }>
  /** inline=聊天流内嵌（默认）；row=收件箱列表行。 */
  variant?: 'inline' | 'row'
  /** 已定态（收件箱历史侧复用同一卡片时传入）：不再出按钮。 */
  resolvedAs?: 'approved' | 'rejected' | 'timed_out' | 'failed'
}

export function ApprovalCard({ item, onApprove, onReject, variant = 'inline', resolvedAs }: ApprovalCardProps) {
  const { tool, target, summary, fingerprint } = summarizeApproval(item)
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [feedback, setFeedback] = useState<string | null>(null)
  const inline = variant === 'inline'

  const act = async (kind: 'approve' | 'reject') => {
    setBusy(kind)
    setFeedback(null)
    const run = kind === 'approve' ? onApprove : onReject
    const res = await run(item.pending_id)
    setBusy(null)
    if (!res.ok) setFeedback(res.error || '操作失败，请重试')
  }

  return (
    <div
      data-testid="approval-card"
      data-pending-id={item.pending_id}
      className={`${inline ? 'max-w-[72%]' : 'w-full'} rounded-md border border-warning/50 bg-surface px-3 py-2.5 text-2xs`}
      style={inline ? { boxShadow: '0 0 0 1px rgba(245,158,11,0.12), 0 6px 18px rgba(0,0,0,0.18)' } : undefined}
    >
      <div className="flex items-start gap-2">
        <div className="w-6 h-6 rounded-md bg-warning-soft text-warning flex items-center justify-center flex-shrink-0 text-xs">
          <Icon icon="fa-solid fa-shield-halved" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="font-medium text-text-primary">破坏性操作待批准</span>
            <code className="text-2xs px-1 py-0.5 rounded bg-hover-bg text-text-secondary">{tool}</code>
            {/* TODO(M4a-F2)：风险档位徽章（注册表 risk 四档 schema 的展示位）。
                当前后端未在 sandbox.pending_added payload 里下发 risk_level，
                徽章归 M4a-F2 与桌宠反应同批落地，此处只留位不留假数据。 */}
          </div>
          {target && (
            <div className="mt-1 text-text-secondary break-all" title={target}>
              <span className="text-text-muted">目标：</span>{target}
            </div>
          )}
          <div className="mt-1 text-text-secondary whitespace-pre-wrap break-all leading-relaxed">{summary}</div>
          {fingerprint && (
            <div className="mt-1 text-text-muted font-mono" title={`后端绑定校验的参数指纹（前端仅展示）：${fingerprint}`}>
              参数指纹 {fingerprint}
            </div>
          )}
        </div>
      </div>
      {resolvedAs ? (
        <div className="mt-2 flex items-center gap-1.5 text-text-muted">
          <Icon icon={`fa-solid fa-${resolvedAs === 'approved' ? 'check' : 'xmark'}`} />
          <span>{resolvedAs === 'approved' ? '已批准' : resolvedAs === 'rejected' ? '已拒绝' : resolvedAs === 'timed_out' ? '已超时' : '执行失败'}</span>
        </div>
      ) : (
        <div className="mt-2 flex items-center gap-2">
          <button
            type="button"
            data-testid="approval-approve"
            disabled={busy !== null}
            onClick={() => { void act('approve') }}
            className="px-2.5 py-1 rounded-md text-2xs font-medium bg-success text-white hover:opacity-90 transition-opacity disabled:opacity-50"
          >
            {busy === 'approve' ? '提交中…' : '批准'}
          </button>
          <button
            type="button"
            data-testid="approval-reject"
            disabled={busy !== null}
            onClick={() => { void act('reject') }}
            className="px-2.5 py-1 rounded-md text-2xs font-medium bg-hover-bg text-text-secondary border border-border hover:text-text-primary transition-colors disabled:opacity-50"
          >
            {busy === 'reject' ? '提交中…' : '拒绝'}
          </button>
          {feedback && <span className="text-danger break-all" role="alert">{feedback}</span>}
        </div>
      )}
    </div>
  )
}

export default ApprovalCard
