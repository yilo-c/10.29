import { useState, useEffect, useRef, useCallback } from 'react'
import { useStore } from '@desk-notes/shared'

interface PomodoroBarProps {
  todoId: string
  todoText: string
  onClose: () => void
}

export default function PomodoroBar({ todoText, onClose }: PomodoroBarProps) {
  const focusDuration = useStore(s => s.focusDuration)
  const incrementSession = useStore(s => s.incrementPomodoroSession)

  const [remaining, setRemaining] = useState(focusDuration * 60)
  const [running, setRunning] = useState(false)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const totalSeconds = focusDuration * 60

  const stopTimer = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [])

  useEffect(() => {
    if (running) {
      intervalRef.current = setInterval(() => {
        setRemaining(prev => {
          if (prev <= 1) {
            stopTimer()
            setRunning(false)
            incrementSession()
            // Vibrate on completion
            if (navigator.vibrate) navigator.vibrate(200)
            return 0
          }
          return prev - 1
        })
      }, 1000)
    } else {
      stopTimer()
    }
    return stopTimer
  }, [running, stopTimer, incrementSession])

  const toggleRunning = () => {
    if (remaining <= 0) {
      setRemaining(focusDuration * 60)
      setRunning(true)
    } else {
      setRunning(v => !v)
    }
  }

  const resetTimer = () => {
    stopTimer()
    setRunning(false)
    setRemaining(focusDuration * 60)
  }

  const progress = 1 - remaining / totalSeconds
  const mins = Math.floor(remaining / 60)
  const secs = remaining % 60

  return (
    <div
      style={{
        background: 'var(--bg-secondary)',
        borderTop: '1px solid var(--border-card)',
        padding: '10px var(--space-4)',
        paddingBottom: 'calc(10px + var(--safe-bottom))',
        flexShrink: 0,
      }}
    >
      {/* Task name and close */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
          🍅 {todoText}
        </div>
        <button
          onClick={onClose}
          style={{
            background: 'none',
            border: 'none',
            color: 'var(--text-tertiary)',
            cursor: 'pointer',
            padding: '4px 8px',
            fontSize: 14,
            flexShrink: 0,
          }}
        >
          ✕
        </button>
      </div>

      {/* Progress bar */}
      <div
        style={{
          height: 3,
          borderRadius: 2,
          background: 'var(--bg-tertiary)',
          marginBottom: 8,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            height: '100%',
            width: `${progress * 100}%`,
            background: running ? 'var(--gold-primary)' : 'var(--gold-muted)',
            borderRadius: 2,
            transition: 'width 1s linear',
          }}
        />
      </div>

      {/* Controls */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
        {/* Time display */}
        <span
          style={{
            fontSize: 22,
            fontWeight: 600,
            fontVariantNumeric: 'tabular-nums',
            color: 'var(--text-primary)',
            minWidth: 60,
            textAlign: 'center',
          }}
        >
          {String(mins).padStart(2, '0')}:{String(secs).padStart(2, '0')}
        </span>

        {/* Start/Pause */}
        <button
          onClick={toggleRunning}
          style={{
            width: 44,
            height: 44,
            borderRadius: '50%',
            border: 'none',
            background: 'var(--gold-primary)',
            color: '#0a0a12',
            fontSize: 18,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {running ? '⏸' : remaining <= 0 ? '↻' : '▶'}
        </button>

        {/* Reset */}
        <button
          onClick={resetTimer}
          style={{
            background: 'none',
            border: '1px solid var(--border-card)',
            color: 'var(--text-tertiary)',
            fontSize: 16,
            cursor: 'pointer',
            padding: '8px 14px',
            borderRadius: 'var(--radius-sm)',
          }}
        >
          重置
        </button>
      </div>
    </div>
  )
}
