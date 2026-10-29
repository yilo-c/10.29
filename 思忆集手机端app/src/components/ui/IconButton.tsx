import React, { useState } from 'react'

interface IconButtonProps {
  onClick?: () => void
  children: React.ReactNode
  style?: React.CSSProperties
  disabled?: boolean
  label?: string
}

export function IconButton({ onClick, children, style, disabled = false, label }: IconButtonProps) {
  const [pressed, setPressed] = useState(false)

  return (
    <button
      aria-label={label}
      onClick={disabled ? undefined : onClick}
      onTouchStart={() => { if (!disabled) { setPressed(true); navigator.vibrate?.(8) } }}
      onTouchEnd={() => setPressed(false)}
      onTouchCancel={() => setPressed(false)}
      style={{
        width: 44, height: 44,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        border: 'none', cursor: disabled ? 'not-allowed' : 'pointer',
        borderRadius: 'var(--radius-sm)',
        background: pressed ? 'var(--bg-tertiary)' : 'transparent',
        color: disabled ? 'var(--text-disabled)' : 'var(--text-secondary)',
        transition: `background var(--duration-fast) var(--ease-out)`,
        opacity: disabled ? 0.35 : 1,
        ...style,
      }}
    >
      {children}
    </button>
  )
}
