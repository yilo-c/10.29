import React from 'react'

interface EmptyStateProps {
  icon?: React.ReactNode
  title: string
  description?: string
  action?: React.ReactNode
}

export default function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '60px 40px',
      textAlign: 'center',
      gap: 12,
    }}>
      {icon && (
        <div style={{
          color: 'var(--text-disabled)',
          marginBottom: 4,
        }}>
          {icon}
        </div>
      )}
      <div style={{
        fontSize: 'var(--text-headline)',
        fontWeight: 600,
        color: 'var(--text-secondary)',
      }}>
        {title}
      </div>
      {description && (
        <div style={{
          fontSize: 'var(--text-subhead)',
          color: 'var(--text-tertiary)',
          lineHeight: 1.5,
          maxWidth: 260,
        }}>
          {description}
        </div>
      )}
      {action && <div style={{ marginTop: 8 }}>{action}</div>}
    </div>
  )
}
