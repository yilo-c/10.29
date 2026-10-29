import { useState, useEffect, useCallback, useRef } from 'react'
import { useStore } from '@desk-notes/shared'
import { LocalNotifications } from '@capacitor/local-notifications'
import { Capacitor } from '@capacitor/core'
import { playAlarm, stopAlarm } from '../lib/alarm'
import SiyiNative from '../lib/native/siyiNative'
import type { TodoItem } from '@desk-notes/shared'

interface ActiveReminder {
  todo: TodoItem
  isRinging: boolean
}

const FOREGROUND_INTERVAL_MS = 10_000
const BACKGROUND_INTERVAL_MS = 60_000

function showNotification(title: string, body: string) {
  if ('Notification' in window && Notification.permission === 'granted') {
    new Notification(title, { body, icon: '/icon-192.png' })
  }
}

/** 为单个待办生成稳定的本地通知 ID。 */
function notificationId(todoId: string | number): number {
  const str = String(todoId)
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    hash = (hash * 31 + str.charCodeAt(i)) >>> 0
  }
  return hash || 1
}

async function ensureNotificationPermission() {
  if (!Capacitor.isNativePlatform()) return true
  const { display } = await LocalNotifications.checkPermissions()
  if (display === 'granted') return true
  const req = await LocalNotifications.requestPermissions()
  return req.display === 'granted'
}

async function scheduleLocalReminder(todo: TodoItem) {
  if (!Capacitor.isNativePlatform()) return
  const time = todo.reminderTime
  if (!time || time <= Date.now()) return
  const granted = await ensureNotificationPermission()
  if (!granted) return
  try {
    await LocalNotifications.schedule({
      notifications: [
        {
          id: notificationId(todo.id),
          title: '思忆便签',
          body: todo.text || '待办提醒',
          schedule: { at: new Date(time) },
          sound: 'default',
          smallIcon: 'ic_stat_icon_config_sample',
          actionTypeId: '',
          extra: { todoId: todo.id },
        },
      ],
    })
  } catch (e) {
    console.warn('[reminder] schedule local notification failed', e)
  }
}

async function cancelLocalReminder(todoId: string | number) {
  if (!Capacitor.isNativePlatform()) return
  try {
    await LocalNotifications.cancel({ notifications: [{ id: notificationId(todoId) }] })
  } catch (e) {
    console.warn('[reminder] cancel local notification failed', e)
  }
}

async function openSystemAlarmIfAvailable(todo: TodoItem) {
  if (!Capacitor.isNativePlatform()) return
  const time = todo.reminderTime
  if (!time || time <= Date.now()) return
  try {
    const d = new Date(time)
    await SiyiNative.openSystemAlarm({
      hour: d.getHours(),
      minute: d.getMinutes(),
      message: todo.text || '待办提醒',
    })
  } catch (e) {
    // 系统闹钟是可选增强，失败时静默忽略
    console.warn('[reminder] open system alarm failed', e)
  }
}

export function useReminderScheduler() {
  const todos = useStore(s => s.todos)
  const todosRef = useRef(todos)
  todosRef.current = todos
  const snoozeReminder = useStore(s => s.snoozeReminder)
  const clearReminder = useStore(s => s.clearReminder)
  const [activeReminder, setActiveReminder] = useState<ActiveReminder | null>(null)
  const activeReminderRef = useRef<ActiveReminder | null>(null)
  activeReminderRef.current = activeReminder
  const firedIdsRef = useRef<Set<string>>(new Set())

  // 当待办提醒时间变化时，同步到本地通知与系统闹钟。
  useEffect(() => {
    const validReminders = todos.filter(t => !t.done && t.reminderTime && t.reminderTime > Date.now())
    const validIds = new Set(validReminders.map(t => String(t.id)))

    for (const todo of validReminders) {
      scheduleLocalReminder(todo)
      // 可选：同时打开系统自带闹钟（每个提醒一个）。
      // 若用户反馈闹钟列表过多，可改为仅在设置开启时调用。
      openSystemAlarmIfAvailable(todo)
    }

    // 取消已删除 / 已完成 / 提醒时间被清除的通知
    for (const todo of todos) {
      if (!validIds.has(String(todo.id))) {
        cancelLocalReminder(todo.id)
      }
    }
  }, [todos])

  const checkReminders = useCallback(() => {
    if (activeReminderRef.current?.isRinging) return

    const now = Date.now()
    const fired = firedIdsRef.current
    const dueList = todosRef.current
      .filter(t => {
        if (!t.reminderTime || t.done) return false
        if (fired.has(String(t.id))) return false
        const effectiveTime = t.reminderSnoozedUntil && t.reminderSnoozedUntil > now
          ? t.reminderSnoozedUntil
          : t.reminderTime
        return effectiveTime <= now
      })
      .sort((a, b) => (a.reminderTime ?? 0) - (b.reminderTime ?? 0))

    const due = dueList[0]
    if (due) {
      fired.add(String(due.id))
      showNotification('思忆便签', due.text)
      playAlarm()
      setActiveReminder({ todo: due, isRinging: true })
    }
  }, [])

  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null

    const startTimer = (intervalMs: number) => {
      if (timer) clearInterval(timer)
      timer = setInterval(checkReminders, intervalMs)
    }

    const onVisibilityChange = () => {
      if (typeof document === 'undefined') return
      startTimer(document.hidden ? BACKGROUND_INTERVAL_MS : FOREGROUND_INTERVAL_MS)
      if (!document.hidden) checkReminders()
    }

    startTimer(
      typeof document !== 'undefined' && document.hidden
        ? BACKGROUND_INTERVAL_MS
        : FOREGROUND_INTERVAL_MS,
    )
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVisibilityChange)
    }

    return () => {
      if (timer) clearInterval(timer)
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVisibilityChange)
      }
    }
  }, [checkReminders])

  const snooze = useCallback((minutes: number) => {
    const active = activeReminderRef.current
    if (!active) return
    stopAlarm()
    firedIdsRef.current.delete(String(active.todo.id))
    snoozeReminder(active.todo.id, minutes)
    setActiveReminder(null)
  }, [snoozeReminder])

  const dismiss = useCallback(() => {
    const active = activeReminderRef.current
    if (!active) return
    stopAlarm()
    clearReminder(active.todo.id)
    setActiveReminder(null)
  }, [clearReminder])

  useEffect(() => {
    return () => stopAlarm()
  }, [])

  return { activeReminder, snooze, dismiss }
}
