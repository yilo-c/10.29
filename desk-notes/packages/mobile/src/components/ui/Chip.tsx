import React from 'react'

type ChipType = 'filter' | 'tag' | 'status'

interface ChipProps {
  type?: ChipType
  selected?: boolean
  label: string
  count?: number
  color?: string
  onClick?: () => void
}

const chipBase: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 'var(--space-1)',
  padding: '6px 14px',
  borderRadius: 'var(--radius-sm)',
  fontSize: 'var(--text-small)',
  fontWeight: 500,
  border: 'none',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  transition: `all var(--duration-sm) var(--ease-out)`,
  minHeight: 36,
}

export const Chip: React.FC<ChipProps> = ({
  type = 'filter',
  selected = false,
  label,
  count,
  onClick,
}) => {
  const style: React.CSSProperties = {
    ...chipBase,
  }

  if (type === 'filter') {
    if (selected) {
      style.background = 'var(--gold-surface)'
      style.color = 'var(--gold-primary)'
      style.border = '1px solid var(--gold-border)'
    } else {
      style.background = 'var(--bg-secondary)'
      style.color = 'var(--text-secondary)'
      style.border = '1px solid transparent'
    }
  } else if (type === 'tag') {
    style.background = 'var(--gold-surface)'
    style.color = 'var(--gold-primary)'
    style.fontSize = 'var(--text-tiny)'
    style.padding = '3px 10px'
    style.minHeight = 28
    style.borderRadius = '4px'
  } else if (type === 'status') {
    style.background = 'transparent'
    style.color = 'var(--text-tertiary)'
    style.fontSize = 'var(--text-tiny)'
    style.padding = '2px 8px'
    style.minHeight = 24
  }

  return (
    <button style={style} onClick={onClick}>
      {label}
      {count !== undefined && (
        <span style={{ opacity: 0.6, fontSize: 'var(--text-tiny)' }}>
          {count}
        </span>
      )}
    </button>
  )
}
