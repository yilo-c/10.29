import React from 'react'

interface DividerProps {
  style?: React.CSSProperties
}

export function Divider({ style }: DividerProps) {
  return (
    <div style={{
      height: 0.5,
      background: 'var(--border-subtle)',
      margin: 'var(--space-3) 0',
      ...style,
    }} />
  )
}
