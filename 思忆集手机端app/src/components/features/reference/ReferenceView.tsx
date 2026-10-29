import { useState, useMemo, useCallback, useRef } from 'react'
import { useStore, getHuangli, getDayCellLunarInfo, calculateBazi, getDailyGuidance, generateSummaryQuote, getFengshuiAdvice, getAdditionalTips, useCompass, labelToDegrees, angleDiff } from '@desk-notes/shared'
import { IconChevronLeft, IconChevronRight, IconChevronDown, IconCompass } from '../../ui'
import CalendarCell from '../calendar/CalendarCell'
import DayRating from './DayRating'
import WeatherCard from './WeatherCard'
import { getQimenInfo, getShiChen } from '../../../lib/qimen'
import { getMonthDays, isSameDay, WEEKDAY_LABELS } from '../calendar/calendarUtils'
import type { BaziProfile, DailyGuidance } from '@desk-notes/shared'
import { haptic } from '../../../lib/haptic'

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

const LEVEL_COLORS: Record<string, string> = {
  '吉': 'var(--tone-smooth)',
  '中吉': 'var(--tone-smooth)',
  '平和': 'var(--tone-neutral)',
  '小凶': 'var(--tone-caution)',
  '凶': 'var(--danger)',
}

export default function ReferenceView() {
  const todos = useStore(s => s.todos)
  const userProfile = useStore(s => s.userProfile)

  const today = useMemo(() => new Date(), [])
  const [baseDate, setBaseDate] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDate, setSelectedDate] = useState(today)
  const [almanacExpanded, setAlmanacExpanded] = useState(true)

  const touchStartX = useRef(0)
  const touchStartY = useRef(0)
  const swiping = useRef(false)

  const year = baseDate.getFullYear()
  const month = baseDate.getMonth()

  const days = useMemo(() => getMonthDays(year, month), [year, month])

  const lunarInfoMap = useMemo(() => {
    const map = new Map<string, ReturnType<typeof getDayCellLunarInfo>>()
    for (const d of days) {
      map.set(d.toDateString(), getDayCellLunarInfo(d))
    }
    return map
  }, [days])

  const todoCountMap = useMemo(() => {
    const map = new Map<string, number>()
    for (const todo of todos) {
      if (!todo.dueDate) continue
      const td = new Date(todo.dueDate)
      map.set(td.toDateString(), (map.get(td.toDateString()) || 0) + 1)
    }
    return map
  }, [todos])

  const weeks = useMemo(() => {
    const result: Date[][] = []
    for (let i = 0; i < days.length; i += 7) result.push(days.slice(i, i + 7))
    return result
  }, [days])

  // Selected date data
  const selectedLunar = useMemo(() => getDayCellLunarInfo(selectedDate), [selectedDate])
  const selectedHuangli = useMemo(() => getHuangli(selectedDate), [selectedDate])
  const isSelectedToday = useMemo(() => isSameDay(selectedDate, today), [selectedDate, today])
  const currentHour = new Date().getHours()
  const shiChen = getShiChen(currentHour)

  const compass = useCompass()

  const dayTodos = useMemo(() => {
    return todos.filter(t => {
      if (!t.dueDate) return false
      const td = new Date(t.dueDate)
      return isSameDay(td, selectedDate)
    })
  }, [todos, selectedDate])

  // Bazi / guidance
  const hasProfile = !!userProfile?.birthDate
  const bazi = useMemo<BaziProfile | null>(() => {
    if (!hasProfile || !userProfile?.birthDate) return null
    try {
      return calculateBazi(new Date(userProfile.birthDate), userProfile.birthHour ?? null, userProfile.gender)
    } catch { return null }
  }, [hasProfile, userProfile])

  const guidance = useMemo<DailyGuidance | null>(() => {
    return getDailyGuidance(selectedDate, bazi, todos)
  }, [selectedDate, bazi, todos])

  const summary = useMemo(() => {
    if (!guidance) return ''
    const seed = `${selectedDate.getFullYear()}/${selectedDate.getMonth() + 1}/${selectedDate.getDate()}`
    return generateSummaryQuote(guidance, seed)
  }, [guidance, selectedDate])

  const goPrevMonth = useCallback(() => {
    haptic.light()
    setBaseDate(prev => new Date(prev.getFullYear(), prev.getMonth() - 1, 1))
  }, [])

  const goNextMonth = useCallback(() => {
    haptic.light()
    setBaseDate(prev => new Date(prev.getFullYear(), prev.getMonth() + 1, 1))
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

  const isCurrentMonth = (d: Date) => d.getMonth() === month

  const subtitle = [selectedLunar?.lunarDay || '', selectedLunar?.jieQi || '', ...(selectedLunar?.festivals || [])]
    .filter(Boolean)
    .join(' · ')

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          WebkitOverflowScrolling: 'touch',
        }}
      >
        {/* ── Month Header ── */}
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
            padding: '16px 20px 4px',
          }}>
            <span style={{
              fontSize: 'var(--text-large-title)',
              fontWeight: 700,
              letterSpacing: -0.5,
            }}>
              {year}年{month + 1}月
            </span>
            <div style={{ display: 'flex', gap: 4 }}>
              <button
                onClick={goPrevMonth}
                style={{
                  width: 36, height: 36, borderRadius: '50%',
                  background: 'none', border: 'none', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: 'var(--text-secondary)',
                  WebkitTapHighlightColor: 'transparent',
                }}
              >
                <IconChevronLeft size={20} />
              </button>
              <button
                onClick={goNextMonth}
                style={{
                  width: 36, height: 36, borderRadius: '50%',
                  background: 'none', border: 'none', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: 'var(--text-secondary)',
                  WebkitTapHighlightColor: 'transparent',
                }}
              >
                <IconChevronRight size={20} />
              </button>
            </div>
          </div>

          {/* ── Weekday Labels ── */}
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
                color: 'var(--text-secondary)',
                fontWeight: 500,
                paddingBottom: 4,
              }}>
                {label}
              </div>
            ))}
          </div>

          {/* ── Calendar Grid ── */}
          <div style={{ padding: '0 12px 4px', display: 'flex', flexDirection: 'column', gap: 2 }}>
            {weeks.map((week, wi) => (
              <div key={wi} style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 2 }}>
                {week.map(d => {
                  const lunar = lunarInfoMap.get(d.toDateString())
                  return (
                    <CalendarCell
                      key={d.toDateString()}
                      date={d}
                      isCurrentMonth={isCurrentMonth(d)}
                      isSelected={isSameDay(d, selectedDate)}
                      lunarDay={lunar?.lunarDay}
                      jieQi={lunar?.jieQi || ''}
                      todoCount={todoCountMap.get(d.toDateString()) || 0}
                      onClick={() => { haptic.light(); setSelectedDate(d) }}
                    />
                  )
                })}
              </div>
            ))}
          </div>
        </div>

        {/* ── Selected Date Detail ── */}
        <div style={{ padding: '8px 20px 0' }}>
          {/* Date header */}
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{
              fontSize: 'var(--text-title-2)',
              fontWeight: 700,
              color: 'var(--text-primary)',
              letterSpacing: -0.3,
            }}>
              {selectedDate.getMonth() + 1}月{selectedDate.getDate()}日
            </span>
            <span style={{ fontSize: 'var(--text-subhead)', color: 'var(--text-secondary)' }}>
              周{WEEKDAYS[selectedDate.getDay()]}
            </span>
            {!isSelectedToday && (
              <button
                onClick={() => { haptic.light(); setSelectedDate(today); setBaseDate(new Date(today.getFullYear(), today.getMonth(), 1)) }}
                style={{
                  fontSize: 'var(--text-caption-1)', color: 'var(--accent)',
                  background: 'none', border: 'none', cursor: 'pointer',
                  fontWeight: 500, marginLeft: 'auto',
                  WebkitTapHighlightColor: 'transparent',
                }}
              >
                今天
              </button>
            )}
          </div>
          {subtitle && (
            <div style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)', marginTop: 2, marginBottom: 8 }}>
              {subtitle}
            </div>
          )}

          {/* ── Full Almanac ── */}
          {selectedHuangli && (
            <div style={{
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-secondary)',
              overflow: 'hidden',
              marginBottom: 10,
            }}>
              <button
                onClick={() => { haptic.light(); setAlmanacExpanded(v => !v) }}
                style={{
                  display: 'flex', alignItems: 'center', width: '100%',
                  padding: '10px 14px 0', border: 'none', background: 'none',
                  cursor: 'pointer', gap: 8,
                  color: 'var(--text-secondary)', fontSize: 'var(--text-footnote)',
                }}
              >
                <span style={{ fontSize: 'var(--text-headline)', fontWeight: 600, color: 'var(--text-primary)' }}>
                  黄历
                </span>
                <span style={{ flex: 1 }} />
                <span style={{
                  color: 'var(--tone-smooth)', fontWeight: 500,
                }}>
                  {selectedHuangli.jianChuLuck}
                </span>
                <span style={{
                  display: 'flex', alignItems: 'center',
                  color: 'var(--text-tertiary)',
                  transition: 'transform 0.2s ease',
                  transform: almanacExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                }}>
                  <IconChevronDown size={14} />
                </span>
              </button>

              {guidance?.advice && (
                <div style={{ padding: '0 14px' }}>
                  <DayRating level={guidance.advice.level} summary={summary} />
                </div>
              )}

              {almanacExpanded && (
                <div style={{
                  padding: '0 14px 12px',
                  fontSize: 'var(--text-footnote)',
                  color: 'var(--text-secondary)',
                  display: 'flex', flexDirection: 'column', gap: 6,
                  animation: 'fadeInUp 0.15s ease',
                }}>
                  <div>
                    <span style={{ color: 'var(--tone-smooth)', fontWeight: 500 }}>宜 </span>
                    {selectedHuangli.yiJi.yi.join(' · ')}
                  </div>
                  <div>
                    <span style={{ color: 'var(--tone-caution)', fontWeight: 500 }}>忌 </span>
                    {selectedHuangli.yiJi.ji.join(' · ')}
                  </div>
                  <div style={{
                    display: 'flex', flexWrap: 'wrap', gap: '2px 12px',
                    fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)',
                    marginTop: 2,
                  }}>
                    <span>冲{selectedHuangli.chongSha.chong} · 煞{selectedHuangli.chongSha.sha}</span>
                    <span>喜神{selectedHuangli.fangwei.xi} · 财神{selectedHuangli.fangwei.cai} · 福神{selectedHuangli.fangwei.fu}</span>
                    <span>{selectedHuangli.xiu.name}宿（{selectedHuangli.xiu.gong}）· {selectedHuangli.xiu.luck}</span>
                    <span>建除{selectedHuangli.jianChu} · {selectedHuangli.jianChuLuck}</span>
                    <span>{selectedHuangli.ganzhi.year}年 · {selectedHuangli.ganzhi.month}月 · {selectedHuangli.ganzhi.day}日</span>
                  </div>
                  {selectedHuangli.pengZu && (
                    <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)', marginTop: 2 }}>
                      {selectedHuangli.pengZu}
                    </div>
                  )}
                  {selectedHuangli.naYin && (
                    <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)' }}>
                      纳音：{selectedHuangli.naYin}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <WeatherCard />

          {/* ── Bazi / Guidance ── */}
          <div style={{
            borderRadius: 'var(--radius-md)',
            background: 'var(--bg-secondary)',
            padding: '12px 14px',
            marginBottom: 10,
          }}>
            <div style={{
              fontSize: 'var(--text-headline)', fontWeight: 600, color: 'var(--text-primary)',
              marginBottom: 8,
            }}>
              命理参考
            </div>
            {bazi ? (
              <div style={{
                fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)',
                lineHeight: 1.7, display: 'flex', flexDirection: 'column', gap: 4,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                  日主 <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{bazi.dayMaster}</span>
                  · 身{bazi.strength}
                  {guidance?.advice && (
                    <span style={{
                      fontSize: 'var(--text-caption-1)',
                      color: LEVEL_COLORS[guidance.advice.level] || 'var(--tone-neutral)',
                      fontWeight: 600,
                      padding: '1px 8px',
                      borderRadius: 'var(--radius-full)',
                      background: `${LEVEL_COLORS[guidance.advice.level]}15`,
                    }}>
                      {guidance.advice.level}
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>
                  四柱：{bazi.fourPillars.year} {bazi.fourPillars.month} {bazi.fourPillars.day}
                  {bazi.fourPillars.hour ? ` ${bazi.fourPillars.hour}` : ''}
                </div>
                {bazi.missingElement && (
                  <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>
                    五行缺{bazi.missingElement}
                  </div>
                )}
                {bazi.yongShen.length > 0 && (
                  <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>
                    用神：{bazi.yongShen.join('、')}
                  </div>
                )}
                {guidance?.advice && (
                  <>
                    <div style={{
                      marginTop: 4, padding: '8px 10px',
                      borderRadius: 'var(--radius-sm)', background: 'var(--bg-tertiary)',
                      fontSize: 'var(--text-caption-1)', color: 'var(--text-secondary)',
                      lineHeight: 1.6,
                    }}>
                      {guidance.advice.summary}
                    </div>
                    {guidance.advice.luckyDirection && (
                      <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>
                        幸运方向：{guidance.advice.luckyDirection}
                      </div>
                    )}
                    {guidance.advice.clothingAdvice && (
                      <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>
                        {guidance.advice.clothingAdvice}
                      </div>
                    )}
                    {guidance.advice.suitable.length > 0 && (
                      <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--tone-smooth)' }}>
                        宜：{guidance.advice.suitable.join(' · ')}
                      </div>
                    )}
                    {guidance.advice.avoid.length > 0 && (
                      <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--tone-caution)' }}>
                        忌：{guidance.advice.avoid.join(' · ')}
                      </div>
                    )}
                  </>
                )}
                <div style={{ fontSize: 10, color: 'var(--text-disabled)', marginTop: 2 }}>
                  依据传统子平八字理论，仅供决策参考
                </div>
              </div>
            ) : (
              <div style={{
                fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)',
                lineHeight: 1.6,
              }}>
                在 设置 中填写出生信息以查看八字命理分析
              </div>
            )}
          </div>

          {/* ── 风水 ── */}
          {guidance?.advice?.luckyDirection && (
            <div style={{
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-secondary)',
              padding: '12px 14px',
              marginBottom: 10,
            }}>
              <div style={{
                fontSize: 'var(--text-headline)', fontWeight: 600, color: 'var(--text-primary)',
                marginBottom: 6,
              }}>
                风水 · {guidance.advice.luckyDirection}
              </div>
              <FengshuiSection
                direction={guidance.advice.luckyDirection}
                compass={compass}
              />
            </div>
          )}

          {/* ── 当前时辰 ── */}
          <div style={{
            borderRadius: 'var(--radius-md)',
            background: 'var(--bg-secondary)',
            padding: '12px 14px',
            marginBottom: 10,
          }}>
            <div style={{
              fontSize: 'var(--text-headline)', fontWeight: 600, color: 'var(--text-primary)',
              marginBottom: 4,
            }}>
              时辰
            </div>
            <div style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
              <div>当前时辰：{shiChen.name}时（{shiChen.range}）</div>
              {selectedHuangli && (
                <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>
                  时柱：{selectedHuangli.ganzhi.time}
                </div>
              )}
              {(() => {
                const qi = getQimenInfo(currentHour)
                if (!qi) return null
                return (
                  <div style={{
                    marginTop: 8, padding: '8px 10px',
                    borderRadius: 'var(--radius-sm)',
                    background: 'var(--bg-tertiary)',
                  }}>
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                      <span style={{ color: 'var(--tone-smooth)', fontWeight: 500 }}>{qi.star}</span>
                      <span style={{ color: 'var(--accent)', fontWeight: 500 }}>{qi.door}</span>
                      <span style={{ color: 'var(--tone-caution)', fontWeight: 500 }}>{qi.palace}</span>
                    </div>
                    <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', lineHeight: 1.5 }}>
                      {qi.advice}
                    </div>
                  </div>
                )
              })()}
              <div style={{ fontSize: 10, color: 'var(--text-disabled)', marginTop: 2 }}>
                依据传统时辰理论，仅供参考
              </div>
            </div>
          </div>

          {/* ── Day's Todos ── */}
          {dayTodos.length > 0 && (
            <div style={{
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-secondary)',
              padding: '12px 14px',
              marginBottom: 10,
            }}>
              <div style={{
                fontSize: 'var(--text-headline)', fontWeight: 600, color: 'var(--text-primary)',
                marginBottom: 6,
              }}>
                该日待办 ({dayTodos.filter(t => !t.done).length}/{dayTodos.length})
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {dayTodos.map(todo => (
                  <div key={todo.id} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)',
                  }}>
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%',
                      background: todo.done ? 'var(--tone-neutral)' : 'var(--accent)',
                      flexShrink: 0, opacity: todo.done ? 0.4 : 1,
                    }} />
                    <span style={{
                      textDecoration: todo.done ? 'line-through' : 'none',
                      opacity: todo.done ? 0.5 : 1,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {todo.text}
                    </span>
                    {todo.priority !== undefined && (
                      <span style={{
                        fontSize: 9, flexShrink: 0,
                        color: todo.priority === 0 ? 'var(--priority-high)' :
                               todo.priority === 1 ? 'var(--priority-mid)' : 'var(--priority-low)',
                      }}>
                        P{todo.priority}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {dayTodos.length === 0 && (
            <div style={{
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-secondary)',
              padding: '12px 14px',
              marginBottom: 10,
              fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)',
              textAlign: 'center',
            }}>
              该日没有待办事项
            </div>
          )}

          {/* ── Footer ── */}
          <div style={{
            fontSize: 10, color: 'var(--text-disabled)',
            textAlign: 'center', padding: '8px 0 40px',
          }}>
            依据传统历法理论整理，仅供做决策时参考
          </div>
        </div>
      </div>
    </div>
  )
}

