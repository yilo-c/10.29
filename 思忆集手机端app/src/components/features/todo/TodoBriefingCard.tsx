import { useState, useMemo, useEffect } from 'react'
import { useStore, fetchWeather, getWeatherEmoji, getCachedWeather, calculateBazi } from '@desk-notes/shared'
import type { WeatherData, BaziProfile } from '@desk-notes/shared'
import { IconChevronDown } from '../../ui'
import { getTcmAdvice } from '../../../lib/tcmEngine'
import type { TcmAdvice } from '../../../lib/tcmEngine'
import { swallowError } from '../../../lib/swallowError'

function getTodayStart(): number {
  const d = new Date()
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

interface TodoBriefingCardProps {
  onQuickAdd: () => void
}

export default function TodoBriefingCard({ onQuickAdd }: TodoBriefingCardProps) {
  const [collapsed, setCollapsed] = useState(false)
  const todos = useStore(s => s.todos)
  const userProfile = useStore(s => s.userProfile)
  const [weather, setWeather] = useState<WeatherData | null>(() => getCachedWeather(userProfile?.city))
  const [weatherLoading, setWeatherLoading] = useState(false)

  useEffect(() => {
    const city = userProfile?.city
    setWeatherLoading(true)
    fetchWeather(city).then((data: WeatherData) => {
      setWeather(data)
      setWeatherLoading(false)
    }).catch(() => setWeatherLoading(false))
  }, [userProfile?.city])

  const bazi = useMemo<BaziProfile | null>(() => {
    if (!userProfile?.birthDate) return null
    try {
      return calculateBazi(new Date(userProfile.birthDate), userProfile.birthHour ?? null, userProfile.gender)
    } catch { return null }
  }, [userProfile])

  const tcmAdvice = useMemo<TcmAdvice[]>(() => {
    if (!weather) return []
    return getTcmAdvice(
      { temp: weather.temp, feelsLike: weather.feelsLike, humidity: weather.humidity, condition: weather.condition },
      new Date(),
      bazi,
    )
  }, [weather, bazi])

  const data = useMemo(() => {
    const todayStart = getTodayStart()
    const todayEnd = todayStart + 86400000

    const todayTodos = todos.filter(t => !t.done && t.dueDate && t.dueDate >= todayStart && t.dueDate < todayEnd)
    const overdueTodos = todos.filter(t => !t.done && t.dueDate && t.dueDate < todayStart)
    const doneToday = todos.filter(t => t.completedAt && t.completedAt >= todayStart && t.completedAt < todayEnd)

    const totalToday = todayTodos.length + doneToday.length
    const completionPct = totalToday > 0 ? Math.round((doneToday.length / totalToday) * 100) : 0

    const active = todos.filter(t => !t.done)
    const highP = active.filter(t => t.priority === 0).length
    const midP = active.filter(t => t.priority === 1).length
    const lowP = active.filter(t => t.priority === undefined || t.priority === 2).length

    let streak = 0
    let checkDate = todayStart
    while (true) {
      const ds = checkDate
      const de = ds + 86400000
      if (todos.some(t => t.completedAt && t.completedAt >= ds && t.completedAt < de)) {
        streak++
        checkDate = ds - 86400000
      } else break
    }

    return {
      todayCount: todayTodos.length,
      overdueCount: overdueTodos.length,
      doneToday: doneToday.length,
      totalToday,
      completionPct,
      highPriority: highP,
      midPriority: midP,
      lowPriority: lowP,
      streak,
    }
  }, [todos])

  const handleShare = async () => {
    const date = new Date()
    const dateStr = `${date.getMonth() + 1}月${date.getDate()}日`
    const weekdays = ['日', '一', '二', '三', '四', '五', '六']
    const weekday = weekdays[date.getDay()]

    let lines = `思忆便签 · 今日简报\n━━━━━━━━━━━━━━━━\n`
    lines += `${dateStr} 周${weekday}\n\n`
    lines += `📌 今日待办 ${data.todayCount} 项`
    if (data.overdueCount > 0) lines += ` | 逾期 ${data.overdueCount} 项`
    lines += `\n📊 完成进度 ${data.doneToday}/${data.totalToday} · ${data.completionPct}%\n`
    if (data.streak > 0) lines += `🔥 连续完成 ${data.streak} 天\n`

    const pending = todos.filter(t => !t.done && t.dueDate && t.dueDate >= getTodayStart() && t.dueDate < getTodayStart() + 86400000)
    if (pending.length > 0) {
      lines += `\n📋 今日待办：\n`
      pending.sort((a, b) => (a.priority ?? 2) - (b.priority ?? 2)).forEach(t => {
        const p = t.priority === 0 ? '高' : t.priority === 1 ? '中' : '低'
        lines += `  ${t.done ? '☑' : '☐'} ${t.text} ${p}\n`
      })
    }

    if (weather) {
      lines += `\n☀️ ${weather.city} ${weather.condition} ${weather.temp}°C\n`
    }
    if (tcmAdvice.length > 0) {
      lines += `💚 ${tcmAdvice[0].summary}\n`
    }

    lines += `\n━━━━━━━━━━━━━━━━\n来自 思忆便签`

    if (navigator.share) {
      try { await navigator.share({ title: '思忆便签 · 今日简报', text: lines }) } catch (e) { /* user cancelled */ swallowError('TodoBriefingCard:121', e) }
    } else {
      await navigator.clipboard.writeText(lines)
    }
  }

  if (collapsed) {
    return (
      <div style={{
        margin: '0 20px 4px',
        borderRadius: 'var(--radius-md)',
        background: 'var(--bg-secondary)',
        flexShrink: 0,
      }}>
        <button
          onClick={() => setCollapsed(false)}
          style={{
            display: 'flex', alignItems: 'center', gap: 8,
            width: '100%', padding: '8px 12px',
            border: 'none', background: 'none', cursor: 'pointer',
            fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)',
          }}
        >
          <span style={{ fontWeight: 500, color: 'var(--accent)' }}>简报</span>
          <span>今日 {data.todayCount + data.doneToday} 项</span>
          {data.overdueCount > 0 && (
            <span style={{ color: 'var(--danger)', fontWeight: 500 }}>
              逾期 {data.overdueCount}
            </span>
          )}
          <div style={{
            flex: 1, height: 6, borderRadius: 3,
            background: 'var(--bg-tertiary)',
            maxWidth: 80, overflow: 'hidden',
          }}>
            <div style={{
              height: '100%', borderRadius: 3,
              width: `${data.completionPct}%`,
              background: data.completionPct === 100 ? 'var(--tone-smooth)' : 'var(--accent)',
              transition: 'width 0.3s ease',
            }} />
          </div>
          <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', minWidth: 30, textAlign: 'right' }}>
            {data.completionPct}%
          </span>
          <span style={{
            display: 'flex', alignItems: 'center',
            color: 'var(--text-tertiary)',
            transition: 'transform 0.2s ease',
          }}>
            <IconChevronDown size={14} />
          </span>
        </button>
      </div>
    )
  }

  return (
    <div style={{
      margin: '0 20px 4px',
      borderRadius: 'var(--radius-md)',
      background: 'var(--bg-secondary)',
      flexShrink: 0,
      overflow: 'hidden',
    }}>
      <button
        onClick={() => setCollapsed(true)}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          width: '100%', padding: '10px 12px 4px',
          border: 'none', background: 'none', cursor: 'pointer',
        }}
      >
        <span style={{
          fontSize: 'var(--text-footnote)', fontWeight: 600,
          color: 'var(--text-primary)',
        }}>
          今日简报
        </span>
        <span style={{
          display: 'flex', alignItems: 'center',
          color: 'var(--text-tertiary)',
          transition: 'transform 0.2s ease',
          transform: 'rotate(180deg)',
        }}>
          <IconChevronDown size={14} />
        </span>
      </button>

      <div style={{ padding: '4px 12px 10px' }}>
        <div style={{ display: 'flex', gap: 16, marginBottom: 8 }}>
          <BriefStat
            label="今日待办"
            value={`${data.todayCount} 项`}
            color="var(--accent)"
          />
          <BriefStat
            label={data.overdueCount > 0 ? '逾期' : '已完成'}
            value={data.overdueCount > 0 ? `${data.overdueCount} 项` : `${data.doneToday} 项`}
            color={data.overdueCount > 0 ? 'var(--danger)' : 'var(--tone-smooth)'}
          />
        </div>

        {data.totalToday > 0 && (
          <div style={{ marginBottom: 8 }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
              marginBottom: 4,
            }}>
              <span>进度</span>
              <span style={{ flex: 1 }} />
              <span>{data.doneToday}/{data.totalToday} &middot; {data.completionPct}%</span>
            </div>
            <div style={{
              height: 6, borderRadius: 3,
              background: 'var(--bg-tertiary)',
              overflow: 'hidden',
            }}>
              <div style={{
                height: '100%', borderRadius: 3,
                width: `${data.completionPct}%`,
                background: data.completionPct === 100 ? 'var(--tone-smooth)' : 'var(--accent)',
                transition: 'width 0.3s ease',
              }} />
            </div>
          </div>
        )}

        {data.streak > 0 && (
          <div style={{
            fontSize: 'var(--text-caption-2)',
            color: 'var(--text-tertiary)',
            marginBottom: 6,
            display: 'flex', alignItems: 'center', gap: 4,
          }}>
            <span style={{ color: 'var(--tone-caution)' }}>连续完成</span>
            <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{data.streak}</span>
            <span>天</span>
          </div>
        )}

        <div style={{
          display: 'flex', gap: 12, alignItems: 'center',
          fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
          marginBottom: 6,
        }}>
          <PriorityDot color="var(--priority-high)" label="高" count={data.highPriority} />
          <PriorityDot color="var(--priority-mid)" label="中" count={data.midPriority} />
          <PriorityDot color="var(--priority-low)" label="低" count={data.lowPriority} />
        </div>

        {/* Weather & TCM */}
        {weather && (
          <div style={{
            marginBottom: 8, padding: '8px 10px',
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-tertiary)',
          }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 6,
              fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)',
            }}>
              <span>{getWeatherEmoji(weather.iconCode)}</span>
              <span style={{ fontWeight: 600 }}>{weather.temp}°C</span>
              <span>{weather.condition}</span>
              <span style={{ color: 'var(--text-tertiary)' }}>{weather.city}</span>
            </div>
            {tcmAdvice.length > 0 && (
              <div style={{
                display: 'flex', flexDirection: 'column', gap: 2,
                marginTop: 4,
                fontSize: 'var(--text-caption-1)',
                color: 'var(--tone-smooth)',
                lineHeight: 1.5,
              }}>
                {tcmAdvice.map((advice, i) => (
                  <span key={i}>{advice.summary}</span>
                ))}
              </div>
            )}
          </div>
        )}

        {weatherLoading && !weather && (
          <div style={{
            marginBottom: 8, height: 20,
            borderRadius: 'var(--radius-sm)',
            background: 'var(--bg-tertiary)',
            opacity: 0.5,
          }} />
        )}

        {/* Quick add + Share */}
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={onQuickAdd}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
              flex: 1, padding: '8px',
              border: '1px dashed var(--border-default)',
              borderRadius: 'var(--radius-sm)',
              background: 'transparent',
              color: 'var(--accent)',
              fontSize: 'var(--text-footnote)', fontWeight: 500,
              cursor: 'pointer',
              transition: 'all 0.15s ease',
            }}
          >
            + 快速添加
          </button>
          {weather && (
            <button
              onClick={handleShare}
              style={{
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
                padding: '8px 12px',
                border: '1px solid var(--border-default)',
                borderRadius: 'var(--radius-sm)',
                background: 'transparent',
                color: 'var(--text-secondary)',
                fontSize: 'var(--text-footnote)', fontWeight: 500,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
            >
              分享
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

function BriefStat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
      <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
        {label}
      </span>
      <span style={{ fontSize: 'var(--text-body)', fontWeight: 600, color }}>
        {value}
      </span>
    </div>
  )
}

function PriorityDot({ color, label, count }: { color: string; label: string; count: number }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <div style={{
        width: 8, height: 8, borderRadius: '50%',
        background: count > 0 ? color : 'var(--text-disabled)',
        opacity: count > 0 ? 1 : 0.3,
      }} />
      <span>{label}</span>
      <span style={{
        fontWeight: 600,
        color: count > 0 ? 'var(--text-primary)' : 'var(--text-disabled)',
      }}>
        {count}
      </span>
    </div>
  )
}
