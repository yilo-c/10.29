import React, { useState } from 'react'
import { useSandbox } from '../../FloatingBubble/useSandbox'
import SandboxPreview from '../../FloatingBubble/SandboxPreview'
import SandboxTimeoutHints from './SandboxTimeoutHints'
import { useTranslation } from '../../i18n'

/**
 * ApprovalInbox — 全局审批收件箱（B-2/韧性审计 G3/N1）。
 *
 * 背景：R4 破坏性 RPC（provider.add / api_keys.create 等）返回
 * {pending:true, pending_id}，后端经 sandbox.pending_added 事件广播；
 * 但审批卡此前只渲染在旧 ChatContextPanel（旧聊天界面）里——新聊天页 /
 * AI 管理页 / 设置页均无审批入口，造成"添加 provider 点了没反应"的审批死锁。
 *
 * 本组件挂载于 App 全局层（主窗口所有视图可见），复用 useSandbox 的
 * 事件订阅 + 30s 兜底轮询；pendingList 为空时不渲染任何内容。
 * 注意：与 ChatContextPanel 的 useSandbox 实例各自轮询（已降频，共 4 msg/min），
 * 事件驱动为主，可接受；如后续收敛可提为共享 store。
 */
const ApprovalInbox: React.FC = () => {
  const sandbox = useSandbox()
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(true)

  if (sandbox.pendingList.length === 0) return null

  return (
    <div
      style={{
        position: 'fixed',
        right: 16,
        bottom: 96,
        zIndex: 60,
        width: 300,
        maxWidth: '90vw',
        border: '1px solid rgba(251,191,36,0.3)',
        borderRadius: 10,
        background: 'rgba(251,191,36,0.06)',
        backdropFilter: 'blur(8px)',
        boxShadow: '0 8px 32px rgba(0,0,0,0.18)',
        overflow: 'hidden',
      }}
    >
      <div
        onClick={() => setExpanded((v) => !v)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '7px 10px',
          cursor: 'pointer',
          userSelect: 'none',
        }}
      >
        <span style={{ fontSize: 12 }}>🛡️</span>
        <span style={{ color: '#fbbf24', fontSize: 11, fontWeight: 600 }}>
          {t('chatModule.sandboxPendingFmt', { count: sandbox.pendingList.length })}
        </span>
        <span style={{ color: '#6b7280', fontSize: 10, marginLeft: 'auto' }}>
          {expanded ? t('chatModule.sandboxCollapse') : t('chatModule.sandboxExpand')}
        </span>
      </div>
      {expanded && (
        <div style={{ padding: '0 10px 8px' }}>
          <SandboxTimeoutHints />
          {sandbox.pendingList.length >= 2 && (
            <button
              onClick={async () => {
                await sandbox.batchApprove(sandbox.pendingList.map((p) => p.pending_id))
              }}
              className="mb-1.5 px-2 py-1 rounded-md transition-all"
              style={{
                fontSize: 10,
                fontWeight: 600,
                color: '#34d399',
                background: 'rgba(52,211,153,0.1)',
                border: '1px solid rgba(52,211,153,0.3)',
              }}
            >
              全部批准（{sandbox.pendingList.length}）
            </button>
          )}
          {sandbox.pendingList.map((preview) => (
            <SandboxPreview
              key={preview.pending_id}
              preview={preview}
              onApprove={async (pid) => {
                await sandbox.approve(pid)
              }}
              onReject={async (pid) => {
                await sandbox.reject(pid)
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}

export default ApprovalInbox
