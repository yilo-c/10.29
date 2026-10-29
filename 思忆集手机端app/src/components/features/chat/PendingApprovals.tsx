import { useEffect, useCallback } from 'react'
import { useMobileStore } from '../../../store/mobileStore'
import type { PendingApproval } from '../../../lib/rpcClient'

function ApprovalItem({ item }: { item: PendingApproval }) {
  const approvePending = useMobileStore(s => s.approvePending)
  const rejectPending = useMobileStore(s => s.rejectPending)

  const handleApprove = useCallback(async () => {
    await approvePending(item.pending_id)
  }, [item.pending_id, approvePending])

  const handleReject = useCallback(async () => {
    await rejectPending(item.pending_id)
  }, [item.pending_id, rejectPending])

  const preview = item.preview
  const label = preview?.action === 'create' ? '创建' : preview?.action === 'modify' ? '修改' : item.action_id
  const path = preview?.path || ''

  return (
    <div style={{
      background: 'var(--bg-secondary)',
      borderRadius: 'var(--radius-md)',
      padding: '10px 12px',
      border: '0.5px solid var(--border-default)',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        marginBottom: 4,
      }}>
        <span style={{
          fontSize: 'var(--text-caption-1)',
          fontWeight: 600,
          color: 'var(--text-primary)',
        }}>
          待审批操作
        </span>
        <span style={{
          fontSize: 'var(--text-caption-2)',
          color: 'var(--text-tertiary)',
        }}>
          {label}
        </span>
      </div>
      {path && (
        <div style={{
          fontSize: 'var(--text-caption-2)',
          color: 'var(--text-secondary)',
          marginBottom: 8,
          wordBreak: 'break-all',
          fontFamily: 'monospace',
        }}>
          {path}
        </div>
      )}
      {preview?.diff && preview.diff.length < 300 && (
        <pre style={{
          fontSize: 'var(--text-caption-2)',
          color: 'var(--text-secondary)',
          background: 'var(--bg-tertiary)',
          padding: '6px 8px',
          borderRadius: 'var(--radius-sm)',
          margin: '0 0 8px',
          overflow: 'auto',
          maxHeight: 80,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
        }}>
          {preview.diff}
        </pre>
      )}
      <div style={{
        display: 'flex', gap: 8,
        justifyContent: 'flex-end',
      }}>
        <button
          onClick={handleReject}
          style={{
            padding: '6px 14px',
            borderRadius: 'var(--radius-md)',
            border: '0.5px solid var(--border-default)',
            background: 'var(--bg-primary)',
            color: 'var(--text-secondary)',
            fontSize: 'var(--text-footnote)',
            cursor: 'pointer',
          }}
        >
          拒绝
        </button>
        <button
          onClick={handleApprove}
          style={{
            padding: '6px 14px',
            borderRadius: 'var(--radius-md)',
            border: 'none',
            background: 'var(--accent)',
            color: '#fff',
            fontSize: 'var(--text-footnote)',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          批准
        </button>
      </div>
    </div>
  )
}

export default function PendingApprovals() {
  const approvals = useMobileStore(s => s.approvals)
  const fetchApprovals = useMobileStore(s => s.fetchApprovals)
  const isPaired = useMobileStore(s => s.pairing.isPaired)

  // Poll every 10 seconds when paired and connected
  useEffect(() => {
    if (!isPaired) return
    fetchApprovals()
    const timer = setInterval(fetchApprovals, 10000)
    return () => clearInterval(timer)
  }, [isPaired, fetchApprovals])

  if (!isPaired || approvals.pending.length === 0) return null

  return (
    <div style={{
      padding: '8px 16px 0',
      display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      {approvals.loading && approvals.pending.length === 0 && (
        <div style={{
          fontSize: 'var(--text-caption-2)',
          color: 'var(--text-tertiary)',
          textAlign: 'center',
          padding: '8px 0',
        }}>
          检查待审批操作...
        </div>
      )}
      {approvals.error && (
        <div style={{
          fontSize: 'var(--text-caption-2)',
          color: '#FF3B30',
          textAlign: 'center',
          padding: '4px 0',
        }}>
          {approvals.error}
        </div>
      )}
      {approvals.pending.map(item => (
        <ApprovalItem key={item.pending_id} item={item} />
      ))}
    </div>
  )
}
