import React from 'react'

type ChipType = 'filter' | 'tag' | 'status'

interface ChipProps {
  type?: ChipType
  selected?: boolean
  label: string
  count?: number
  onClick?: () => void
}

export function Chip({ type = 'filter', selected = false, label, count, onClick }: ChipProps) {
  const base: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: 'var(--space-1)',
    border: 'none',
    cursor: 'pointer',
    whiteSpace: 'nowrap',
    transition: `all var(--duration-fast) var(--ease-out)`,
  }

  let style: React.CSSProperties = { ...base }

  if (type === 'filter') {
    style.padding = '7px 16px'
    style.borderRadius = 'var(--radius-full)'
    style.fontSize = 'var(--text-footnote)'
    style.fontWeight = 500
    style.minHeight = 36
    if (selected) {
      style.background = 'var(--accent)'
      style.color = '#FFFFFF'
    } else {
      style.background = 'var(--bg-secondary)'
      style.color = 'var(--text-secondary)'
    }
  } else if (type === 'tag') {
    style.padding = '4px 12px'
    style.borderRadius = 'var(--radius-sm)'
    style.fontSize = 'var(--text-caption-1)'
    style.fontWeight = 500
    style.minHeight = 28
    if (selected) {
      style.background = 'var(--accent-surface)'
      style.color = 'var(--accent)'
      style.border = '0.5px solid var(--accent-border)'
    } else {
      style.background = 'var(--bg-tertiary)'
      style.color = 'var(--text-secondary)'
      style.border = '0.5px solid transparent'
    }
  } else {
    style.padding = '2px 8px'
    style.fontSize = 'var(--text-caption-2)'
    style.minHeight = 24
    style.background = 'transparent'
    style.color = 'var(--text-tertiary)'
  }

  return (
    <button style={style} onClick={onClick}>
      {label}
      {count !== undefined && (
        <span style={{ opacity: selected ? 0.7 : 0.5, fontSize: 'var(--text-caption-2)' }}>
          {count}
        </span>
      )}
    </button>
  )
}
