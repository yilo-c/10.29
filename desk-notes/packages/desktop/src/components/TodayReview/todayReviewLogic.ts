/**
 * V1-6「今日回顾」纯逻辑层：时间过滤 + 聚合分组。
 *
 * 与 UI / RPC 解耦，全部函数接受可注入的 now 基准（避免时间炸弹），供 vitest 直接覆盖。
 * 数据源：后端 info.items.list（资讯捕获）+ notes.list（笔记），均为只读聚合。
 */

export interface InfoItemRaw {
  id?: string | number
  title?: string
  title_zh?: string
  summary?: string
  summary_zh?: string
  category?: string
  source?: string
  source_label?: string
  url?: string
  collected_at?: string | number
  created_at?: string | number
  read?: boolean
  saved?: boolean
}

export interface NoteRaw {
  id?: string | number
  title?: string
  content?: string
  text?: string
  tags?: string[]
  category?: string
  updated_at?: string | number
  created_at?: string | number
  deleted_at?: string | number | null
}

export type ReviewEntryKind = 'info' | 'note'

export interface ReviewEntry {
  key: string
  kind: ReviewEntryKind
  id: string
  title: string
  summary: string
  category?: string
  ts: number
  url?: string
}

export interface CategoryCount {
  category: string
  count: number
}

export interface DayReview {
  dateKey: string
  entries: ReviewEntry[]
  captureCount: number
  noteCount: number
}

/** 秒级 / 毫秒级 epoch 分界（< 1e10 视为秒） */
const SECOND_EPOCH_MAX = 10_000_000_000

const pad2 = (n: number): string => String(n).padStart(2, '0')

/**
 * 把后端可能返回的多种时间表示（秒 / 毫秒 epoch、ISO 字符串、纯数字字符串）
 * 统一为毫秒时间戳；无法解析时返回 undefined（调用方据此丢弃条目）。
 */
export function toTimestamp(value?: string | number | null): number | undefined {
  if (value === undefined || value === null || value === '') return undefined
  if (typeof value === 'number') {
    if (!Number.isFinite(value) || value <= 0) return undefined
    return value < SECOND_EPOCH_MAX ? value * 1000 : value
  }
  const raw = value.trim()
  if (/^\d{9,13}$/.test(raw)) {
    const n = Number(raw)
    return n < SECOND_EPOCH_MAX ? n * 1000 : n
  }
  const parsed = Date.parse(raw)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined
}

/** 目标日（相对今天的偏移，0=今天、-1=昨天）在本地时区的起点毫秒。 */
export function startOfLocalDay(dayOffset: number, now: Date = new Date()): number {
  return new Date(now.getFullYear(), now.getMonth(), now.getDate() + dayOffset).getTime()
}

/** 时间戳是否落在目标本地日 [start, start+1day) 内。 */
export function isWithinLocalDay(ts: number, dayOffset: number, now: Date = new Date()): boolean {
  if (!Number.isFinite(ts)) return false
  return ts >= startOfLocalDay(dayOffset, now) && ts < startOfLocalDay(dayOffset + 1, now)
}

