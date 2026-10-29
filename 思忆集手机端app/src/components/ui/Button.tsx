import React from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'destructive'

interface ButtonProps {
  variant?: Variant
  disabled?: boolean
  onClick?: () => void
  children: React.ReactNode
  style?: React.CSSProperties
}

const base: React.CSSProperties = {
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  padding: '12px 24px',
  fontSize: 'var(--text-callout)',
  fontWeight: 600,
  cursor: 'pointer',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 'var(--space-2)',
  minHeight: 44,
  transition: `all var(--duration-fast) var(--ease-out)`,
}

const variants: Record<Variant, React.CSSProperties> = {
  primary: {
    background: 'var(--accent)',
    color: '#FFFFFF',
  },
  secondary: {
    background: 'var(--accent-surface)',
    color: 'var(--accent)',
  },
  ghost: {
    background: 'transparent',
    color: 'var(--text-secondary)',
  },
  destructive: {
    background: 'transparent',
    color: 'var(--danger)',
  },
}

export function Button({
  variant = 'primary',
  disabled = false,
  onClick,
  children,
  style,
}: ButtonProps) {
  const handlePress = (e: React.MouseEvent) => {
    if (disabled) return
    const el = e.currentTarget as HTMLElement
    el.style.transform = 'scale(0.97)'
    navigator.vibrate?.(8)
    setTimeout(() => { el.style.transform = 'scale(1)' }, 120)
    onClick?.()
  }

  return (
    <button
      style={{
        ...base,
        ...variants[variant],
        ...(disabled ? { opacity: 0.35, cursor: 'not-allowed' } : {}),
        ...style,
      }}
      onClick={handlePress}
      disabled={disabled}
    >
      {children}
    </button>
  )
}
