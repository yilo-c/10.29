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
      /*
       * 后端 `daily_get` 返回 `{note: {...}, date}` 信封，
       * 正文在 `note.content` 里。原代码直接读 `obj.content`，
       * 于是日记正文永远是空串 —— 界面看起来像「今天还没写」，
       * 实际内容一直存在后端。
       */
      const note = (obj.note && typeof obj.note === 'object')
        ? obj.note as Record<string, unknown>
        : null

      // 兼容少数直接返回扁平结构的分支
      const content = typeof note?.content === 'string'
        ? note.content
        : (typeof obj.content === 'string' ? obj.content : '')
      const updated = note?.updated_at ?? obj.updated_at

      return {
        date: typeof obj.date === 'string' ? obj.date : toIsoDate(date),
        content,
        updated_at: typeof updated === 'string' || typeof updated === 'number'
          ? updated as string | number
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
