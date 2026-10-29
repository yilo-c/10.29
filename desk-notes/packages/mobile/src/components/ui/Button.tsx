import React from 'react'

type Variant = 'primary' | 'secondary' | 'ghost' | 'destructive'

interface ButtonProps {
  variant?: Variant
  disabled?: boolean
  onClick?: () => void
  children: React.ReactNode
  style?: React.CSSProperties
  className?: string
}

const btnBase: React.CSSProperties = {
  border: 'none',
  borderRadius: 'var(--radius-sm)',
  padding: '10px 20px',
  fontSize: 'var(--text-body)',
  fontWeight: 500,
  cursor: 'pointer',
  transition: `all var(--duration-sm) var(--ease-out)`,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: 'var(--space-2)',
  minHeight: 44,
}

const variants: Record<Variant, React.CSSProperties> = {
  primary: {
    background: 'var(--gold-primary)',
    color: '#0a0a12',
  },
  secondary: {
    background: 'transparent',
    color: 'var(--gold-primary)',
    border: '1px solid var(--gold-muted)',
  },
  ghost: {
    background: 'transparent',
    color: 'var(--text-secondary)',
  },
  destructive: {
    background: 'transparent',
    color: '#e57373',
  },
}

const disabledStyle: React.CSSProperties = {
  opacity: 0.35,
  cursor: 'not-allowed',
}

export const Button: React.FC<ButtonProps> = ({
  variant = 'primary',
  disabled = false,
  onClick,
  children,
  style,
  className,
}) => {
  const handlePress = (e: React.MouseEvent) => {
    if (disabled) return
    const el = e.currentTarget as HTMLElement
    el.style.transform = 'scale(0.97)'
    setTimeout(() => {
      el.style.transform = 'scale(1)'
    }, 100)
    onClick?.()
  }

  return (
    <button
      className={className}
      style={{
        ...btnBase,
        ...variants[variant],
        ...(disabled ? disabledStyle : {}),
        ...style,
      }}
      onClick={handlePress}
      disabled={disabled}
    >
      {children}
    </button>
  )
}
