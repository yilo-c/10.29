export interface AgentStep {
  icon: string
  title: string
  detail?: string
  status: 'running' | 'completed' | 'failed' | 'pending'
}

interface AgentStepBubbleProps {
  steps: AgentStep[]
}

const statusColor: Record<AgentStep['status'], string> = {
  running: '#0A84FF',
  completed: '#34C759',
  failed: '#FF3B30',
  pending: 'var(--text-tertiary)',
}

const statusLabel: Record<AgentStep['status'], string> = {
  running: '进行中',
  completed: '完成',
  failed: '失败',
  pending: '等待',
}

export default function AgentStepBubble({ steps }: AgentStepBubbleProps) {
  if (steps.length === 0) return null

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      gap: 8,
      padding: '4px 0',
    }}>
      {steps.map((step, index) => (
        <div key={`${step.title}-${index}`} style={{
          display: 'flex',
          gap: 8,
          alignItems: 'flex-start',
          padding: '8px 10px',
          borderRadius: 'var(--radius-md)',
          background: 'rgba(255,255,255,0.04)',
          border: '0.5px solid var(--border-subtle)',
        }}>
          <span style={{ fontSize: 16, lineHeight: '20px' }}>{step.icon}</span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 'var(--text-footnote)',
              fontWeight: 600,
              color: 'var(--text-primary)',
            }}>
              <span>{step.title}</span>
              <span style={{ color: statusColor[step.status], fontSize: 'var(--text-caption-2)' }}>
                {statusLabel[step.status]}
              </span>
            </div>
            {step.detail && (
              <div style={{
                marginTop: 2,
                fontSize: 'var(--text-caption-2)',
                color: 'var(--text-tertiary)',
                wordBreak: 'break-word',
              }}>
                {step.detail}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
