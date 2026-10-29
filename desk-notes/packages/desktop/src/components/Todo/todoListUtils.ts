import type { TodoItem } from '../../types'

export type SortMode = 'created' | 'priority' | 'dueDate' | 'alpha'

export function cyclePriority(current: 0 | 1 | 2 | undefined): 0 | 1 | 2 | undefined {
  if (current === undefined) return 0
  if (current === 0) return 1
  if (current === 1) return 2
  return undefined
}

export function formatDueDate(ts: number): string {
  const d = new Date(ts)
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  // Show time if the timestamp has non-midnight time components
  const hasTime = d.getHours() !== 0 || d.getMinutes() !== 0 || d.getSeconds() !== 0
  if (hasTime) {
    const h = String(d.getHours()).padStart(2, '0')
    const min = String(d.getMinutes()).padStart(2, '0')
    return `${month}/${day} ${h}:${min}`
  }
  return `${month}/${day}`
}

export function tsToDateInputValue(ts: number): string {
  const d = new Date(ts)
  // Use datetime-local format so time isn't lost when editing
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const min = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day}T${h}:${min}`
}

export function calculateDefaultReminder(dueDate: number | undefined): number | undefined {
  if (!dueDate) return undefined
  const d = new Date(dueDate)
  d.setHours(9, 0, 0, 0)
  return d.getTime()
}

export function isOverdue(dueDate: number): boolean {
  return dueDate < Date.now()
}

export function isDueSoon(dueDate: number): boolean {
  const now = Date.now()
  return dueDate > now && dueDate - now < 24 * 60 * 60 * 1000
}

export function getSortFn(mode: SortMode, asc: boolean = true): (a: TodoItem, b: TodoItem) => number {
  const d = asc ? 1 : -1
  switch (mode) {
    case 'priority':
      return (a, b) => {
        const pa = a.priority ?? 99
        const pb = b.priority ?? 99
        if (pa !== pb) return (pa - pb) * d
        return ((a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt)) * d
      }
    case 'dueDate':
      return (a, b) => {
        if (a.dueDate && b.dueDate) return (a.dueDate - b.dueDate) * d
        if (a.dueDate) return asc ? -1 : 1
        if (b.dueDate) return asc ? 1 : -1
        return ((a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt)) * d
      }
    case 'alpha':
      return (a, b) => a.text.localeCompare(b.text) * d
    case 'created':
    default:
      return (a, b) => ((a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt)) * d
  }
}
