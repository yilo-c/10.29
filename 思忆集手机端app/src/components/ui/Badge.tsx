
interface BadgeProps {
  priority?: 'high' | 'mid' | 'low'
  label?: string
  dotOnly?: boolean
}

const colors: Record<string, string> = {
  high: 'var(--priority-high)',
  mid: 'var(--priority-mid)',
  low: 'var(--priority-low)',
}

const defaultLabels: Record<string, string> = {
  high: '高',
  mid: '中',
  low: '低',
}

export function Badge({ priority = 'mid', label, dotOnly = false }: BadgeProps) {
  if (dotOnly) {
    return (
      <span style={{
        display: 'inline-block', width: 8, height: 8,
        borderRadius: '50%', background: colors[priority], flexShrink: 0,
      }} />
    )
  }

  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 4,
      fontSize: 'var(--text-caption-2)', fontWeight: 500,
      color: colors[priority],
    }}>
      <span style={{
        display: 'inline-block', width: 6, height: 6,
        borderRadius: '50%', background: colors[priority],
      }} />
      {label || defaultLabels[priority]}
    </span>
  )
}
