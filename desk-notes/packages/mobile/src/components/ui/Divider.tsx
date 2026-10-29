import React from 'react'

interface DividerProps {
  style?: React.CSSProperties
}

export const Divider: React.FC<DividerProps> = ({ style }) => (
  <div
    style={{
      height: 1,
      background: 'var(--border-subtle)',
      margin: 'var(--space-3) 0',
      ...style,
    }}
  />
)
