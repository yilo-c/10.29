import { swallowError } from './swallowError'
/**
 * Badge number on the app icon (Web Badging API).
 * Falls back gracefully when unsupported.
 */
export async function setAppBadge(count: number): Promise<void> {
  try {
    if (navigator.setAppBadge) {
      await navigator.setAppBadge(count)
    }
  } catch (e) {
    // Not supported — silently ignore
    swallowError('native:10', e)
  }
}

export async function clearAppBadge(): Promise<void> {
  try {
    if (navigator.clearAppBadge) {
      await navigator.clearAppBadge()
    }
  } catch (e) {
    // Not supported
    swallowError('native:20', e)
  }
}

/**
 * Schedule a local notification via the Notification API.
 * Note: this only works while the app is in the foreground or
 * when triggered from a ServiceWorker. For true background
 * notifications, install @capacitor/local-notifications (requires Capacitor 8+).
 *
 * Returns the notification handle or null.
 */
export function sendLocalNotification(title: string, body: string): Notification | null {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    return null
  }
  try {
    return new Notification(title, { body, icon: '/icon-192.png' })
  } catch {
    return null
  }
}

/**
 * Check if the Web Badging API is available.
 */
export function isBadgeSupported(): boolean {
  return 'setAppBadge' in navigator
}
