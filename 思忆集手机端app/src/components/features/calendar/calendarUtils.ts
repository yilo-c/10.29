import { getDayCellLunarInfo } from '@desk-notes/shared'

export function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
}

export function isToday(date: Date): boolean {
  return isSameDay(date, new Date())
}

export function getMonthDays(year: number, month: number): Date[] {
  const first = new Date(year, month, 1)
  const last = new Date(year, month + 1, 0)
  const startPad = first.getDay()
  const days: Date[] = []
  for (let i = 0; i < startPad; i++) {
    days.push(new Date(year, month, -startPad + i + 1))
  }
  for (let i = 1; i <= last.getDate(); i++) {
    days.push(new Date(year, month, i))
  }
  const remaining = (7 - days.length % 7) % 7
  for (let i = 1; i <= remaining; i++) {
    days.push(new Date(year, month + 1, i))
  }
  return days
}

export interface DayLunarInfo {
  lunarDay: string
  jieQi: string
  festivals: string[]
}

export function getDayLunarInfo(date: Date): DayLunarInfo | null {
  try {
    const info = getDayCellLunarInfo(date)
    if (!info) return null
    return {
      lunarDay: info.lunarDay,
      jieQi: info.jieQi || '',
      festivals: info.festivals || [],
    }
  } catch {
    return null
  }
}

export const WEEKDAY_LABELS = ['日', '一', '二', '三', '四', '五', '六']
