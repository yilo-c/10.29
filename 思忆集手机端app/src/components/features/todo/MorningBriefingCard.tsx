import { useState, useMemo } from 'react'
import { useStore, getHuangli, fetchWeather } from '@desk-notes/shared'

const BRIEFING_KEY = 'siyi-briefing-date'

function getTodayStr(): string {
  return new Date().toLocaleDateString('zh-CN')
}

function getWeatherEmoji(code: number): string {
  if (code >= 200 && code < 300) return '⛈️'
  if (code >= 300 && code < 400) return '🌦️'
  if (code >= 500 && code < 600) return '🌧️'
  if (code >= 600 && code < 700) return '❄️'
  if (code >= 700 && code < 800) return '🌫️'
  if (code === 800) return '☀️'
  if (code > 800 && code < 900) return '☁️'
  return '🌡️'
}

export default function MorningBriefingCard() {
  const [dismissed, setDismissed] = useState(false)

  // Only show once per day
  const shouldShow = useMemo(() => {
    if (dismissed) return false
    const last = localStorage.getItem(BRIEFING_KEY)
    const today = getTodayStr()
    if (last === today) return false
    localStorage.setItem(BRIEFING_KEY, today)
    return true
  }, [dismissed])

  const todos = useStore(s => s.todos)
  const userProfile = useStore(s => s.userProfile)
  const [weatherInfo, setWeatherInfo] = useState<{ temp: string; code: number } | null>(null)

  // Load weather once
  useMemo(() => {
    fetchWeather(userProfile?.city).then((w: any) => {
      if (w?.current) setWeatherInfo({ temp: w.current.temp, code: w.current.condition })
    }).catch(() => {})
  }, [userProfile?.city])

  // Almanac
  const almanac = useMemo(() => {
    try {
      return getHuangli(new Date())
    } catch {
      return null
    }
  }, [])

  // Stats
  const stats = useMemo(() => {
    const now = Date.now()
    const todayStart = new Date(now).setHours(0, 0, 0, 0)
    const todayEnd = todayStart + 86400000
    return {
      pending: todos.filter(t => !t.done).length,
      doneToday: todos.filter(t => t.done && t.completedAt && t.completedAt >= todayStart && t.completedAt < todayEnd).length,
      overdue: todos.filter(t => !t.done && t.dueDate && t.dueDate < todayStart).length,
      dueToday: todos.filter(t => !t.done && t.dueDate && t.dueDate >= todayStart && t.dueDate < todayEnd).length,
    }
  }, [todos])

  if (!shouldShow) return null

  const hour = new Date().getHours()
  const greeting = hour < 6 ? '夜深了' : hour < 9 ? '早安' : hour < 12 ? '上午好' : hour < 14 ? '中午好' : hour < 18 ? '下午好' : '晚上好'

  return (
    <div style={{
      margin: '8px 16px 4px',
      padding: '14px 16px',
      borderRadius: 'var(--radius-lg)',
      background: 'linear-gradient(135deg, var(--accent-surface) 0%, var(--bg-secondary) 100%)',
      border: '0.5px solid var(--border-subtle)',
      animation: 'fadeSlideIn 0.3s var(--ease-out)',
      position: 'relative',
    }}>
      {/* Dismiss */}
      <button
        onClick={() => setDismissed(true)}
        aria-label="关闭"
        style={{
          position: 'absolute', top: 6, right: 8,
          background: 'none', border: 'none',
          color: 'var(--text-tertiary)', cursor: 'pointer',
          fontSize: 16, lineHeight: 1, padding: 4,
        }}
      >
        ✕
      </button>

      {/* Greeting */}
      <div style={{ fontSize: 'var(--text-title-3)', fontWeight: 700, marginBottom: 6, color: 'var(--text-primary)' }}>
        {greeting} 👋
      </div>

      {/* Weather + Almanac summary */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
        {weatherInfo && (
          <span style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)' }}>
            {getWeatherEmoji(weatherInfo.code)} {weatherInfo.temp}°C
          </span>
        )}
        {almanac && almanac.yiJi.yi.length > 0 && (
          <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
            宜 {almanac.yiJi.yi.slice(0, 3).join(' · ')}
          </span>
        )}
      </div>

      {/* Stats */}
      <div style={{ display: 'flex', gap: 16, fontSize: 'var(--text-caption-1)' }}>
        <span style={{ color: 'var(--text-secondary)' }}>
          <strong style={{ color: 'var(--accent)' }}>{stats.pending}</strong> 待办
        </span>
        {stats.dueToday > 0 && (
          <span style={{ color: '#FF9F0A' }}>
            <strong>{stats.dueToday}</strong> 今日截止
          </span>
        )}
        {stats.overdue > 0 && (
          <span style={{ color: '#FF3B30' }}>
            <strong>{stats.overdue}</strong> 逾期
          </span>
        )}
        {stats.doneToday > 0 && (
          <span style={{ color: '#34C759' }}>
            已完成 <strong>{stats.doneToday}</strong>
          </span>
        )}
      </div>
    </div>
  )
}
