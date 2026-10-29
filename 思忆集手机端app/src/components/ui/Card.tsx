import React from 'react'
import { IconChevronDown } from './Icons'

interface CardProps {
  variant?: 'elevated' | 'flat' | 'collapsible'
  collapsed?: boolean
  onToggle?: () => void
  title?: string
  subtitle?: string
  children: React.ReactNode
  style?: React.CSSProperties
}

export function Card({
  variant = 'elevated',
  collapsed,
  onToggle,
  title,
  subtitle,
  children,
  style,
}: CardProps) {
  return (
    <div style={{
      borderRadius: 'var(--radius-md)',
      padding: 'var(--space-4)',
      background: variant === 'flat' ? 'transparent' : 'var(--bg-secondary)',
      border: variant === 'flat'
        ? '0.5px solid var(--border-card)'
        : '0.5px solid var(--border-subtle)',
      boxShadow: variant === 'elevated' ? 'var(--shadow-card)' : 'none',
      transition: `all var(--duration-normal) var(--ease-out)`,
      ...style,
    }}>
      {title && (
        <div
          onClick={onToggle}
          style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            cursor: onToggle ? 'pointer' : 'default',
            marginBottom: collapsed ? 0 : 'var(--space-3)',
            userSelect: 'none',
          }}
        >
          <div>
            <div style={{ fontSize: 'var(--text-title-3)', fontWeight: 600 }}>
              {title}
            </div>
            {subtitle && (
              <div style={{
                fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)',
                marginTop: 2,
              }}>
                {subtitle}
              </div>
            )}
          </div>
          {onToggle && (
            <span style={{
              transform: collapsed ? 'rotate(-90deg)' : 'rotate(0deg)',
              transition: `transform var(--duration-fast) var(--ease-out)`,
              color: 'var(--text-tertiary)',
            }}>
              <IconChevronDown size={16} />
            </span>
          )}
        </div>
      )}
      {!collapsed && <div>{children}</div>}
    </div>
  )
}
