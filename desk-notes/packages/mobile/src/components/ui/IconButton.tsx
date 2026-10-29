import React from 'react'

interface IconButtonProps {
  onClick?: () => void
  children: React.ReactNode
  style?: React.CSSProperties
  disabled?: boolean
}

export const IconButton: React.FC<IconButtonProps> = ({
  onClick,
  children,
  style,
  disabled = false,
}) => {
  return (
    <button
      style={{
        width: 44,
        height: 44,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        border: 'none',
        background: 'transparent',
        color: 'var(--text-secondary)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        borderRadius: 'var(--radius-sm)',
        transition: `all var(--duration-sm) var(--ease-out)`,
        opacity: disabled ? 0.35 : 1,
        ...style,
      }}
      onClick={disabled ? undefined : onClick}
      onMouseDown={(e) => {
        if (disabled) return
        ;(e.currentTarget as HTMLElement).style.background = 'var(--bg-tertiary)'
      }}
      onMouseUp={(e) => {
        ;(e.currentTarget as HTMLElement).style.background = 'transparent'
      }}
      onMouseLeave={(e) => {
        ;(e.currentTarget as HTMLElement).style.background = 'transparent'
      }}
    >
      {children}
    </button>
  )
}
