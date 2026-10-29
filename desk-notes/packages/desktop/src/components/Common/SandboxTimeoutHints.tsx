/**
 * A4（前后端对齐规划）：审批超时提示。
 *
 * 数据来源：sandbox.timeout / sandbox.timeout_warning 事件经
 * backendEventsStore 落点（A1 接入层）；后端策略见
 * services/sandbox_manager.py（默认 30 分钟超时，临期 60s 提醒，
 * reject/dry_run 两种处置策略）。
 *
 * i18n 说明：按用户确认不做英文界面，本组件直接使用中文文案。
 */
import React from 'react'
import { useBackendEventsStore } from '@desk-notes/shared'

const MAX_HINTS = 3

const SandboxTimeoutHints: React.FC = () => {
  const timeouts = useBackendEventsStore((s) => s.sandboxTimeouts)
  const dismiss = useBackendEventsStore((s) => s.dismissSandboxTimeout)

  if (timeouts.length === 0) return null

  return (
    <div className="space-y-1 mb-1.5">
      {timeouts.slice(0, MAX_HINTS).map((t) => (
        <div
          key={`${t.pending_id}-${t.kind}`}
          className="flex items-center gap-1.5 px-2 py-1 rounded-md"
          style={{
            background: t.kind === 'timeout' ? 'rgba(248,113,113,0.08)' : 'rgba(251,191,36,0.08)',
            border: `1px solid ${t.kind === 'timeout' ? 'rgba(248,113,113,0.3)' : 'rgba(251,191,36,0.3)'}`,
          }}
        >
          <span style={{ fontSize: 10 }}>{t.kind === 'timeout' ? '⏰' : '⚠️'}</span>
          <span
            className="flex-1 truncate"
            style={{ fontSize: 10, color: t.kind === 'timeout' ? 'var(--danger)' : 'var(--warning)' }}
          >
            {t.kind === 'timeout'
              ? `审批 ${t.action_id || t.pending_id} 已超时${t.policy === 'dry_run' ? '（降级为只读预演）' : '（已自动拒绝）'}`
              : `审批 ${t.action_id || t.pending_id} 将在 ${t.remaining_seconds ?? 60}s 后超时`}
          </span>
          <button
            onClick={() => dismiss(t.pending_id)}
            className="shrink-0 transition-all hover:opacity-70"
            style={{ fontSize: 10, color: 'var(--text-muted)' }}
            title="关闭提示"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  )
}

export default SandboxTimeoutHints
