import { useMemo, useState } from 'react'
import { Sheet } from '../../ui'

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

export default function DateHeader() {
  const [showPicker, setShowPicker] = useState(false)

  const dateInfo = useMemo(() => {
    const d = new Date()
    return {
      monthDay: `${d.getMonth() + 1}月${d.getDate()}日`,
      weekday: `周${WEEKDAYS[d.getDay()]}`,
      lunar: '', // Phase 2: real lunar calc
      weatherText: '', // Phase 2: real weather
    }
  }, [])

  return (
    <>
      <button
        onClick={() => setShowPicker(true)}
        style={{
          padding: 'var(--space-4) var(--space-4) 0',
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
          flexShrink: 0,
          border: 'none',
          background: 'none',
          cursor: 'pointer',
          alignItems: 'flex-start',
          width: '100%',
        }}
      >
        {/* Line 1: date + weekday */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 'var(--space-2)' }}>
          <span style={{ fontSize: 'var(--text-h1)', fontWeight: 600, color: 'var(--text-primary)' }}>
            {dateInfo.monthDay}
          </span>
          <span style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)' }}>
            {dateInfo.weekday}
          </span>
        </div>

        {/* Line 2: lunar + weather */}
        {(dateInfo.lunar || dateInfo.weatherText) && (
          <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)' }}>
            {dateInfo.lunar}
            {dateInfo.lunar && dateInfo.weatherText && ' · '}
            {dateInfo.weatherText}
          </div>
        )}
      </button>

      {/* Date picker sheet (Phase 2: real implementation) */}
      <Sheet open={showPicker} onClose={() => setShowPicker(false)} title="选择日期">
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)', textAlign: 'center', padding: 20 }}>
          其他日期的待办查看将在后续版本加入
        </div>
      </Sheet>
    </>
  )
}
