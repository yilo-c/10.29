import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { swallowError } from '../../utils/swallowError'

interface PomodoroTimerProps {
  todoId: string
  noteId?: string
}

const BREAK_SUGGESTION_DURATION = 4000

const PomodoroTimer: React.FC<PomodoroTimerProps> = ({ todoId: _todoId, noteId }) => {
  const focusDuration = useStore(s => s.focusDuration)
  const pomodoroSessionCount = useStore(s => s.pomodoroSessionCount)
  const incrementPomodoro = useStore(s => s.incrementPomodoro)
  const incrementPomodoroSession = useStore(s => s.incrementPomodoroSession)
  const { t } = useTranslation()

  const [active, setActive] = useState(false)
  const [remaining, setRemaining] = useState(focusDuration * 60 * 1000)
  const [paused, setPaused] = useState(false)
  const [showPicker, setShowPicker] = useState(false)
  const [showBreakSuggestion, setShowBreakSuggestion] = useState(false)

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const startTimeRef = useRef<number>(0)
  const elapsedRef = useRef<number>(0)
  const pickerRef = useRef<HTMLDivElement>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const breakTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const pomodoroMs = focusDuration * 60 * 1000

  // Reset remaining when focus duration changes while idle
  useEffect(() => {
    if (!active) {
      setRemaining(focusDuration * 60 * 1000)
    }
  }, [focusDuration, active])

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
      if (breakTimerRef.current) clearTimeout(breakTimerRef.current)
      audioCtxRef.current?.close()
    }
  }, [])

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

  // Keep noteId in a ref
  const noteIdRef = useRef(noteId)
  useEffect(() => { noteIdRef.current = noteId }, [noteId])

  const clearTimer = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [])

  const notifyComplete = useCallback(() => {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification(t('pomodoro.notificationTitle'), { body: t('pomodoro.notificationBody') })
    }
    try {
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)()
      }
      const ctx = audioCtxRef.current
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.connect(gain)
      gain.connect(ctx.destination)
      osc.frequency.value = 880
      osc.type = 'sine'
      gain.gain.setValueAtTime(0.3, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.5)
      osc.start(ctx.currentTime)
      osc.stop(ctx.currentTime + 0.5)
    } catch (e) { /* audio not supported */ swallowError('PomodoroTimer:92', e) }
  }, [t])

  const startTimer = useCallback((customMinutes?: number) => {
    const ms = (customMinutes ?? focusDuration) * 60 * 1000
    clearTimer()
    startTimeRef.current = Date.now()
    elapsedRef.current = 0
    setActive(true)
    setPaused(false)
    setRemaining(ms)
    setShowBreakSuggestion(false)
    if (breakTimerRef.current) clearTimeout(breakTimerRef.current)

    intervalRef.current = setInterval(() => {
      const elapsed = Date.now() - startTimeRef.current + elapsedRef.current
      const rem = Math.max(0, ms - elapsed)
      setRemaining(rem)

      if (rem <= 0) {
        clearTimer()
        setActive(false)
        setRemaining(ms)
        notifyComplete()
        if (noteIdRef.current) incrementPomodoro(noteIdRef.current)
        incrementPomodoroSession()
        setShowBreakSuggestion(true)
        breakTimerRef.current = setTimeout(() => setShowBreakSuggestion(false), BREAK_SUGGESTION_DURATION)
      }
    }, 1000)
  }, [clearTimer, notifyComplete, incrementPomodoro, incrementPomodoroSession, focusDuration])

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
    setRemaining(pomodoroMs)
    setShowBreakSuggestion(false)
    if (breakTimerRef.current) clearTimeout(breakTimerRef.current)
    elapsedRef.current = 0
  }, [clearTimer, pomodoroMs])

  const handleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    if (paused) {
      if ('Notification' in window && Notification.permission === 'default') {
        Notification.requestPermission()
      }
      startTimer()
    } else if (active) {
      pauseTimer()
    }
  }, [active, paused, startTimer, pauseTimer])

  const formatTime = (ms: number): string => {
    const totalSec = Math.ceil(ms / 1000)
    const min = Math.floor(totalSec / 60)
    const sec = totalSec % 60
    return `${min}:${sec.toString().padStart(2, '0')}`
  }

  const progress = 1 - remaining / pomodoroMs

  return (
    <span className="inline-flex items-center gap-0.5 relative" onClick={e => e.stopPropagation()}>
      {!active && !paused ? (
        <>
          <button
            onClick={e => {
              e.stopPropagation()
              if ('Notification' in window && Notification.permission === 'default') {
                Notification.requestPermission()
              }
              setShowPicker(!showPicker)
            }}
            className="flex-shrink-0 transition-all rounded text-xs px-1 py-0.5 text-text-dim hover:text-text-secondary hover:bg-white/[0.04]"
            title={t('pomodoro.selectDuration')}
          >
            <svg viewBox="0 0 20 20" className="w-4 h-4 inline-block" fill="none" stroke="currentColor" strokeWidth="1.2">
              <path d="M10 2v2M6.5 3.5l1 1.5M3.5 6.5l1.5 1M2 10h2M16 10h2M15.5 6.5l-1.5 1M13.5 3.5l-1 1.5" stroke="currentColor" strokeWidth="1.2" />
            </svg>
          </button>
          {showPicker && (
            <div ref={pickerRef}
              className="absolute top-full right-0 mt-1 z-50 border border-white/[0.08] rounded-xl px-2 py-1.5 shadow-xl flex items-center gap-1"
              style={{ background: 'var(--panel-bg-solid)' }}
            >
              {[15, 25, 30, 45, 60].map(d => (
                <button
                  key={d}
                  onClick={e => {
                    e.stopPropagation()
                    startTimer(d)
                    setShowPicker(false)
                  }}
                  className="text-xs px-2 py-1 rounded-lg transition-colors text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]"
                >
                  {d}
                </button>
              ))}
            </div>
          )}
        </>
      ) : (
        <>
          {/* Timer controls row: icon + time text + reset */}
          <span
            onClick={handleClick}
            className={`inline-flex items-center gap-1 cursor-pointer transition-all rounded px-1 py-0.5 text-xs
              ${active && !paused ? 'text-red-400/70 hover:bg-white/[0.04]' : ''}
              ${paused ? 'text-yellow-400/70 hover:bg-white/[0.04]' : ''}`}
            title={
              active
                ? paused
                  ? t('pomodoro.paused', { time: formatTime(remaining) })
                  : t('pomodoro.running', { time: formatTime(remaining) })
                : ''
            }
          >
            <svg
              viewBox="0 0 20 20"
              className="w-4 h-4 inline-block"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
              style={active && !paused ? { animation: 'pomodoro-spin 1s linear infinite' } : undefined}
            >
              {active && !paused ? (
                <>
                  <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.5" opacity="0.2" />
                  <circle
                    cx="10" cy="10" r="8"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeDasharray={`${progress * 50.24} ${50.24 - progress * 50.24}`}
                    transform="rotate(-90, 10, 10)"
                    opacity="0.8"
                  />
                </>
              ) : paused ? (
                <>
                  <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.5" opacity="0.2" />
                  <rect x="6.5" y="6" width="2" height="8" rx="0.5" fill="currentColor" stroke="none" />
                  <rect x="11.5" y="6" width="2" height="8" rx="0.5" fill="currentColor" stroke="none" />
                </>
              ) : null}
            </svg>
            {/* Time text always visible */}
            <span className={`font-mono leading-none ${paused ? 'text-yellow-400/80' : ''}`}>
              {formatTime(remaining)}
            </span>
            {/* Session count */}
            {pomodoroSessionCount > 0 && (
              <span className="text-[8px] text-text-dim ml-0.5" title={t('pomodoro.sessionCount', { count: pomodoroSessionCount })}>
                #{pomodoroSessionCount}
              </span>
            )}
          </span>
          <button
            onClick={e => { e.stopPropagation(); resetTimer() }}
            className="text-[8px] text-text-dim hover:text-red-400/50 transition-colors"
            title={t('pomodoro.reset')}
          >
            ✕
          </button>
        </>
      )}

      {/* Break suggestion toast */}
      {showBreakSuggestion && (
        <span className="absolute -top-5 left-1/2 -translate-x-1/2 whitespace-nowrap text-2xs text-green-400/80 animate-pulse pointer-events-none">
          {t('pomodoro.breakSuggestion')}
        </span>
      )}
    </span>
  )
}

export default PomodoroTimer
