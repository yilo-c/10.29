import { useEffect, useRef } from 'react'
import { useStore } from '@desk-notes/shared'
import { setAppBadge, clearAppBadge } from '../lib/native'

/**
 * Keep the app icon badge in sync with pending reminders.
 * Updates whenever todos change.
 */
export function useBadge() {
  const todos = useStore(s => s.todos)
  const lastCount = useRef(0)

  useEffect(() => {
    const now = Date.now()
    const pending = todos.filter(t => {
      if (t.done) return false
      if (!t.reminderTime) return false
      return t.reminderTime > now
    }).length

    if (pending === lastCount.current) return
    lastCount.current = pending

    if (pending > 0) {
      setAppBadge(pending)
    } else {
      clearAppBadge()
    }
  }, [todos])
}
