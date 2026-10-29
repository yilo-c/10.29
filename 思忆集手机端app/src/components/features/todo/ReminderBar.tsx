import { useState, useEffect, useCallback, useRef } from 'react'
import { IconBell, IconX } from '../../ui'
import { swallowError } from '../../../lib/swallowError'

interface ReminderBarProps {
  todoId: string
  todoText: string
  reminderTime: number
  onSnooze: (minutes: number) => void
  onDismiss: (id: string) => void
}

export default function ReminderBar({ todoId, todoText, reminderTime, onSnooze, onDismiss }: ReminderBarProps) {
  const [isRinging, setIsRinging] = useState(false)
  const audioRef = useRef<AudioContext | null>(null)
  const oscillatorRef = useRef<OscillatorNode | null>(null)

  const startAlarm = useCallback(() => {
    try {
      const ctx = new AudioContext()
      audioRef.current = ctx
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.connect(gain)
      gain.connect(ctx.destination)
      gain.gain.value = 0.15
      osc.frequency.value = 440

      let high = false
      const interval = setInterval(() => {
        osc.frequency.value = high ? 440 : 660
        high = !high
      }, 500)

      osc.start()
      oscillatorRef.current = osc

      return () => {
        clearInterval(interval)
        osc.stop()
        ctx.close()
      }
    } catch {
      return () => {}
    }
  }, [])

  const stopAlarm = useCallback(() => {
    try {
      oscillatorRef.current?.stop()
      audioRef.current?.close()
    } catch (e) { swallowError('ReminderBar:51', e) }
    oscillatorRef.current = null
    audioRef.current = null
  }, [])

  useEffect(() => {
    if (!reminderTime) return
    const now = Date.now()
    if (reminderTime <= now) {
      setIsRinging(true)
      const cleanup = startAlarm()
      navigator.vibrate?.([200, 100, 200, 100, 200])
      return cleanup
    }
    const delay = reminderTime - now
    const timer = setTimeout(() => {
      setIsRinging(true)
      startAlarm()
      navigator.vibrate?.([200, 100, 200, 100, 200])
    }, delay)
    return () => clearTimeout(timer)
  }, [reminderTime, startAlarm])

  const handleSnooze = useCallback((minutes: number) => {
    stopAlarm()
    setIsRinging(false)
    onSnooze(minutes)
  }, [stopAlarm, onSnooze])

  const handleDismiss = useCallback(() => {
    stopAlarm()
    setIsRinging(false)
    onDismiss(todoId)
  }, [todoId, stopAlarm, onDismiss])

  if (!isRinging) return null

  return (
    <div style={{
      position: 'fixed',
      top: 0,
      left: 0,
      right: 0,
      background: 'var(--accent)',
      color: '#FFFFFF',
      padding: '12px 16px',
      paddingTop: 'calc(12px + env(safe-area-inset-top, 0px))',
      zIndex: 200,
      animation: 'fadeSlideIn 0.25s ease',
      display: 'flex',
      flexDirection: 'column',
      gap: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <IconBell size={20} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: 'var(--text-subhead)' }}>提醒</div>
          <div style={{
            fontSize: 'var(--text-footnote)',
            opacity: 0.9,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {todoText}
          </div>
        </div>
        <button
          onClick={handleDismiss}
          style={{
            background: 'rgba(255,255,255,0.2)',
            border: 'none',
            borderRadius: '50%',
            width: 32,
            height: 32,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: '#FFFFFF',
            cursor: 'pointer',
          }}
        >
          <IconX size={16} />
        </button>
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button
          onClick={() => handleSnooze(5)}
          style={{
            flex: 1,
            padding: '6px',
            background: 'rgba(255,255,255,0.2)',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            color: '#FFFFFF',
            fontSize: 'var(--text-caption-1)',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          5分钟后
        </button>
        <button
          onClick={() => handleSnooze(15)}
          style={{
            flex: 1,
            padding: '6px',
            background: 'rgba(255,255,255,0.2)',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            color: '#FFFFFF',
            fontSize: 'var(--text-caption-1)',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          15分钟后
        </button>
        <button
          onClick={() => handleSnooze(60)}
          style={{
            flex: 1,
            padding: '6px',
            background: 'rgba(255,255,255,0.2)',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            color: '#FFFFFF',
            fontSize: 'var(--text-caption-1)',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          1小时后
        </button>
      </div>
    </div>
  )
}
