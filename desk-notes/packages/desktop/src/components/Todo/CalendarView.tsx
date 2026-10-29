import React, { useState, useMemo, useCallback, useEffect, useRef, Suspense } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { TodoItem, type QimenEvent, type QimenDomain } from '../../types'
import { useTranslation } from '../../i18n'
import { getHuangli, getDayCellLunarInfo, calculateBazi, getDailyGuidance, type HuangliData, type BaziProfile, type DailyGuidance } from '../../utils/astrology'
import { getAIGuidance } from '../../utils/astrology/ai-guidance'
import { getLocalAIConfig } from '../../utils/aiConfig'
import { fetchWeather, type WeatherData } from '../../utils/astrology/weather'
import BaziSettings from './BaziSettings'
import { suggestQimenEvent, getDomainLabel } from '../../utils/astrology/qimenSuggest'
import { isSameDay, getMonthDays } from './calendarUtils'
import WeekView from './WeekView'
import HuangliPanel from './HuangliPanel'
import { swallowError } from '../../utils/swallowError'
const GuidanceDashboard = React.lazy(() => import('./GuidanceDashboard'))

/** Max festival/tag labels to show in a day cell before truncating */
const MAX_CELL_TAGS = 2

const CalendarView: React.FC = () => {
  const todos = useStore(s => s.todos)
  const setDueDate = useStore(s => s.setDueDate)
  const activeCat = useStore(s => s.activeCat)
  const toggleTodo = useStore(s => s.toggleTodo)
  const deleteTodo = useStore(s => s.deleteTodo)
  const addEvent = useStore(s => s.addEvent)
  const { t, tArray } = useTranslation()
  const weekdays = tArray('calendar.weekdays')
  const months = tArray('calendar.months')
  const today = new Date()
  const [viewMode, setViewMode] = useState<'month' | 'week'>('month')
  const [viewDate, setViewDate] = useState(new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDate, setSelectedDate] = useState<Date | null>(today)
  const [dragTodoId, setDragTodoId] = useState<string | null>(null)
  const [inlinePrompt, setInlinePrompt] = useState<{ date: Date; value: string } | null>(null)
  const inlineInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (inlinePrompt) {
      inlineInputRef.current?.focus()
    }
  }, [inlinePrompt])

  const year = viewDate.getFullYear()
  const month = viewDate.getMonth()

  const days = useMemo(() => getMonthDays(year, month), [year, month])

  // Pre-compute lunar info for all visible days (one map lookup per cell)
  const lunarInfoMap = useMemo(() => {
    const map = new Map<string, ReturnType<typeof getDayCellLunarInfo>>()
    for (const day of days) {
      try { map.set(day.toDateString(), getDayCellLunarInfo(day)) } catch (e) { /* ignore */ swallowError('CalendarView:51', e) }
    }
    return map
  }, [days])

  // Full huangli data for selected date (null if lunar library unavailable)
  const selectedHuangli: HuangliData | null = useMemo(() => {
    if (!selectedDate) return null
    try { return getHuangli(selectedDate) } catch { return null }
  }, [selectedDate])

  // Group todos by date
  const todosByDate = useMemo(() => {
    const map = new Map<string, TodoItem[]>()
    const list = activeCat === 'all' ? todos : todos.filter(t => t.category === activeCat)
    for (const t of list) {
      if (!t.dueDate) continue
      const key = new Date(t.dueDate).toDateString()
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(t)
    }
    return map
  }, [todos, activeCat])

  const selectedTodos = useMemo(() => {
    if (!selectedDate) return []
    const key = selectedDate.toDateString()
    return todosByDate.get(key) || []
  }, [selectedDate, todosByDate])

  const selectedReminders = useMemo(() => {
    if (!selectedDate) return []
    return todos.filter(t => {
      if (!t.reminderTime) return false
      const d = new Date(t.reminderTime)
      return d.getFullYear() === selectedDate.getFullYear()
        && d.getMonth() === selectedDate.getMonth()
        && d.getDate() === selectedDate.getDate()
    })
  }, [selectedDate, todos])

  const prevMonth = useCallback(() => {
    if (viewMode === 'week' && selectedDate) {
      const d = new Date(selectedDate)
      d.setDate(d.getDate() - 7)
      setSelectedDate(d)
    } else {
      setViewDate(new Date(year, month - 1, 1))
    }
  }, [year, month, viewMode, selectedDate])

  const nextMonth = useCallback(() => {
    if (viewMode === 'week' && selectedDate) {
      const d = new Date(selectedDate)
      d.setDate(d.getDate() + 7)
      setSelectedDate(d)
    } else {
      setViewDate(new Date(year, month + 1, 1))
    }
  }, [year, month, viewMode, selectedDate])

  const handleDayClick = useCallback((day: Date) => {
    setSelectedDate(day)
  }, [])

  const handleDayDoubleClick = useCallback((day: Date) => {
    setInlinePrompt({ date: day, value: '' })
    setTimeout(() => inlineInputRef.current?.focus(), 50)
  }, [addEvent])

  const confirmInlineEvent = useCallback(() => {
    if (!inlinePrompt) return
    const title = inlinePrompt.value.trim()
    if (title) {
      addEvent(title, inlinePrompt.date.getTime())
    }
    setInlinePrompt(null)
  }, [inlinePrompt, addEvent])

  const handleDragStart = useCallback((e: React.DragEvent, todoId: string) => {
    const dt = e.dataTransfer
    if (!dt) return
    dt.setData('text/plain', todoId)
    dt.effectAllowed = 'move'
    setDragTodoId(todoId)
  }, [])

  // ── Keyboard navigation ──
  const dayCellRefs = useRef<Map<string, HTMLDivElement>>(new Map())

  const handleDayKeyDown = useCallback((e: React.KeyboardEvent, day: Date) => {
    let newDate: Date | null = null
    switch (e.key) {
      case 'ArrowLeft':  newDate = new Date(day); newDate.setDate(newDate.getDate() - 1); break
      case 'ArrowRight': newDate = new Date(day); newDate.setDate(newDate.getDate() + 1); break
      case 'ArrowUp':    newDate = new Date(day); newDate.setDate(newDate.getDate() - 7); break
      case 'ArrowDown':  newDate = new Date(day); newDate.setDate(newDate.getDate() + 7); break
      case 'Enter':
      case ' ':
        e.preventDefault()
        setSelectedDate(day)
        return
      default:
        return
    }
    e.preventDefault()
    if (newDate) {
      setSelectedDate(newDate)
      const vd = viewDate
      if (newDate.getMonth() !== vd.getMonth() || newDate.getFullYear() !== vd.getFullYear()) {
        setViewDate(new Date(newDate.getFullYear(), newDate.getMonth(), 1))
      }
      requestAnimationFrame(() => {
        dayCellRefs.current.get(newDate!.toDateString())?.focus()
      })
    }
  }, [setSelectedDate, setViewDate, viewDate])

  const handleDayDrop = useCallback((e: React.DragEvent, day: Date) => {
    e.preventDefault()
    e.stopPropagation()
    const dt = e.dataTransfer
    if (!dt) return
    const todoId = dt.getData('text/plain')
    if (todoId) {
      setDueDate(todoId, day.getTime())
    }
    setDragTodoId(null)
  }, [setDueDate])

  const handleDayDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
  }, [])

  const removeDueDate = useCallback((todoId: string) => {
    setDueDate(todoId, undefined)
  }, [setDueDate])

  const isCurrentMonth = (day: Date) => day.getMonth() === month

  // Subtask count per todo
  const childCounts = useMemo(() => {
    const map = new Map<string, number>()
    for (const t of todos) {
      if (t.parentId) map.set(t.parentId, (map.get(t.parentId) || 0) + 1)
    }
    return map
  }, [todos])

  // Count todos per day
  const todoCounts = useMemo(() => {
    const map = new Map<string, number>()
    for (const [key, items] of todosByDate) {
      const active = items.filter(t => !t.done)
      if (active.length > 0) map.set(key, active.length)
    }
    return map
  }, [todosByDate])

  // Reminder status per day (for color dots)
  const reminderDots = useMemo(() => {
    const overdue = new Set<string>()
    const pending = new Set<string>()
    const future = new Set<string>()
    const now = Date.now()
    for (const [key, items] of todosByDate) {
      for (const t of items) {
        if (t.done) continue
        if (t.dueDate && t.dueDate < now) overdue.add(key)
        else if (t.reminderTime && t.reminderTime <= now) pending.add(key)
        else if (t.dueDate) future.add(key)
      }
    }
    return { overdue, pending, future }
  }, [todosByDate])

  const [huangliCollapsed, setHuangliCollapsed] = useState(false)
  const [showBaziSettings, setShowBaziSettings] = useState(false)
  const [adviceView, setAdviceView] = useState<'advice' | 'bazi' | 'qimen'>('advice')
  const [qimenEventId, setQimenEventId] = useState<string | null>(null)
  const [qimenContext, setQimenContext] = useState<QimenEvent | null>(null)
  const [aiAdvice, setAiAdvice] = useState<string | null>(null)
  const [aiLoading, setAiLoading] = useState(false)
  const [aiError, setAiError] = useState<string | null>(null)
  const aiAbortRef = useRef<AbortController | null>(null)
  const [weather, setWeather] = useState<WeatherData | null>(null)
  const userProfile = useStore(s => s.userProfile)
  const setUserProfile = useStore(s => s.setUserProfile)
  const aiEnabled = useStore(s => s.aiEnabled)
  const qimenNavigationTarget = useStore(s => s.qimenNavigationTarget)
  const setQimenNavigationTarget = useStore(s => s.setQimenNavigationTarget)

  // 从主列表导航过来时打开奇门面板
  useEffect(() => {
    if (!qimenNavigationTarget) return
    const target = qimenNavigationTarget
    setQimenNavigationTarget(null)
    if (target.todoId) {
      const todo = todos.find(t => t.id === target.todoId)
      if (todo) {
        handleQimenSelect(todo)
        return
      }
    }
    setQimenEventId(null)
    setQimenContext({
      domain: (target.domain || 'career') as QimenDomain,
      scenario: target.scenario,
      description: target.text || '',
    })
    setAdviceView('qimen')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [qimenNavigationTarget])

  // 切换日期时重载 AI 建议 + 获取天气
  useEffect(() => {
    setAiAdvice(null)
    setAiError(null)
    setAiLoading(false)
    aiAbortRef.current?.abort()
    aiAbortRef.current = null

    // 获取当天天气（按需获取，不阻塞）
    let weatherCancelled = false
    fetchWeather().then(w => {
      if (!weatherCancelled) setWeather(w)
    }).catch(() => {
      if (!weatherCancelled) setWeather(null)
    })

    return () => { weatherCancelled = true }
  }, [selectedDate])

  // 八字命理
  const baziProfile: BaziProfile | null = useMemo(() => {
    if (!userProfile?.birthDate) return null
    const result = calculateBazi(
      new Date(userProfile.birthDate),
      userProfile.birthHour ?? null,
      userProfile.gender ?? null,
    )
    if (!result) console.warn('[CalendarView] calculateBazi returned null (birthDate=%s, birthHour=%s, hasGender=%s)',
      userProfile?.birthDate ? 'set' : 'null',
      userProfile?.birthHour ?? 'null',
      userProfile?.gender ?? 'null')
    return result
  }, [userProfile?.birthDate, userProfile?.birthHour, userProfile?.gender])

  // 每日命理指导（基于选中日期）
  const dailyGuidance: DailyGuidance | null = useMemo(() => {
    if (!selectedDate) return null
    const list = activeCat === 'all' ? todos : todos.filter(t => t.category === activeCat)
    return getDailyGuidance(selectedDate, baziProfile, list)
  }, [selectedDate, baziProfile, todos, activeCat])

  const handleQimenSelect = useCallback((todo: TodoItem) => {
    setQimenEventId(todo.id)
    setQimenContext(null)
    setAdviceView('qimen')
  }, [])

  // 日期快捷按钮 — 主动进入奇门
  const handleQimenQuickEntry = useCallback(() => {
    setQimenEventId(null)
    setQimenContext({ domain: 'career', scenario: undefined, description: '' })
    setAdviceView('qimen')
  }, [])

  // — 点击：有待办 qimenEvent — 选中；无 — 关键词自动推断领域
  const handleQimenBadgeClick = useCallback((e: React.MouseEvent, todo: TodoItem) => {
    e.stopPropagation()
    if (todo.qimenEvent) {
      handleQimenSelect(todo)
    } else {
      const suggested = suggestQimenEvent(todo.text)
      setQimenEventId(null)
      setQimenContext({
        domain: suggested?.domain || 'career',
        scenario: suggested?.scenario,
        description: todo.text,
      })
      setAdviceView('qimen')
    }
  }, [handleQimenSelect])

  // 奇门分析完成后创建待办
  const handleQimenCreateTodo = useCallback((qimenEvent: QimenEvent) => {
    const { addTodo, setQimenEvent, setPriority, todos } = useStore.getState()
    addTodo(
      qimenEvent.description || t('calendar.qimenFallback', { domain: qimenEvent.domain }),
      activeCat === 'all' ? undefined : activeCat,
    )
    // 获取刚创建的待办 ID（最新添加的那个）
    const newTodo = useStore.getState().todos.find(t => !todos.includes(t))
    if (newTodo) {
      setQimenEvent(newTodo.id, qimenEvent)
      setPriority(newTodo.id, 1)
    }
  }, [activeCat])

  const handleAIAnalysis = useCallback(async () => {
    if (!selectedDate || !selectedHuangli || aiLoading) return
    aiAbortRef.current?.abort()
    const controller = new AbortController()
    aiAbortRef.current = controller

    setAiAdvice(null)
    setAiError(null)
    setAiLoading(true)

    try {
      const list = activeCat === 'all' ? todos : todos.filter(t => t.category === activeCat)
      const result = await getAIGuidance(
        selectedDate,
        selectedHuangli,
        baziProfile,
        list,
        (text) => setAiAdvice(text),
        getLocalAIConfig({ enabled: aiEnabled }),        weather,
      )
      if (!controller.signal.aborted) {
        setAiAdvice(result.text)
        setAiLoading(false)
      }
    } catch (err: unknown) {
      if (!controller.signal.aborted) {
        setAiError(err instanceof Error ? err.message : t('guidance.aiError'))
        setAiLoading(false)
      }
    }
  }, [selectedDate, selectedHuangli, baziProfile, todos, activeCat, weather, aiEnabled, aiLoading])

  return (
    <div className="flex flex-col px-3 py-2 select-none">
      {/* Month navigation */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1">
          <button onClick={prevMonth}
            className="w-6 h-6 flex items-center justify-center rounded-lg text-xs transition-all"
            style={{ color: 'var(--text-secondary)' }}>
            <Icon icon="fa-solid fa-chevron-left" />
          </button>
          <button onClick={nextMonth}
            className="w-6 h-6 flex items-center justify-center rounded-lg text-xs transition-all"
            style={{ color: 'var(--text-secondary)' }}>
            <Icon icon="fa-solid fa-chevron-right" />
          </button>
        </div>
        <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
          {viewMode === 'week' && selectedDate
            ? t('calendar.weekLabel', { month: selectedDate.getMonth() + 1, date: selectedDate.getDate() })
            : t('calendar.monthYearFormat', { year, month: months[month] })}
        </span>
        <div className="flex items-center bg-white/[0.04] rounded-lg p-0.5 gap-0.5">
          <button onClick={() => setViewMode('month')}
            className={`px-1.5 py-0.5 rounded text-[8px] font-medium transition-all ${
              viewMode === 'month' ? 'bg-white/[0.1] text-text-secondary' : 'text-text-muted hover:text-text-secondary'
            }`}>
            {t('calendar.month')}
          </button>
          <button onClick={() => setViewMode('week')}
            className={`px-1.5 py-0.5 rounded text-[8px] font-medium transition-all ${
              viewMode === 'week' ? 'bg-white/[0.1] text-text-secondary' : 'text-text-muted hover:text-text-secondary'
            }`}>
            {t('calendar.week')}
          </button>
        </div>
      </div>

      {/* Weekday headers —?month view */}
      {viewMode === 'month' && (
        <div className="grid grid-cols-7 gap-0.5 mb-1">
          {weekdays.map((d, i) => (
            <div key={i} className="text-center text-2xs py-1"
              style={{ color: 'var(--text-dim)' }}>
              {d}
            </div>
          ))}
        </div>
      )}

      {/* Day grid —?month view */}
      {viewMode === 'month' && (
        <div className="grid grid-cols-7 gap-0.5">
          {days.map((day, i) => {
            const key = day.toDateString()
            const count = todoCounts.get(key) || 0
            const isToday = isSameDay(day, today)
            const isSelected = selectedDate && isSameDay(day, selectedDate)
            const inMonth = isCurrentMonth(day)
            const lunarInfo = lunarInfoMap.get(key)

          // Collect cell tags (solar term first, then festivals)
          const cellTags: string[] = []
          if (lunarInfo?.jieQi) cellTags.push(lunarInfo.jieQi)
          if (lunarInfo?.festivals) {
            for (const f of lunarInfo.festivals) {
              if (cellTags.length >= MAX_CELL_TAGS) break
              cellTags.push(f)
            }
          }

          return (
            <div
              key={i}
              onClick={() => handleDayClick(day)}
              onDoubleClick={() => handleDayDoubleClick(day)}
              onDrop={(e) => handleDayDrop(e, day)}
              onDragOver={handleDayDragOver}
              onKeyDown={e => handleDayKeyDown(e, day)}
              tabIndex={0}
              ref={el => { if (el) dayCellRefs.current.set(day.toDateString(), el) }}
              className={`relative flex flex-col items-center justify-start py-1 rounded-lg cursor-pointer transition-all min-h-[48px] ${
                !inMonth ? 'opacity-25' : ''
              } ${
                isSelected
                  ? 'bg-fluent-blue/15 ring-1 ring-fluent-blue/30'
                  : 'hover:bg-white/[0.04]'
              }`}
              style={isToday && !isSelected ? { background: 'rgba(96,165,250,0.08)' } : undefined}
            >
              <span className={`text-xs leading-none mb-0.5 ${
                isToday ? 'font-bold text-fluent-blue' : ''
              }`}
                style={{ color: isToday ? undefined : 'var(--text-secondary)' }}
              >
                {day.getDate()}
              </span>
              {/* Lunar day + ganZhi */}
              {lunarInfo && lunarInfo.lunarDay && (
                <span className="text-[7px] leading-none mb-0.5" style={{ color: 'var(--text-muted)' }}>
                  {lunarInfo.ganZhiDay} {lunarInfo.lunarDay}
                </span>
              )}
              {/* Todo count */}
              {count > 0 && (
                <span className={`text-[8px] px-1 rounded-full leading-none ${
                  count > 3 ? 'bg-red-500/20 text-red-400' : 'bg-fluent-blue/15 text-fluent-blue/70'
                }`}>
                  {count > 9 ? t('calendar.over9') : count}
                </span>
              )}
              {/* Event indicators — purple line at top of cell */}
              {(() => {
                const dayEvents = (todosByDate.get(key) || []).filter(t => t.isEvent && !t.done)
                if (dayEvents.length === 0) return null
                return (
                  <div className="w-full flex gap-0.5 mt-0.5 justify-center">
                    {dayEvents.slice(0, 3).map(evt => (
                      <div key={evt.id} className="flex-1 h-0.5 rounded-full bg-purple-500/60" style={{ maxWidth: 12 }} title={evt.text} />
                    ))}
                    {dayEvents.length > 3 && (
                      <span className="text-[6px] text-purple-400/60 leading-none">+{dayEvents.length - 3}</span>
                    )}
                  </div>
                )
              })()}
              {/* Solar term / festival tags */}
              {cellTags.length > 0 && (
                <div className="flex flex-wrap gap-0.5 mt-0.5 justify-center">
                  {cellTags.map((tag, idx) => (
                    <span key={idx}
                      className={`text-[6px] px-1 rounded-sm leading-tight ${
                        lunarInfo?.jieQi === tag
                          ? 'bg-amber-500/20 text-amber-400'
                          : 'bg-rose-500/15 text-rose-400'
                      }`}
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
              {/* Reminder status dots */}
              <div className="flex items-center gap-0.5 mt-0.5 justify-center" style={{ minHeight: 4 }}>
                {reminderDots.overdue.has(key) && <span className="w-1 h-1 rounded-full bg-red-400" />}
                {reminderDots.pending.has(key) && <span className="w-1 h-1 rounded-full bg-orange-400" />}
                {reminderDots.future.has(key) && <span className="w-1 h-1 rounded-full bg-white/30" />}
              </div>
              {/* Drop indicator */}
              {dragTodoId && (
                <div className="absolute inset-0 rounded-lg border-2 border-dashed border-fluent-blue/30 opacity-0 hover:opacity-100 pointer-events-none" />
              )}
            </div>
          )
        })}
      </div>
      )}

      {/* Full time-grid week view */}
      {viewMode === 'week' && selectedDate && (
        <WeekView
          weekStart={selectedDate}
          selectedDate={selectedDate}
          onSelectDate={handleDayClick}
          todosByDate={todosByDate}
          weekdays={weekdays}
          today={today}
        />
      )}

      {/* Inline event creation prompt — replaces window.prompt */}
      {inlinePrompt && (
        <div className="mt-2 flex items-center gap-2 px-2 py-1.5 rounded-lg" style={{ background: 'rgba(96,165,250,0.1)' }}>
          <span className="text-xs whitespace-nowrap flex-shrink-0" style={{ color: 'var(--text-muted)' }}>
{t('calendar.eventLabel', { date: inlinePrompt.date.toLocaleDateString() })}
          </span>
          <input
            ref={inlineInputRef}
            value={inlinePrompt.value}
            onChange={e => setInlinePrompt({ ...inlinePrompt, value: e.target.value })}
            onKeyDown={e => {
              if (e.key === 'Enter') confirmInlineEvent()
              if (e.key === 'Escape') setInlinePrompt(null)
            }}
            className="flex-1 bg-transparent border-none outline-none text-xs"
            style={{ color: 'var(--text-primary)' }}
            placeholder={t('calendar.eventPlaceholder')}
            maxLength={200}
          />
          <button onClick={confirmInlineEvent}
            className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-2xs text-fluent-blue hover:bg-white/[0.08] transition-all">
            <Icon icon="fa-solid fa-check" />
          </button>
          <button onClick={() => setInlinePrompt(null)}
            className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded text-2xs text-text-muted hover:bg-white/[0.08] transition-all">
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>
      )}

      {/* Selected date: todo list + huangli */}
      {selectedDate && (
        <div className="mt-3 pt-3 border-t border-white/[0.06]">
          {/* Date header */}
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
              {selectedDate.toLocaleDateString(
                useStore.getState().locale === 'en' ? 'en-US' : 'zh-CN',
                { month: 'long', day: 'numeric', weekday: 'short' }
              )}
            </span>
            <span className="text-2xs px-1.5 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}>
              {t('calendar.itemsCount', { count: selectedTodos.length })}
            </span>
            {selectedTodos.length === 0 && (
              <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>
                {t('calendar.dropHint')}
              </span>
            )}
          </div>

          {/* Todo list */}
          <div className="flex flex-col gap-0.5 max-h-[120px] overflow-y-auto">
            {selectedTodos.map(todo => (
              <div key={todo.id}
                draggable
                onDragStart={(e) => handleDragStart(e, todo.id)}
                className="group flex items-center gap-2 px-2 py-1 rounded-lg transition-all cursor-grab active:cursor-grabbing hover:bg-white/[0.04]"
              >
                <button
                  draggable={false}
                  onClick={() => toggleTodo(todo.id)}
                  className={`w-2.5 h-2.5 rounded-full flex-shrink-0 transition-all hover:scale-125 ${
                    todo.done ? 'bg-green-400/50' : 'bg-fluent-blue/50'
                  }`}
                  title={todo.done ? t('todo.markUndone') : t('todo.markDone')}
                />
                <span className={`flex-1 text-xs truncate ${todo.done ? 'line-through text-text-muted' : ''}`}
                  style={{ color: todo.done ? undefined : 'var(--text-secondary)' }}>
                  {todo.text}
                </span>
                {childCounts.has(todo.id) && (
                  <span className="flex-shrink-0 text-[7px] px-1 py-0.5 rounded bg-white/[0.06] text-text-muted leading-none">
                    {childCounts.get(todo.id)}
                  </span>
                )}
                {todo.priority !== undefined && (
                  <span className={`text-[7px] px-1 py-0.5 rounded ${
                    todo.priority === 0 ? 'bg-red-500/15 text-red-400' :
                    todo.priority === 1 ? 'bg-yellow-500/15 text-yellow-400' :
                    'bg-gray-500/15 text-gray-400'
                  }`}>
                    P{todo.priority}
                  </span>
                )}
                {!todo.done && todo.priority !== undefined && todo.priority <= 1 && (
                  <button
                    onClick={(e) => handleQimenBadgeClick(e, todo)}
                    className={`flex-shrink-0 text-2xs px-1 py-0.5 rounded transition-all hover:scale-110 ${
                      todo.qimenEvent
                        ? 'text-purple-400 bg-purple-500/15'
                        : (() => {
                            const s = suggestQimenEvent(todo.text)
                            return s
                              ? 'text-purple-300/50 bg-purple-500/8 hover:text-purple-400 hover:bg-purple-500/15'
                              : 'text-text-dim hover:text-purple-400/60 hover:bg-purple-500/10'
                          })()
                    }`}
                    title={(() => {
                      if (todo.qimenEvent) return t('guidance.qimen')
                      const s = suggestQimenEvent(todo.text)
                      return s ? `${t('guidance.qimen')}·${getDomainLabel(s.domain)}${s.scenario ? `·${s.scenario}` : ''}` : t('guidance.qimenEntryTitle')
                    })()}
                  >
                    </button>
                )}
                <button onClick={() => removeDueDate(todo.id)}
                  className="opacity-0 group-hover:opacity-100 text-2xs transition-all"
                  style={{ color: 'var(--text-dim)' }}
                  title={t('todo.dueDate.remove')}>
                  <Icon icon="fa-solid fa-xmark" />
                </button>
                <button draggable={false} onClick={() => deleteTodo(todo.id)}
                  className="opacity-0 group-hover:opacity-100 text-2xs transition-all hover:text-red-400"
                  style={{ color: 'var(--text-dim)' }}
                  title={t('common.delete')}>
                  <Icon icon="fa-solid fa-trash-can" />
                </button>
              </div>
            ))}
            {selectedTodos.length > 0 && (
              <div className="mt-1 pt-1 border-t border-white/[0.04]">
                <div className="flex items-center gap-2 text-2xs">
                  <span style={{ color: 'var(--text-muted)' }}>
                    {t('calendar.completedCount')}: {selectedTodos.filter(t => t.done).length}
                  </span>
                  <span style={{ color: 'var(--text-muted)' }}>
                    {t('calendar.pendingCount')}: {selectedTodos.filter(t => !t.done).length}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* Reminder section */}
          {selectedReminders.length > 0 && (
            <div className="mt-2 pt-2 border-t border-white/[0.04]">
              <div className="flex items-center gap-1.5 mb-1">
                <Icon icon="fa-regular fa-bell text-2xs text-fluent-blue/60" />
                <span className="text-2xs font-medium" style={{ color: 'var(--text-muted)' }}>
                  {t('calendar.reminders')}
                </span>
                <span className="text-[8px]" style={{ color: 'var(--text-dim)' }}>
                  {selectedReminders.length}
                </span>
              </div>
              <div className="flex flex-col gap-0.5 max-h-[80px] overflow-y-auto">
                {selectedReminders.map(todo => {
                  const rt = todo.reminderTime ? new Date(todo.reminderTime) : null
                  return (
                    <div key={`remind-${todo.id}`}
                      className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-white/[0.04]"
                    >
                      <span className="text-2xs text-fluent-blue/70 flex-shrink-0 w-12">
                        {rt ? rt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                      </span>
                      <span className={`flex-1 text-xs truncate ${todo.done ? 'line-through text-text-muted' : ''}`}
                        style={{ color: todo.done ? undefined : 'var(--text-secondary)' }}>
                        {todo.text}
                      </span>
                      {!todo.done && (
                        <button
                          onClick={() => {
                            const item = todos.find(t => t.id === todo.id)
                            if (item) toggleTodo(item.id)
                          }}
                          className="text-2xs text-text-dim hover:text-text-secondary transition-colors flex-shrink-0"
                        >
                          <Icon icon="fa-regular fa-circle-check" />
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* 奇门起局快捷入口 */}
          <div className="mt-2 pt-2 border-t border-white/[0.04]">
            <button
              onClick={handleQimenQuickEntry}
              className="w-full flex items-center justify-center gap-1.5 px-2 py-1.5 rounded-lg text-2xs transition-all"
              style={{
                background: 'rgba(139,92,246,0.08)',
                color: '#c4b5fd',
                border: '1px dashed rgba(139,92,246,0.2)',
              }}
            >
              <Icon icon="fa-solid fa-compass text-xs" />
              {t('guidance.qimen')} · {t('guidance.qimenEntryTitle')}
            </button>
          </div>

          {/* ── 黄历 section ── */}
          {selectedHuangli && (
            <HuangliPanel
              huangli={selectedHuangli}
              collapsed={huangliCollapsed}
              onToggle={() => setHuangliCollapsed(v => !v)}
            />
          )}

          {/* ── 命理指导 dashboard — always mount to preserve tab state */}
          <Suspense fallback={<div className="h-8" />}>
          <GuidanceDashboard
              dailyGuidance={dailyGuidance}
              adviceView={adviceView}
              onAdviceViewChange={setAdviceView}
              baziProfile={baziProfile}
              onOpenBaziSettings={() => setShowBaziSettings(true)}
              weather={weather}
              userProfile={userProfile}
              onSetCity={(city) => setUserProfile({ city })}
              t={t}
              aiAdvice={aiAdvice}
              aiLoading={aiLoading}
              aiError={aiError}
              onAIAnalysis={handleAIAnalysis}
              qimenEventId={qimenEventId}
              qimenContext={qimenContext}
              selectedTodos={selectedTodos}
              onCreateTodo={handleQimenCreateTodo}
            />
          </Suspense>


          {/* 八字设置弹窗 */}
          {showBaziSettings && (
            <BaziSettings onClose={() => setShowBaziSettings(false)} />
          )}
        </div>
      )}
    </div>
  )
}

export default CalendarView

