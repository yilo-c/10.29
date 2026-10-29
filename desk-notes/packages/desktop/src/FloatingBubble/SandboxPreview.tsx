import { useState, useCallback } from 'react'

export interface SandboxPreviewData {
  pending_id: string
  action_id: string
  action: string
  path?: string
  source?: string
  dest?: string
  diff?: string
  new_size?: number
  old_size?: number
  source_exists?: boolean
  source_size?: number
  dest_exists?: boolean
  exists?: boolean
}

interface Props {
  preview: SandboxPreviewData
  onApprove: (pendingId: string) => void
  onReject: (pendingId: string) => void
  /** Called when the user wants to edit the content before approving */
  onEdit?: (pendingId: string, newContent: string) => void
}

export default function SandboxPreview({ preview, onApprove, onReject }: Props) {
  const [approving, setApproving] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [collapsed, setCollapsed] = useState(false)

  const handleApprove = useCallback(async () => {
    setApproving(true)
    try {
      await onApprove(preview.pending_id)
    } finally {
      setApproving(false)
    }
  }, [preview.pending_id, onApprove])

  const handleReject = useCallback(async () => {
    setRejecting(true)
    try {
      await onReject(preview.pending_id)
    } finally {
      setRejecting(false)
    }
  }, [preview.pending_id, onReject])

  const actionLabel =
    preview.action === 'create' ? '新建文件' :
    preview.action === 'modify' ? '修改文件' :
    preview.action === 'copy' ? '复制文件' : preview.action

  const isCreate = preview.action === 'create'
  const isModify = preview.action === 'modify'
  const isCopy = preview.action === 'copy'

  const diffLines = preview.diff ? preview.diff.split('\n') : []

  // Parse diff for line-by-line highlighting
  const parsedLines = diffLines.map((line, i) => {
    let type: 'header' | 'add' | 'del' | 'context' = 'context'
    if (line.startsWith('@@')) type = 'header'
    else if (line.startsWith('+') && !line.startsWith('+++')) type = 'add'
    else if (line.startsWith('-') && !line.startsWith('---')) type = 'del'
    return { line, type, key: i }
  })

  return (
    <div
      style={{
        border: '1px solid rgba(96,165,250,0.3)',
        borderRadius: 8,
        background: 'rgba(96,165,250,0.05)',
        overflow: 'hidden',
        marginTop: 8,
        marginBottom: 8,
      }}
    >
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 10px',
          background: 'rgba(96,165,250,0.1)',
          borderBottom: '1px solid rgba(96,165,250,0.15)',
          cursor: 'pointer',
          userSelect: 'none',
        }}
        onClick={() => setCollapsed(!collapsed)}
      >
        <span style={{ color: 'var(--brand)', fontSize: 11 }}>🛡️</span>
        <span style={{ color: '#93bbfc', fontSize: 11, fontWeight: 600, flex: 1 }}>
          {actionLabel}
        </span>
        <span style={{ color: '#6b7280', fontSize: 10, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {preview.path || preview.dest || ''}
        </span>
        <span style={{ color: '#6b7280', fontSize: 10 }}>{collapsed ? '▶' : '▼'}</span>
      </div>

      {!collapsed && (
        <>
          {/* Info row */}
          <div style={{ padding: '6px 10px', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {isCreate && (
              <span style={{ color: 'var(--success)', fontSize: 10 }}>
                新文件 · {preview.new_size} 字节
              </span>
            )}
            {isModify && (
              <span style={{ color: 'var(--warning)', fontSize: 10 }}>
                已有文件 ({preview.old_size} 字节 → {preview.new_size} 字节)
              </span>
            )}
            {isCopy && (
              <span style={{ color: 'var(--brand)', fontSize: 10 }}>
                从 {preview.source} → {preview.dest}
                {preview.source_exists ? ' ✅ 源文件存在' : ' ❌ 源文件不存在'}
                {preview.dest_exists ? ' ⚠️ 目标已存在' : ''}
              </span>
            )}
          </div>

          {/* Diff view */}
          {preview.diff && (
            <div
              style={{
                margin: '0 10px 8px',
                borderRadius: 4,
                background: 'rgba(0,0,0,0.3)',
                maxHeight: 240,
                overflow: 'auto',
                fontFamily: 'Consolas, "Courier New", monospace',
                fontSize: 10,
                lineHeight: '16px',
              }}
            >
              {parsedLines.map(({ line, type, key }) => (
                <div
                  key={key}
                  style={{
                    padding: '0 8px',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all',
                    background:
                      type === 'add' ? 'rgba(34,197,94,0.12)' :
                      type === 'del' ? 'rgba(239,68,68,0.12)' :
                      'transparent',
                    color:
                      type === 'header' ? 'var(--brand)' :
                      type === 'add' ? '#86efac' :
                      type === 'del' ? '#fca5a5' :
                      '#d1d5db',
                  }}
                >
                  {line}
                </div>
              ))}
            </div>
          )}

          {/* Actions */}
          <div style={{ display: 'flex', gap: 6, padding: '0 10px 8px' }}>
            <button
              onClick={handleApprove}
              disabled={approving}
              style={{
                flex: 1,
                padding: '5px 0',
                border: '1px solid rgba(34,197,94,0.3)',
                borderRadius: 4,
                background: approving ? 'rgba(34,197,94,0.08)' : 'rgba(34,197,94,0.12)',
                color: '#86efac',
                fontSize: 11,
                cursor: approving ? 'not-allowed' : 'pointer',
                opacity: approving ? 0.6 : 1,
              }}
            >
              {approving ? '执行中...' : '✓ 批准执行'}
            </button>
            <button
              onClick={handleReject}
              disabled={rejecting}
              style={{
                flex: 1,
                padding: '5px 0',
                border: '1px solid rgba(239,68,68,0.3)',
                borderRadius: 4,
                background: rejecting ? 'rgba(239,68,68,0.08)' : 'rgba(239,68,68,0.12)',
                color: '#fca5a5',
                fontSize: 11,
                cursor: rejecting ? 'not-allowed' : 'pointer',
                opacity: rejecting ? 0.6 : 1,
              }}
            >
              {rejecting ? '处理中...' : '✕ 拒绝'}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
