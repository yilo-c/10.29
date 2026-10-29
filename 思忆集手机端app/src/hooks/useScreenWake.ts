import { useRef, useEffect } from 'react'
import { swallowError } from '../lib/swallowError'

/**
 * Keep the device screen from dimming while `active` is true.
 * Releases when the component unmounts or `active` becomes false.
 *
 * Usage: useScreenWake(isEditingOrReading) — the screen stays on
 * while the user is in an active session.
 */
export function useScreenWake(active: boolean) {
  const wakeRef = useRef<WakeLockSentinel | null>(null)

  useEffect(() => {
    if (!active || !navigator.wakeLock) return

    let cancelled = false

    const requestLock = async () => {
      try {
        const lock = await navigator.wakeLock.request('screen')
        if (cancelled) {
          lock.release()
          return
        }
        wakeRef.current = lock
      } catch (e) {
        // Wake lock not supported or denied by system
        swallowError('useScreenWake:26', e)
      }
    }

    requestLock()

    return () => {
      cancelled = true
      wakeRef.current?.release()
      wakeRef.current = null
    }
  }, [active])

  // Re-acquire on visibility change (e.g. switching apps and back)
  useEffect(() => {
    if (!active) return

    const handleVisibility = () => {
      if (document.visibilityState === 'visible' && !wakeRef.current) {
        navigator.wakeLock?.request('screen').then(lock => {
          wakeRef.current = lock
        }).catch(() => {})
      }
    }

    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [active])
}
