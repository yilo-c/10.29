import { useState, useMemo, useCallback, useRef } from 'react'
import { useStore } from '@desk-notes/shared'
import { IconChevronLeft, IconChevronRight, IconChevronDown } from '../../ui'
import CalendarCell from './CalendarCell'
import CalendarDayDetail from './CalendarDayDetail'
import { getMonthDays, getDayLunarInfo, isSameDay, WEEKDAY_LABELS } from './calendarUtils'

export default function CalendarView() {
  const todos = useStore(s => s.todos)
  const today = useMemo(() => new Date(), [])
  const [baseDate, setBaseDate] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDate, setSelectedDate] = useState(today)
  const [expanded, setExpanded] = useState(false)
  const touchStartX = useRef(0)
  const touchStartY = useRef(0)
  const swiping = useRef(false)

  const year = baseDate.getFullYear()
  const month = baseDate.getMonth()

  const days = useMemo(() => getMonthDays(year, month), [year, month])

  const dayLunarMap = useMemo(() => {
    const map = new Map<string, ReturnType<typeof getDayLunarInfo>>()
    for (const d of days) {
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
      map.set(key, getDayLunarInfo(d))
    }
    return map
  }, [days])

  const todoCountMap = useMemo(() => {
    const map = new Map<string, number>()
    for (const todo of todos) {
      if (!todo.dueDate) continue
      const td = new Date(todo.dueDate)
      const key = `${td.getFullYear()}-${td.getMonth()}-${td.getDate()}`
      map.set(key, (map.get(key) || 0) + 1)
    }
    return map
  }, [todos])

  const weeks = useMemo(() => {
    const result: Date[][] = []
    for (let i = 0; i < days.length; i += 7) {
      result.push(days.slice(i, i + 7))
    }
    return result
  }, [days])

  const getLunarInfo = useCallback((d: Date) => {
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
    return dayLunarMap.get(key) || null
  }, [dayLunarMap])

  const getTodoCount = useCallback((d: Date) => {
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
    return todoCountMap.get(key) || 0
  }, [todoCountMap])

  const goPrevMonth = useCallback(() => {
    navigator.vibrate?.(6)
    setBaseDate(prev => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))
  }, [])

  const goNextMonth = useCallback(() => {
    navigator.vibrate?.(6)
    setBaseDate(prev => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))
  }, [])

  const goToday = useCallback(() => {
    navigator.vibrate?.(6)
    const now = new Date()
    setBaseDate(new Date(now.getFullYear(), now.getMonth(), 1))
    setSelectedDate(now)
  }, [])

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartX.current = e.touches[0].clientX
    touchStartY.current = e.touches[0].clientY
    swiping.current = false
  }, [])

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    const dx = e.touches[0].clientX - touchStartX.current
    const dy = e.touches[0].clientY - touchStartY.current
    if (Math.abs(dx) > 10 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      swiping.current = true
    }
  }, [])

  const onTouchEnd = useCallback((e: React.TouchEvent) => {
    if (!swiping.current) return
    const dx = e.changedTouches[0].clientX - touchStartX.current
    if (Math.abs(dx) > 40) {
      if (dx < 0) goNextMonth()
      else goPrevMonth()
    }
    swiping.current = false
  }, [goNextMonth, goPrevMonth])

  const isCurrentMonth = useCallback((d: Date) => {
    return d.getMonth() === month
  }, [month])

  const monthLabel = `${year}年${month + 1}月`

  return (
    <div style={{ flexShrink: 0 }}>
      <div
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{ userSelect: 'none' }}
      >
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 20px 4px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <button
              onClick={() => setExpanded(v => !v)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--text-primary)',
                WebkitTapHighlightColor: 'transparent',
              }}
            >
              <span style={{
                fontSize: expanded ? 'var(--text-title-3)' : 'var(--text-large-title)',
                fontWeight: 700,
                letterSpacing: -0.5,
                transition: 'font-size 0.2s ease',
              }}>
                {monthLabel}
              </span>
              <span style={{
                display: 'flex',
                alignItems: 'center',
                color: 'var(--text-tertiary)',
                transition: 'transform 0.2s ease',
                transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
              }}>
                <IconChevronDown size={16} />
              </span>
            </button>
            {!expanded && !isSameDay(selectedDate, today) && (
              <button
                onClick={goToday}
                style={{
                  fontSize: 'var(--text-caption-1)',
                  color: 'var(--accent)',
                  background: 'var(--accent-surface)',
                  border: 'none',
                  borderRadius: 'var(--radius-full)',
                  padding: '4px 10px',
                  cursor: 'pointer',
                  fontWeight: 500,
                  WebkitTapHighlightColor: 'transparent',
                }}
              >
                回到今天
              </button>
            )}
          </div>

          <div style={{ display: 'flex', gap: 4 }}>
            <button
              onClick={goPrevMonth}
              style={{
                width: 36,
                height: 36,
                borderRadius: '50%',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--text-secondary)',
                WebkitTapHighlightColor: 'transparent',
              }}
            >
              <IconChevronLeft size={20} />
            </button>
            <button
              onClick={goNextMonth}
              style={{
                width: 36,
                height: 36,
                borderRadius: '50%',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                color: 'var(--text-secondary)',
                WebkitTapHighlightColor: 'transparent',
              }}
            >
              <IconChevronRight size={20} />
            </button>
          </div>
        </div>

        {expanded && (
          <div style={{
            animation: 'fadeInUp 0.15s ease',
          }}>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              padding: '8px 12px 0',
              gap: 2,
            }}>
              {WEEKDAY_LABELS.map(label => (
                <div key={label} style={{
                  textAlign: 'center',
                  fontSize: 'var(--text-caption-2)',
                  color: 'var(--text-tertiary)',
                  fontWeight: 500,
                  paddingBottom: 4,
                }}>
                  {label}
                </div>
              ))}
            </div>

            <div style={{
              padding: '0 12px 8px',
              display: 'flex',
              flexDirection: 'column',
              gap: 2,
            }}>
              {weeks.map((week, wi) => (
                <div key={wi} style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(7, 1fr)',
                  gap: 2,
                }}>
                  {week.map(d => {
                    const lunar = getLunarInfo(d)
                    return (
                      <CalendarCell
                        key={`${d.getMonth()}-${d.getDate()}`}
                        date={d}
                        isCurrentMonth={isCurrentMonth(d)}
                        isSelected={isSameDay(d, selectedDate)}
                        lunarDay={lunar?.lunarDay}
                        jieQi={lunar?.jieQi}
                        todoCount={getTodoCount(d)}
                        onClick={() => setSelectedDate(d)}
                      />
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <CalendarDayDetail
        date={selectedDate}
        todos={todos}
      />
    </div>
  )
}
