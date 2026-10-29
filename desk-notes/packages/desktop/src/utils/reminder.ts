/**
 * Reminder sync module.
 *
 * Subscribes to Zustand store changes and sends the current todos
 * to the Electron main process for due-date notification scheduling.
 * In browser (dev) mode this is a no-op.
 */

import { useStore } from '../store/useStore'

let lastSentJson = ''

/**
 * Start syncing todos to the main process for reminder scheduling.
 * Returns a cleanup function to unsubscribe.
 */
export function startReminderSync(): () => void {
  const ei = (globalThis as { electronAPI?: ElectronAPI | undefined }).electronAPI
  if (!ei?.updateReminders) {
    // Not in Electron — no-op
    return () => {}
  }

  const sendTodos = () => {
    const todos = useStore.getState().todos
    const json = JSON.stringify(todos)
    if (json === lastSentJson) return
    lastSentJson = json
    ei.updateReminders(todos as unknown as Array<Record<string, unknown>>)
  }

  // Send immediately, then subscribe to changes
  sendTodos()
  const unsub = useStore.subscribe(sendTodos)

  return unsub
}
