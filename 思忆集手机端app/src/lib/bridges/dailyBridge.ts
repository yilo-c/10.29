/**
 * 日记桥 — 桌面 `daily.get` / `daily.append` RPC 的薄封装。
 *
 * 桌面端 daily 是 markdown 段落串行追加，这里同样保持纯文本接口，让前端组件
 * 自行决定渲染（reference 视图、回顾视图等）。
 */
import { callRpc } from '../rpcClient'

export interface DailyEntry {
  date: string
  content: string
  updated_at?: string | number
}

function toIsoDate(date: string | Date | undefined): string {
  if (!date) return new Date().toISOString().slice(0, 10)
  if (date instanceof Date) return date.toISOString().slice(0, 10)
  // 容忍 'YYYY-MM-DD' / ISO string
  if (/^\d{4}-\d{2}-\d{2}/.test(date)) return date.slice(0, 10)
  const parsed = Date.parse(date)
  if (!Number.isNaN(parsed)) return new Date(parsed).toISOString().slice(0, 10)
  return new Date().toISOString().slice(0, 10)
}

export async function getDaily(date?: string | Date): Promise<DailyEntry | null> {
  try {
    const res = await callRpc<unknown>('daily.get', { date: toIsoDate(date) })
    if (typeof res === 'string') return { date: toIsoDate(date), content: res }
    if (res && typeof res === 'object') {
      const obj = res as Record<string, unknown>
      return {
        date: typeof obj.date === 'string' ? obj.date : toIsoDate(date),
        content: typeof obj.content === 'string' ? obj.content : '',
        updated_at: typeof obj.updated_at === 'string' || typeof obj.updated_at === 'number'
          ? obj.updated_at as string | number
          : undefined,
      }
    }
    return null
  } catch {
    return null
  }
}

export async function appendDaily(content: string, date?: string | Date): Promise<boolean> {
  if (!content.trim()) return false
  try {
    await callRpc('daily.append', { date: toIsoDate(date), content })
    return true
  } catch {
    return false
  }
}
