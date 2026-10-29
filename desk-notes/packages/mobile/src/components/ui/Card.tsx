import React from 'react'

interface CardProps {
  variant?: 'elevated' | 'flat' | 'collapsible'
  collapsed?: boolean
  onToggle?: () => void
  title?: string
  subtitle?: string
  children: React.ReactNode
  style?: React.CSSProperties
}

const cardBase: React.CSSProperties = {
  borderRadius: 'var(--radius-md)',
  padding: 'var(--space-4)',
  transition: `all var(--duration-md) var(--ease-out)`,
}

export const Card: React.FC<CardProps> = ({
  variant = 'elevated',
  collapsed,
  onToggle,
  title,
  subtitle,
  children,
  style,
}) => {
  const base: React.CSSProperties = {
    ...cardBase,
    background: variant === 'flat' ? 'transparent' : 'var(--bg-secondary)',
    border: variant === 'flat'
      ? '1px solid var(--border-card)'
      : '1px solid var(--border-subtle)',
    ...style,
  }

  return (
    <div style={base}>
      {title && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            cursor: onToggle ? 'pointer' : 'default',
            marginBottom: collapsed ? 0 : 'var(--space-3)',
          }}
          onClick={onToggle}
        >
          <div>
            <div style={{ fontSize: 'var(--text-h2)', fontWeight: 600 }}>{title}</div>
            {subtitle && (
              <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)', marginTop: 2 }}>
                {subtitle}
              </div>
            )}
          </div>
          {onToggle && (
            <span style={{ color: 'var(--text-tertiary)', fontSize: 14 }}>
              {collapsed ? '▶' : '▼'}
            </span>
          )}
        </div>
      )}
      {!collapsed && <div>{children}</div>}
    </div>
  )
}
