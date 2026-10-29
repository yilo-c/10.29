import React, { useMemo, useEffect, useState } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { COUNTDOWNS_CHANGED_EVENT, DEFAULT_COUNTDOWN_DATE, dateToInputValue, loadCountdowns, type CountdownItem } from './DashboardTypes'
import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'

/** 目标日期与今天之间的整天天数（按本地时区 0 点对齐，不修改入参）。 */
function getDaysUntil(targetMs: number, nowMs: number): number {
  const startOfDay = (ts: number): number => {
    const d = new Date(ts)
    d.setHours(0, 0, 0, 0)
    return d.getTime()
  }
  return Math.round((startOfDay(targetMs) - startOfDay(nowMs)) / 86400000)
}

export const ExamCountdown: React.FC = () => {
  const { t } = useTranslation()
  const [items, setItems] = useState<CountdownItem[] | null>(loadCountdowns)
  const [now, setNow] = useState<number>(() => Date.now())

  // 每分钟刷新一次，保证跨天/临近时数字准确。
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(timer)
  }, [])

  // 设置面板修改配置后（saveCountdowns 派发事件）同步刷新。
  useEffect(() => {
    const onChanged = () => setItems(loadCountdowns())
    window.addEventListener(COUNTDOWNS_CHANGED_EVENT, onChanged)
    return () => window.removeEventListener(COUNTDOWNS_CHANGED_EVENT, onChanged)
  }, [])

  // 无配置时回退默认单条（兼容现状：26考研倒计时）。
  const configured = !!items && items.length > 0
  const list: CountdownItem[] = configured
    ? items as CountdownItem[]
    : [{ id: 'default', label: t('exam.countdownLabel'), date: DEFAULT_COUNTDOWN_DATE }]

  const active = list
    .map(item => ({ item, days: getDaysUntil(item.date, now) }))
    .filter(({ days }) => days >= 0)

  if (active.length === 0) return null

  return (
    <div className="space-y-2">
      {active.map(({ item, days }) => {
        const urgent = days <= 30
        const soon = days <= 90
        const subtitle = configured
          ? t('dashboardWidgets.countdownTarget', { date: dateToInputValue(item.date) })
          : t('exam.countdown', { days })
        return (
          <div
            key={item.id}
            className="rounded-xl px-4 py-2.5 flex items-center justify-between select-none"
            style={{
              background: urgent
                ? 'linear-gradient(135deg, rgba(239,68,68,0.15), rgba(239,68,68,0.05))'
                : soon
                  ? 'linear-gradient(135deg, rgba(251,191,36,0.12), rgba(251,191,36,0.04))'
                  : 'var(--panel-bg)',
              border: `1px solid ${
                urgent ? 'rgba(239,68,68,0.25)' : soon ? 'rgba(251,191,36,0.2)' : 'var(--panel-border)'
              }`,
            }}
          >
            <div className="flex items-center gap-2.5">
              <div
                className="w-8 h-8 rounded-lg flex items-center justify-center text-sm"
                style={{
                  background: urgent ? 'rgba(239,68,68,0.2)' : soon ? 'rgba(251,191,36,0.2)' : 'rgba(96,165,250,0.12)',
                  color: urgent ? 'var(--danger)' : soon ? '#fbbf24' : 'var(--brand)',
                }}
              >
                <Icon icon="fa-solid fa-calendar-check" />
              </div>
              <div>
                <div className="text-xs font-medium" style={{ color: urgent ? 'var(--danger)' : soon ? '#fbbf24' : 'var(--text-primary)' }}>
                  {item.label}
                </div>
                <div className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                  {subtitle}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span
                className="text-lg font-bold tabular-nums"
                style={{ color: urgent ? 'var(--danger)' : soon ? '#fbbf24' : 'var(--text-primary)' }}
              >
                {days}
              </span>
              <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{t('dashboardWidgets.daysUnit')}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

export const GreetingBar: React.FC = () => {
  const { t } = useTranslation()
  const now = useMemo(() => new Date(), [])
  const hour = now.getHours()
  const greeting =
    hour < 6 ? t('dashboardWidgets.greetLate')
      : hour < 9 ? t('dashboardWidgets.greetMorning')
        : hour < 12 ? t('dashboardWidgets.greetForenoon')
          : hour < 14 ? t('dashboardWidgets.greetNoon')
            : hour < 18 ? t('dashboardWidgets.greetAfternoon')
              : t('dashboardWidgets.greetEvening')

  const dateStr = now.toLocaleDateString('zh-CN', {
    year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
  })

  // B3：天气接 weather.current（后端 _weather.py:52 已注册）。网络/后端不可用时
  // 回退到 "--°"，不阻塞主渲染。
  const [weatherText, setWeatherText] = useState('--°')
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const client = getWsClient()
        if (client.status !== 'connected') return
        const res = await client.call<{ success?: boolean; value?: string }>('weather.current', {})
        if (!cancelled && res?.success && res.value) {
          setWeatherText(String(res.value).replace(/\s*°?\s*$/, '°'))
        }
      } catch (e) { /* ignore */ swallowError('DashboardWidgets:GreetingBar', e) }
    }
    load()
    return () => { cancelled = true }
  }, [])

  return (
    <div
      className="dashboard-hero rounded-xl px-6 py-6 flex items-center justify-between select-none"
    >
      <div className="flex items-center gap-4">
        <div
          className="w-12 h-12 rounded-xl flex items-center justify-center text-lg"
          style={{ background: 'linear-gradient(135deg, rgba(91,156,246,0.28), rgba(139,92,246,0.24))', color: '#8ab8ff' }}
        >
          <Icon icon="fa-regular fa-sun" />
        </div>
        <div>
          <div className="text-display font-bold" style={{ color: 'var(--text-primary)', letterSpacing: '-0.01em' }}>
            {greeting}
          </div>
          <div className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
            {dateStr}
          </div>
        </div>
      </div>
      <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
        <Icon icon="fa-regular fa-cloud text-sm" />
        <span>{weatherText}</span>
      </div>
    </div>
  )
}

export const QuickActionBar: React.FC = () => {
  const { t } = useTranslation()
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const setActiveModule = useStore(s => s.setActiveModule)

  const handleNewNote = () => {
    const id = crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
    addFloatingNote({
      id, type: 'text', title: t('note.newNote'), content: '',
      x: 120 + Math.random() * 60, y: 120 + Math.random() * 60,
      width: 260, height: 200, zIndex: Date.now(),
    })
  }

  const handleNewTask = () => {
    useStore.setState({ addingTodo: true })
  }

  const btnClass =
    'flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-medium transition-all ' +
    'border hover:bg-white/[0.06]'
  const btnStyle = {
    background: 'var(--panel-bg)',
    borderColor: 'var(--panel-border)',
    color: 'var(--text-secondary)',
  } as const

  return (
    <div className="flex items-center gap-2 px-1">
      <button onClick={handleNewNote} className={btnClass} style={btnStyle}>
        <Icon icon="fa-regular fa-pen-to-square text-xs" />
        {t('dashboardWidgets.newNote')}
      </button>
      <button onClick={handleNewTask} className={btnClass} style={btnStyle}>
        <Icon icon="fa-regular fa-plus text-xs" />
        {t('dashboardWidgets.newTask')}
      </button>
      <button className={btnClass} style={btnStyle} onClick={() => setActiveModule('inbox')}>
        <Icon icon="fa-regular fa-inbox text-xs" />
        {t('dashboardWidgets.quickInbox')}
      </button>
    </div>
  )
}
