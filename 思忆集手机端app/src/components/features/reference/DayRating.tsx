interface DayRatingProps {
  level: string // 吉/中吉/平和/小凶/凶
  summary: string
}

const RATING_MAP: Record<string, { index: number; label: string }> = {
  '吉': { index: 5, label: '较顺利' },
  '中吉': { index: 4, label: '较顺利' },
  '平和': { index: 3, label: '平和' },
  '小凶': { index: 2, label: '宜谨慎' },
  '凶': { index: 1, label: '宜谨慎' },
}

export default function DayRating({ level, summary }: DayRatingProps) {
  const rating = RATING_MAP[level]
  if (!rating) return null

  return (
    <div style={{
      padding: '10px 0 4px',
      borderBottom: '0.5px solid var(--border-subtle)',
      marginBottom: 8,
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10,
        marginBottom: 6,
      }}>
        {/* 5-segment bar */}
        <div style={{
          flex: 1, display: 'flex', gap: 4, height: 6,
        }}>
          {[1, 2, 3, 4, 5].map(i => (
            <div
              key={i}
              style={{
                flex: 1,
                borderRadius: 3,
                background: i <= rating.index
                  ? i <= 2 ? 'var(--tone-caution)'
                    : i === 3 ? 'var(--tone-neutral)'
                    : 'var(--tone-smooth)'
                  : 'var(--bg-tertiary)',
                transition: 'background 0.3s ease',
              }}
            />
          ))}
        </div>
        <span style={{
          fontSize: 'var(--text-footnote)',
          fontWeight: 600,
          color: rating.index >= 4 ? 'var(--tone-smooth)'
            : rating.index === 3 ? 'var(--tone-neutral)'
            : 'var(--tone-caution)',
          whiteSpace: 'nowrap',
        }}>
          {rating.label}
        </span>
      </div>

      {summary && (
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          lineHeight: 1.5,
        }}>
          {summary}
        </div>
      )}
    </div>
  )
}
