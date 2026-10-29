import React, { useState, useRef, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { swallowError } from '../../utils/swallowError'

interface FocusTimerProps {
  onFocusChange: (todoId: string | null) => void
  onCategoryFocusChange?: (catId: string | null) => void
}

const DURATIONS = [15, 25, 30, 45, 60]

const STORAGE_KEY = 'focus_timer_sessions'

function loadSessions(): number {
  try { return Number(localStorage.getItem(STORAGE_KEY)) || 0 } catch { return 0 }
}
function saveSessions(n: number) {
  try { localStorage.setItem(STORAGE_KEY, String(n)) } catch (e) { /* noop */ swallowError('FocusTimer:18', e) }
}

const FocusTimer: React.FC<FocusTimerProps> = ({ onFocusChange, onCategoryFocusChange }) => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const activeCat = useStore(s => s.activeCat)
  const categories = useStore(s => s.categories)
  const currentCategory = activeCat !== 'all' ? categories.find(c => c.id === activeCat) : null

  const [active, setActive] = useState(false)
  const [paused, setPaused] = useState(false)
  const [duration, setDuration] = useState(25)
  const [remaining, setRemaining] = useState(25 * 60 * 1000)
  const [showPicker, setShowPicker] = useState(false)
  const [focusTodoId, setFocusTodoId] = useState<string | null>(null)
  const [focusCategory, setFocusCategory] = useState<string | null>(null)
  const [sessionCount, setSessionCount] = useState(loadSessions)

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startTimeRef = useRef<number>(0)
  const elapsedRef = useRef<number>(0)
  const pickerRef = useRef<HTMLDivElement>(null)

  // Active todos for focus target selector — filtered to current category
  const activeTodos = React.useMemo(
    () => {
      let list = todos.filter(t => !t.done && !t.parentId)
      if (activeCat !== 'all') list = list.filter(t => t.category === activeCat)
      return list.sort((a, b) => (a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt))
    },
    [todos, activeCat],
  )

  // Notify parent of focus changes
  useEffect(() => {
    onFocusChange(active ? focusTodoId : null)
  }, [active, focusTodoId, onFocusChange])

  // Notify parent of category focus changes
  useEffect(() => {
    onCategoryFocusChange?.(active && focusCategory ? focusCategory : null)
  }, [active, focusCategory, onCategoryFocusChange])

  // Close picker on outside click
  useEffect(() => {
    if (!showPicker) return
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowPicker(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showPicker])

  const notifyComplete = useCallback(() => {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(t('pomodoro.notificationTitle'), { body: t('pomodoro.notificationBody') })
    }
    try {
      if (navigator.vibrate) navigator.vibrate(200)
    } catch (e) { /* noop */ swallowError('FocusTimer:80', e) }
  }, [t])

  const clearTimer = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [])

  const startTimer = useCallback((customMinutes?: number) => {
    const mins = customMinutes ?? duration
    const ms = mins * 60 * 1000
    clearTimer()
    startTimeRef.current = Date.now()
    elapsedRef.current = 0
    setDuration(mins)
    setActive(true)
    setPaused(false)
    setRemaining(ms)
    setShowPicker(false)

    intervalRef.current = setInterval(() => {
      const elapsed = Date.now() - startTimeRef.current + elapsedRef.current
      const rem = Math.max(0, ms - elapsed)
      setRemaining(rem)

      if (rem <= 0) {
        clearTimer()
        setActive(false)
        setRemaining(ms)
        notifyComplete()
        setShowBreakSuggestion(true)
        breakTimerRef.current = setTimeout(() => setShowBreakSuggestion(false), 4000)
        const n = loadSessions() + 1
        saveSessions(n)
        setSessionCount(n)
      }
    }, 1000)
  }, [duration, clearTimer, notifyComplete])

  const pauseTimer = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
    elapsedRef.current += Date.now() - startTimeRef.current
    setPaused(true)
  }, [])

  const resetTimer = useCallback(() => {
    clearTimer()
    setActive(false)
    setPaused(false)
    setRemaining(duration * 60 * 1000)
    setShowBreakSuggestion(false)
    setFocusTodoId(null)
    setFocusCategory(null)
    if (breakTimerRef.current) clearTimeout(breakTimerRef.current)
    elapsedRef.current = 0
  }, [clearTimer, duration])

  const [showBreakSuggestion, setShowBreakSuggestion] = useState(false)
  const breakTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Cleanup on unmount (all timers)
  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
      if (breakTimerRef.current) clearTimeout(breakTimerRef.current)
    }
  }, [])

  const handleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    if (paused) {
      if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission()
      }
      startTimer()
    } else if (active) {
      pauseTimer()
    } else {
      setShowPicker(true)
    }
  }, [active, paused, startTimer, pauseTimer])

  const formatTime = (ms: number): string => {
    const totalSec = Math.ceil(ms / 1000)
    const min = Math.floor(totalSec / 60)
    const sec = totalSec % 60
    return `${min}:${sec.toString().padStart(2, '0')}`
  }

  const progress = 1 - remaining / (duration * 60 * 1000)

  if (showPicker && !active) {
    return (
      <div className="relative" ref={pickerRef}>
        <button
          onClick={handleClick}
          aria-label={t('pomodoro.selectDuration')}
          title={t('pomodoro.selectDuration')}
          className="flex items-center gap-1 text-2xs px-1.5 py-0.5 rounded-lg transition-colors text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]"
        >
          <svg viewBox="0 0 20 20" className="w-[10px] h-[10px] inline-block" fill="none" stroke="currentColor" strokeWidth="1.2">
            <path d="M10 2v2M6.5 3.5l1 1.5M3.5 6.5l1.5 1M2 10h2M16 10h2M15.5 6.5l-1.5 1M13.5 3.5l-1 1.5" stroke="currentColor" strokeWidth="1.2" />
          </svg>
        </button>
        <div
          className="absolute top-full left-1/2 -translate-x-1/2 mt-1 z-50 border border-white/[0.08] rounded-xl px-3 py-2 shadow-xl whitespace-nowrap"
          style={{ background: 'var(--panel-bg-solid)' }}
        >
          {/* Duration row */}
          <div className="flex items-center gap-1 mb-2">
            {DURATIONS.map(d => (
              <button
                key={d}
                onClick={e => { e.stopPropagation(); startTimer(d) }}
                className="text-xs px-2 py-1 rounded-lg transition-colors text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]"
              >
                {d}min
              </button>
            ))}
          </div>
          {/* Focus current category button */}
          {currentCategory && (
            <div className="border-t border-white/[0.06] pt-1.5 mt-0.5">
              <button
                onClick={e => { e.stopPropagation(); setFocusCategory(activeCat); startTimer(duration) }}
                className="w-full flex items-center gap-1.5 px-2 py-1 rounded-lg text-2xs transition-all text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]"
              >
                <Icon icon="fa-solid fa-layer-group text-[8px]" />
                聚焦「{currentCategory.label}」
              </button>
            </div>
          )}
          {/* Focus target selector */}
          {activeTodos.length > 0 && (
            <div className="border-t border-white/[0.06] pt-1.5 mt-0.5">
              <div className="text-[8px] mb-1" style={{ color: 'var(--text-muted)' }}>
                聚焦目标
              </div>
              <div className="flex flex-wrap gap-1" style={{ maxWidth: 240 }}>
                {activeTodos.slice(0, 6).map(t => (
                  <button
                    key={t.id}
                    onClick={e => { e.stopPropagation(); setFocusTodoId(t.id); startTimer(duration) }}
                    className={`text-2xs px-1.5 py-0.5 rounded-lg transition-all truncate max-w-[120px] ${
                      focusTodoId === t.id ? 'bg-fluent-blue/20 text-fluent-blue' : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]'
                    }`}
                  >
                    {t.text}
                  </button>
                ))}
                {activeTodos.length > 6 && (
                  <span className="text-[8px] text-text-dim self-center">+{activeTodos.length - 6}</span>
                )}
              </div>
            </div>
          )}
          {/* Session count */}
          {sessionCount > 0 && (
            <div className="text-[8px] text-text-dim text-center mt-1">
              {t('pomodoro.sessionCount', { count: sessionCount })}
            </div>
          )}
        </div>
      </div>
    )
  }

  return (
    <span className="inline-flex items-center gap-0.5 relative" onClick={e => e.stopPropagation()}>
      {!active && !paused ? (
        <button
          onClick={handleClick}
          aria-label={t('pomodoro.selectDuration')}
          className="flex items-center gap-1 text-2xs px-1.5 py-0.5 rounded-lg transition-colors text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]"
          title={t('pomodoro.selectDuration')}
        >
          <svg viewBox="0 0 20 20" className="w-[10px] h-[10px] inline-block" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M10 2v2M6.5 3.5l1 1.5M3.5 6.5l1.5 1M2 10h2M16 10h2M15.5 6.5l-1.5 1M13.5 3.5l-1 1.5" stroke="currentColor" strokeWidth="1.2" />
          </svg>
        </button>
      ) : (
        <>
          <span
            onClick={handleClick}
            className={`inline-flex items-center gap-1 cursor-pointer transition-all rounded px-1.5 py-1 text-xs ${
              active && !paused ? 'text-red-400/70 hover:bg-white/[0.04]' : ''
            } ${paused ? 'text-yellow-400/70 hover:bg-white/[0.04]' : ''}`}
            title={active ? (paused ? t('pomodoro.paused', { time: formatTime(remaining) }) : t('pomodoro.running', { time: formatTime(remaining) })) : ''}
          >
            <svg viewBox="0 0 20 20" className="w-4 h-4 inline-block" fill="none" stroke="currentColor" strokeWidth="1.5">
              {active && !paused ? (
                <>
                  <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.5" opacity="0.2" />
                  <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
                    strokeDasharray={`${progress * 50.24} ${50.24 - progress * 50.24}`}
                    transform="rotate(-90, 10, 10)" opacity="0.8" />
                </>
              ) : paused ? (
                <>
                  <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.5" opacity="0.2" />
                  <rect x="6.5" y="6" width="2" height="8" rx="0.5" fill="currentColor" stroke="none" />
                  <rect x="11.5" y="6" width="2" height="8" rx="0.5" fill="currentColor" stroke="none" />
                </>
              ) : null}
            </svg>
            <span className="font-mono leading-none">{formatTime(remaining)}</span>
          </span>
          <button
            onClick={e => { e.stopPropagation(); resetTimer() }}
            className="text-[8px] text-text-dim hover:text-red-400/50 transition-colors px-0.5"
            title={t('pomodoro.reset')}
          >
            ✕
          </button>
        </>
      )}

      {showBreakSuggestion && (
        <span className="absolute top-full left-1/2 -translate-x-1/2 mt-0.5 whitespace-nowrap text-2xs text-green-400/80 animate-pulse pointer-events-none">
          {t('pomodoro.breakSuggestion')}
        </span>
      )}
    </span>
  )
}

export default FocusTimer