function FengshuiSection({ direction, compass }: { direction: string; compass: ReturnType<typeof useCompass> }) {
  const advice = useMemo(() => getFengshuiAdvice('life', direction), [direction])
  const tips = useMemo(() => getAdditionalTips(direction), [direction])

  if (!advice) return null

  const showCompass = compass.permission === 'granted' && compass.heading !== null
  const diff = compass.heading !== null ? angleDiff(compass.heading, labelToDegrees(direction)) : null

  return (
    <div style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
      {/* ── Compass Row ── */}
      {(() => {
        // Determine which state to show
        if (compass.permission === 'unsupported') {
          return null  // device truly can't support compass
        }
        if (showCompass) {
          // heading data available — show rotating compass
          return (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              marginBottom: 10, padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              background: diff !== null && Math.abs(diff) < 30 ? 'rgba(52,199,89,0.1)' : 'var(--bg-glass)',
            }}>
              <span style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                transform: `rotate(${compass.heading || 0}deg)`,
                color: diff !== null && Math.abs(diff) < 30 ? 'var(--tone-smooth)' : 'var(--text-secondary)',
                transition: 'transform 0.3s ease',
                flexShrink: 0,
              }}>
                <IconCompass size={20} />
              </span>
              <span style={{ flex: 1, fontSize: 'var(--text-caption-1)' }}>
                当前 <strong>{compass.headingLabel}</strong>
                {diff !== null && (
                  Math.abs(diff) < 22.5
                    ? <span style={{ color: 'var(--tone-smooth)' }}> · 正朝吉方 ✓</span>
                    : Math.abs(diff) < 90
                      ? <span style={{ color: 'var(--tone-caution)' }}> · 偏{Math.round(Math.abs(diff))}°</span>
                      : <span style={{ color: 'var(--text-tertiary)' }}> · 背对吉方</span>
                )}
              </span>
            </div>
          )
        }
        if (compass.permission === 'prompt') {
          return (
            <button
              onClick={() => compass.requestPermission()}
              style={{
                display: 'flex', alignItems: 'center', gap: 6, width: '100%',
                marginBottom: 10, padding: '8px 10px',
                borderRadius: 'var(--radius-sm)', border: '0.5px solid var(--accent-border)',
                background: 'var(--accent-surface)', cursor: 'pointer',
                color: 'var(--accent)', fontSize: 'var(--text-caption-1)',
              }}
            >
              <IconCompass size={16} />
              开启罗盘查看当前朝向
            </button>
          )
        }
        if (compass.error) {
          // granted but sensor never fired (desktop / no magnetometer)
          return (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              marginBottom: 10, padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-glass)', opacity: 0.6,
            }}>
              <IconCompass size={16} />
              <span style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>
                当前设备不支持指南针功能
              </span>
            </div>
          )
        }
        if (compass.permission === 'denied') {
          return (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              marginBottom: 10, padding: '8px 10px',
              borderRadius: 'var(--radius-sm)',
              background: 'var(--bg-glass)', opacity: 0.7,
            }}>
              <IconCompass size={16} />
              <span style={{ fontSize: 'var(--text-caption-1)', color: 'var(--tone-caution)' }}>
                罗盘权限已关闭，请在系统设置中允许
              </span>
            </div>
          )
        }
        // granted but heading not yet received (initializing)
        return (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            marginBottom: 10, padding: '8px 10px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-glass)',
          }}>
            <span style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              animation: 'spin 1s linear infinite',
              color: 'var(--text-tertiary)', flexShrink: 0,
            }}>
              <IconCompass size={18} />
            </span>
            <span style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)' }}>
              校准罗盘中…
            </span>
          </div>
        )
      })()}

      <div style={{ marginBottom: 6 }}>
        <span style={{ color: 'var(--accent)', fontWeight: 500 }}>办公布局</span>
        <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginTop: 2 }}>
          {advice.deskOrientation}
        </div>
      </div>
      <div style={{ marginBottom: 6 }}>
        <span style={{ color: 'var(--tone-smooth)', fontWeight: 500 }}>催旺建议</span>
        <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginTop: 2 }}>
          {advice.enhancement}
        </div>
      </div>
      <div style={{ marginBottom: 6 }}>
        <span style={{ color: 'var(--tone-caution)', fontWeight: 500 }}>避忌</span>
        <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginTop: 2 }}>
          {advice.avoid}
        </div>
      </div>
      {advice.extra && (
        <div style={{ marginBottom: 6 }}>
          <span style={{ color: 'var(--tone-neutral)', fontWeight: 500 }}>额外提示</span>
          <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginTop: 2 }}>
            {advice.extra}
          </div>
        </div>
      )}
      <div style={{ borderTop: '0.5px solid var(--border-subtle)', margin: '6px 0' }} />
      <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
        {tips.map((t: string, i: number) => <div key={i}>{t}</div>)}
      </div>
    </div>
  )
}
