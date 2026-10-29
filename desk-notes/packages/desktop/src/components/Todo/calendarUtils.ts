export function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
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

export function getWeekDays(date: Date): Date[] {
  const day = date.getDay()
  const start = new Date(date)
  start.setDate(start.getDate() - day)
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(start)
    d.setDate(d.getDate() + i)
    return d
  })
}