/** YYYY-MM-DD（本地时区） */
export function localDateKey(ts: number): string {
  const d = new Date(ts)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/**
 * 时间过滤：按注入的时间取值器筛出目标本地日的条目。
 * 后端 info.items.list 无时间范围参数，故「取 top N 后前端按 date 过滤」的降级在此实现。
 */
export function filterByLocalDay<T>(
  items: readonly T[],
  tsOf: (item: T) => number | undefined,
  dayOffset: number = 0,
  now: Date = new Date(),
): T[] {
  return items.filter((item) => {
    const ts = tsOf(item)
    return ts !== undefined && isWithinLocalDay(ts, dayOffset, now)
  })
}

export function infoItemTimestamp(item: InfoItemRaw): number | undefined {
  return toTimestamp(item.collected_at) ?? toTimestamp(item.created_at)
}

export function noteTimestamp(note: NoteRaw): number | undefined {
  return toTimestamp(note.updated_at) ?? toTimestamp(note.created_at)
}

const stripInlineMarkdown = (s: string): string =>
  s.replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[#>*_`~-]+/g, ' ').replace(/\s+/g, ' ').trim()

export function truncate(text: string, max: number): string {
  const s = text.trim()
  return s.length <= max ? s : `${s.slice(0, max)}…`
}

/** 单条资讯 → 回顾条目（标题优先中文译名，摘要优先中文摘要） */
export function toInfoEntry(item: InfoItemRaw): ReviewEntry | null {
  const ts = infoItemTimestamp(item)
  if (ts === undefined) return null
  const title = (item.title_zh || item.title || '').trim()
  const summary = (item.summary_zh || item.summary || '').trim()
  if (!title && !summary) return null
  return {
    key: `info:${String(item.id ?? title ?? ts)}`,
    kind: 'info',
    id: String(item.id ?? ''),
    title: title || truncate(summary, 40),
    summary,
    category: item.category,
    ts,
    url: item.url,
  }
}

/** 单条笔记 → 回顾条目（无标题时取正文首行） */
export function toNoteEntry(note: NoteRaw): ReviewEntry | null {
  if (note.deleted_at) return null
  const ts = noteTimestamp(note)
  if (ts === undefined) return null
  const raw = note.content || note.text || ''
  const body = stripInlineMarkdown(raw)
  const title = (note.title || '').trim() || truncate(stripInlineMarkdown(raw.split('\n')[0] || ''), 40)
  if (!title && !body) return null
  return {
    key: `note:${String(note.id ?? title ?? ts)}`,
    kind: 'note',
    id: String(note.id ?? ''),
    title,
    summary: truncate(body, 160),
    category: note.category,
    ts,
  }
}

/** 类目分布（按 count 降序，同数按首次出现顺序）。 */
export function countCategories(entries: readonly ReviewEntry[]): CategoryCount[] {
  const order: string[] = []
  const map = new Map<string, number>()
  for (const e of entries) {
    const cat = (e.category || 'general').trim() || 'general'
    if (!map.has(cat)) {
      map.set(cat, 0)
      order.push(cat)
    }
    map.set(cat, (map.get(cat) ?? 0) + 1)
  }
  return order
    .map((category) => ({ category, count: map.get(category) ?? 0 }))
    .sort((a, b) => b.count - a.count || order.indexOf(a.category) - order.indexOf(b.category))
}

/** 活跃类目 top N（默认 3，统计条展示用）。 */
export function topCategories(entries: readonly ReviewEntry[], limit: number = 3): CategoryCount[] {
  return countCategories(entries).slice(0, Math.max(0, limit))
}

/** 聚合入口：筛出目标本地日的资讯 + 笔记，按时间倒序合并，并给出统计。 */
export function aggregateDayReview(input: {
  infoItems?: readonly InfoItemRaw[]
  notes?: readonly NoteRaw[]
  dayOffset?: number
  now?: Date
}): DayReview {
  const { dayOffset = 0, now = new Date() } = input
  const dayStart = startOfLocalDay(dayOffset, now)

  const infoEntries = filterByLocalDay(input.infoItems ?? [], infoItemTimestamp, dayOffset, now)
    .map(toInfoEntry)
    .filter((e): e is ReviewEntry => e !== null)
  const noteEntries = filterByLocalDay(input.notes ?? [], noteTimestamp, dayOffset, now)
    .map(toNoteEntry)
    .filter((e): e is ReviewEntry => e !== null)

  const entries = [...infoEntries, ...noteEntries].sort((a, b) => b.ts - a.ts)

  return {
    dateKey: localDateKey(dayStart),
    entries,
    captureCount: infoEntries.length,
    noteCount: noteEntries.length,
  }
}
