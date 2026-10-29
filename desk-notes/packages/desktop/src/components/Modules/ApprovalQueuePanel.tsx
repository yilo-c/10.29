import { useState } from 'react'
import Icon from '../Common/Icon'
import { ApprovalCard } from '../chat/ApprovalCard'
import { useSandboxApprovals, type ApprovalHistoryEntry } from '../../hooks/useSandboxApprovals'

/**
 * M2.2 ②：收件箱审批页（待处理队列 + 已批准/拒绝历史）。
 *
 * 与既有 `Common/ApprovalInbox.tsx`（右下角浮出卡）分工：浮卡负责「打断」，
 * 本页负责「可回溯的队列视图」，两者共用 `useSandboxApprovals` 的事件通道
 * （sandbox.pending_added / approved / rejected / evicted / timeout）。
 *
 * 数据源（实地核对后端注册表）：
 * - 待处理：RPC `sandbox.list_pending`（`handlers/_sandbox.py:32`）——**已存在**，非缺口。
 * - 历史：**缺口** ——后端无 `sandbox.list_history`，rpc 类条目批准后即从
 *   `mgr._pending` 弹出（`_sandbox.py:105`），无服务端留存。本面板的历史侧由
 *   浏览器端事件累积（localStorage，上限 50 条），并已登记该 RPC 缺口。
 */

function formatWhen(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const DECISION_LABEL: Record<ApprovalHistoryEntry['decision'], string> = {
  approved: '已批准',
  rejected: '已拒绝',
  failed: '执行失败',
  timed_out: '已超时',
}

export function ApprovalQueuePanel() {
  const { pending, history, approve, reject, clearHistory } = useSandboxApprovals()
  const [open, setOpen] = useState(true)
  if (pending.length === 0 && history.length === 0) return null

  return (
    <div data-testid="approval-queue-panel" className="border-b px-4 py-2 space-y-2 flex-shrink-0" style={{ borderColor: 'var(--separator)' }}>
      <div className="flex items-center gap-2">
        <Icon icon="fa-solid fa-shield-halved text-xs" style={{ color: 'var(--warning)' }} />
        <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>审批</span>
        <span className="text-xs px-1.5 py-0.5 rounded-full" style={{ background: 'var(--hover-bg)', color: 'var(--text-dim)' }}>
          待处理 {pending.length}
        </span>
        <span className="text-xs px-1.5 py-0.5 rounded-full" style={{ background: 'var(--hover-bg)', color: 'var(--text-dim)' }}>
          历史 {history.length}
        </span>
        <button
          onClick={() => setOpen((v) => !v)}
          className="ml-auto text-xs transition-colors"
          style={{ color: 'var(--text-muted)' }}
        >
          {open ? '收起' : '展开'}
        </button>
        {history.length > 0 && (
          <button
            onClick={clearHistory}
            className="text-xs transition-colors"
            style={{ color: 'var(--text-muted)' }}
            title="清空本机审批历史（仅浏览器端记录）"
          >
            清空历史
          </button>
        )}
      </div>
      {open && (
        <>
          <div className="space-y-1.5">
            {pending.map((item) => (
              <ApprovalCard key={item.pending_id} item={item} variant="row" onApprove={approve} onReject={reject} />
            ))}
            {pending.length === 0 && (
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>没有待批准的破坏性操作。</p>
            )}
          </div>
          {history.length > 0 && (
            <ul className="space-y-1">
              {history.slice(0, 10).map((h) => (
                <li key={`${h.pending_id}-${h.at}`} className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
                  <Icon icon={`fa-solid fa-${h.decision === 'approved' ? 'check' : 'xmark'}`} />
                  <span className="font-mono" style={{ color: 'var(--text-secondary)' }}>{h.action_id}</span>
                  <span>{DECISION_LABEL[h.decision]}</span>
                  <span className="ml-auto">{formatWhen(h.at)}</span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

export default ApprovalQueuePanel
