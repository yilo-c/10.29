import { useState, useEffect, useRef, useCallback } from 'react'

export type UserActivityLevel = 'active' | 'idle' | 'away'

export interface QueuedNotification {
  id: string
  title: string
  body: string
  priority: 'normal' | 'urgent'
  createdAt: number
  onClick?: () => void
}

const IDLE_MS = 30000   // 30s no input → idle
const AWAY_MS = 300000  // 5min no input → away

/**
 * Track user activity level and manage a notification queue.
 * Non-urgent notifications are delayed until the user becomes active.
 */
export function useUserActivity() {
  const [level, setLevel] = useState<UserActivityLevel>('active')
  const lastActiveRef = useRef(Date.now())
  const idleTimerRef = useRef<ReturnType<typeof setTimeout>>()
  const awayTimerRef = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    const onActivity = () => {
      lastActiveRef.current = Date.now()
      setLevel('active')
      clearTimeout(idleTimerRef.current)
      clearTimeout(awayTimerRef.current)
      idleTimerRef.current = setTimeout(() => setLevel('idle'), IDLE_MS)
      awayTimerRef.current = setTimeout(() => setLevel('away'), AWAY_MS)
    }

    window.addEventListener('mousemove', onActivity, { passive: true })
    window.addEventListener('keydown', onActivity, { passive: true })
    window.addEventListener('mousedown', onActivity, { passive: true })
    window.addEventListener('scroll', onActivity, { passive: true })
    window.addEventListener('touchstart', onActivity, { passive: true })

    // Initial timers
    idleTimerRef.current = setTimeout(() => setLevel('idle'), IDLE_MS)
    awayTimerRef.current = setTimeout(() => setLevel('away'), AWAY_MS)

    return () => {
      window.removeEventListener('mousemove', onActivity)
      window.removeEventListener('keydown', onActivity)
      window.removeEventListener('mousedown', onActivity)
      window.removeEventListener('scroll', onActivity)
      window.removeEventListener('touchstart', onActivity)
      clearTimeout(idleTimerRef.current)
      clearTimeout(awayTimerRef.current)
    }
  }, [])

  return level
}

/**
 * Notification queue that respects user activity.
 * - `urgent` notifications: fire immediately
 * - `normal` notifications: queue until user becomes active
 */
export function useNotificationQueue() {
  const activityLevel = useUserActivity()
  const [queue, setQueue] = useState<QueuedNotification[]>([])
  const [active, setActive] = useState<QueuedNotification | null>(null)
  const queueRef = useRef<QueuedNotification[]>([])
  const prevActiveRef = useRef(false)

  const enqueue = useCallback((notif: QueuedNotification) => {
    if (notif.priority === 'urgent') {
      setActive(notif)
      setTimeout(() => setActive(null), 5000)
    } else {
      queueRef.current = [...queueRef.current, notif]
      setQueue([...queueRef.current])
    }
  }, [])

  const dismiss = useCallback(() => {
    setActive(null)
  }, [])

  // When user transitions to active, flush the queue
  useEffect(() => {
    const wasActive = prevActiveRef.current
    prevActiveRef.current = activityLevel === 'active'

    if (activityLevel === 'active' && !wasActive && queueRef.current.length > 0) {
      const next = queueRef.current[0]
      queueRef.current = queueRef.current.slice(1)
      setQueue([...queueRef.current])
      setActive(next)
      setTimeout(() => setActive(null), 5000)
    }
  }, [activityLevel])

  return { active, queue, enqueue, dismiss }
}
