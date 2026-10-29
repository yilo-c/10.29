interface AgentConfirmBubbleProps {
  title: string
  detail?: string
  onApprove: () => void | Promise<void>
  onReject: () => void | Promise<void>
}

export default function AgentConfirmBubble({ title, detail, onApprove, onReject }: AgentConfirmBubbleProps) {
  return (
    <div style={{
      padding: '10px 12px',
      borderRadius: 'var(--radius-lg)',
      background: 'rgba(255,149,0,0.10)',
      border: '0.5px solid rgba(255,149,0,0.35)',
      color: 'var(--text-primary)',
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 'var(--text-footnote)',
        fontWeight: 700,
        marginBottom: 4,
      }}>
        <span style={{ color: '#FF9500', fontWeight: 800 }}>!</span>
        <span>{title}</span>
      </div>
      {detail && (
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-secondary)',
          lineHeight: 1.5,
          marginBottom: 10,
          whiteSpace: 'pre-wrap',
        }}>
          {detail}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
        <button
          onClick={onReject}
          style={{
            padding: '6px 14px',
            borderRadius: 'var(--radius-md)',
            border: '0.5px solid var(--border-default)',
            background: 'var(--bg-primary)',
            color: 'var(--text-secondary)',
            fontSize: 'var(--text-footnote)',
          }}
        >
          取消
        </button>
        <button
          onClick={onApprove}
          style={{
            padding: '6px 14px',
            borderRadius: 'var(--radius-md)',
            border: 'none',
            background: '#FF9500',
            color: '#fff',
            fontSize: 'var(--text-footnote)',
            fontWeight: 700,
          }}
        >
          允许
        </button>
      </div>
    </div>
  )
}
