import { isToday } from './calendarUtils'

interface CalendarCellProps {
  date: Date
  isCurrentMonth: boolean
  isSelected: boolean
  lunarDay?: string
  jieQi?: string
  todoCount: number
  onClick: () => void
}

export default function CalendarCell({
  date,
  isCurrentMonth,
  isSelected,
  lunarDay,
  jieQi,
  todoCount,
  onClick,
}: CalendarCellProps) {
  const today = isToday(date)
  const dayNum = date.getDate()

  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: 52,
        padding: '2px 0',
        background: isSelected
          ? 'var(--accent)'
          : today
            ? 'var(--accent-surface)'
            : 'none',
        borderRadius: 'var(--radius-sm)',
        border: today && !isSelected ? '1px solid var(--accent-border)' : 'none',
        cursor: 'pointer',
        opacity: isCurrentMonth ? 1 : 0.25,
        WebkitTapHighlightColor: 'transparent',
        transition: 'background 0.15s ease, opacity 0.15s ease',
      }}
    >
      <span style={{
        fontSize: 'var(--text-callout)',
        fontWeight: isSelected ? 600 : today ? 600 : 400,
        color: isSelected
          ? '#FFFFFF'
          : today
            ? 'var(--accent)'
            : 'var(--text-primary)',
        lineHeight: 1.2,
      }}>
        {dayNum}
      </span>
      <span style={{
        fontSize: 9,
        color: isSelected ? 'rgba(255,255,255,0.8)' : 'var(--text-tertiary)',
        lineHeight: 1,
        marginTop: 1,
        overflow: 'hidden',
        textOverflow: 'ellipsis',
        whiteSpace: 'nowrap',
        maxWidth: '100%',
        padding: '0 2px',
      }}>
        {jieQi || lunarDay || ''}
      </span>
      {todoCount > 0 && (
        <div style={{
          display: 'flex',
          gap: 3,
          marginTop: 2,
        }}>
          {Array.from({ length: Math.min(todoCount, 3) }).map((_, i) => (
            <div key={i} style={{
              width: 4,
              height: 4,
              borderRadius: '50%',
              background: isSelected ? 'rgba(255,255,255,0.9)' : 'var(--accent)',
            }} />
          ))}
        </div>
      )}
    </button>
  )
}
