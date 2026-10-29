import { useMemo } from 'react'
import { getDayCellLunarInfo } from '@desk-notes/shared'
import { IconSearch } from '../../ui'

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

interface DateHeaderProps {
  onSearchToggle?: () => void
  showSearch?: boolean
}

export default function DateHeader({ onSearchToggle, showSearch }: DateHeaderProps) {
  const info = useMemo(() => {
    const d = new Date()
    const lunar = getDayCellLunarInfo(d)
    return {
      monthDay: `${d.getMonth() + 1}月${d.getDate()}日`,
      weekday: `周${WEEKDAYS[d.getDay()]}`,
      lunarDay: lunar ? `${lunar.lunarDay}` : '',
      jieQi: lunar?.jieQi || '',
      festivals: lunar?.festivals || [],
    }
  }, [])

  const subtitle = [info.lunarDay, info.jieQi, ...info.festivals]
    .filter(Boolean)
    .join(' · ')

  return (
    <div style={{
      padding: '16px 20px 4px',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flex: 1 }}>
          <span style={{
            fontSize: 'var(--text-large-title)',
            fontWeight: 700,
            color: 'var(--text-primary)',
            letterSpacing: -0.5,
          }}>
            {info.monthDay}
          </span>
          <span style={{
            fontSize: 'var(--text-subhead)',
            color: 'var(--text-secondary)',
          }}>
            {info.weekday}
          </span>
        </div>
        {onSearchToggle && (
          <button
            onClick={onSearchToggle}
            aria-label={showSearch ? '关闭搜索' : '搜索'}
            style={{
              background: showSearch ? 'var(--accent-surface)' : 'none',
              border: 'none',
              borderRadius: '50%',
              width: 36,
              height: 36,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              color: showSearch ? 'var(--accent)' : 'var(--text-tertiary)',
              WebkitTapHighlightColor: 'transparent',
              transition: 'all 0.15s ease',
            }}
          >
            <IconSearch size={18} />
          </button>
        )}
      </div>
      {subtitle && (
        <div style={{
          fontSize: 'var(--text-footnote)',
          color: 'var(--text-tertiary)',
          marginTop: 2,
        }}>
          {subtitle}
        </div>
      )}
    </div>
  )
}
