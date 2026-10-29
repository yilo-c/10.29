import React from 'react'

interface BadgeProps {
  priority?: 'high' | 'mid' | 'low'
  label?: string
  dotOnly?: boolean
}

const colors: Record<string, string> = {
  high: '#e57373',
  mid: '#c9a75c',
  low: '#6fb389',
}

export const Badge: React.FC<BadgeProps> = ({
  priority = 'mid',
  label,
  dotOnly = false,
}) => {
  if (dotOnly) {
    return (
      <span
        style={{
          display: 'inline-block',
          width: 8,
          height: 8,
          borderRadius: '50%',
          background: colors[priority],
          flexShrink: 0,
        }}
      />
    )
  }

  const priorityLabels: Record<string, string> = {
    high: '高优先',
    mid: '中优先',
    low: '低优先',
  }

  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        fontSize: 'var(--text-tiny)',
        color: colors[priority],
      }}
    >
      <span
        style={{
          display: 'inline-block',
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: colors[priority],
        }}
      />
      {label || priorityLabels[priority]}
    </span>
  )
}
