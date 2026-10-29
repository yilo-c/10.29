import type { TodoItem } from '@desk-notes/shared'

export type TemplateKey = 'balanced' | 'focus' | 'daily'
export type CardKey = 'tasks' | 'notes' | 'suggestions' | 'news' | 'translate' | 'inbox'
export type CardSize = 'small' | 'medium' | 'large'
export type DensityKey = 'compact' | 'normal' | 'comfortable'

export interface CardConfig {
  key: CardKey
  icon: string
  title: string
  /** Short label used in dense chips (e.g. "任务" vs "最近任务"). */
  shortTitle: string
  subtitle: string
  color: string
}

export interface LayoutPreset {
  name: string
  template: TemplateKey
  visibility: Record<CardKey, boolean>
  sizes: Record<CardKey, CardSize>
  spans: Record<CardKey, number>
  order: CardKey[]
  density: DensityKey
}

export interface InfoItem {
  title: string
  url?: string
  source?: string
}

export const CARD_CONFIGS: Record<CardKey, CardConfig> = {
  tasks: {
    key: 'tasks', icon: 'fa-regular fa-square-check',
    title: '最近任务', shortTitle: '任务',
    subtitle: '优先查看未完成或今天到期的任务。',
    color: 'yellow',
  },
  notes: {
    key: 'notes', icon: 'fa-regular fa-note-sticky',
    title: '最近笔记', shortTitle: '笔记',
    subtitle: '继续沉淀最近的想法和记录。',
    color: 'green',
  },
  suggestions: {
    key: 'suggestions', icon: 'fa-regular fa-lightbulb',
    title: '智能建议', shortTitle: '建议',
    subtitle: '根据当前上下文推荐值得做的动作。',
    color: 'purple',
  },
  news: {
    key: 'news', icon: 'fa-regular fa-newspaper',
    title: '今日资讯', shortTitle: '资讯',
    subtitle: '技术简报与动态。',
    color: 'blue',
  },
  translate: {
    key: 'translate', icon: 'fa-solid fa-language',
    title: '翻译', shortTitle: '翻译',
    subtitle: '最近查过的表达，沉淀到单词本。',
    color: 'teal',
  },
  inbox: {
    key: 'inbox', icon: 'fa-regular fa-inbox',
    title: '收件箱', shortTitle: '收件箱',
    subtitle: '先收，再整理，再沉淀。',
    color: 'orange',
  },
}

export const ALL_CARDS: CardKey[] = ['tasks', 'notes', 'news', 'suggestions', 'translate', 'inbox']

export const COLOR_MAP: Record<string, { hex: string; dim: string }> = {
  yellow: { hex: '#fbbf24', dim: 'rgba(251,191,36,0.12)' },
  green:  { hex: '#34d399', dim: 'rgba(52,211,153,0.12)' },
  purple: { hex: '#a78bfa', dim: 'rgba(167,139,250,0.12)' },
  blue:   { hex: '#60a5fa', dim: 'rgba(96,165,250,0.12)' },
  teal:   { hex: '#2dd4bf', dim: 'rgba(45,212,191,0.12)' },
  orange: { hex: '#fb923c', dim: 'rgba(251,146,60,0.12)' },
}

export const SIZE_LABELS: Record<CardSize, string> = { small: '小', medium: '中', large: '大' }
export const SIZE_NEXT: Record<CardSize, CardSize> = { small: 'medium', medium: 'large', large: 'small' }

export const DENSITY_CONFIG: Record<DensityKey, { gap: number; padding: string; fontSize: string }> = {
  compact:    { gap: 3, padding: 'p-4', fontSize: 'text-[10px]' },
  normal:     { gap: 4, padding: 'p-5', fontSize: 'text-[11px]' },
  comfortable: { gap: 5, padding: 'p-6', fontSize: 'text-[12px]' },
}

export const LS_KEY = 'desk-notes-dashboard-layout'

/**
 * @deprecated 旧版写死的单条倒计时目标（26考研 2026-12-21）。
 * 已被 localStorage 可配置多条替代，见 DEFAULT_COUNTDOWN_DATE / loadCountdowns。
 */
export const EXAM_DATE = new Date(2026, 11, 21)

// ── 倒计时配置（localStorage 可配置多条）───────────────────────────────
export const COUNTDOWNS_LS_KEY = 'desk-notes-dashboard-countdowns'
/** saveCountdowns 持久化成功后派发的变更事件，供 ExamCountdown 等订阅方刷新。 */
export const COUNTDOWNS_CHANGED_EVENT = 'desk-notes:countdowns-changed'
/** 默认倒计时目标：26考研（2026-12-21），与旧版硬编码行为一致。 */
export const DEFAULT_COUNTDOWN_DATE = new Date(2026, 11, 21).getTime()

export interface CountdownItem {
  id: string
  /** 展示名称，例如「考研」 */
  label: string
  /** 目标日期（毫秒时间戳） */
  date: number
}

export function makeCountdownId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `cd-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`
}

/** 读取倒计时配置；无配置或非法时返回 null（调用方回退默认单条）。 */
export function loadCountdowns(): CountdownItem[] | null {
  try {
    const raw = localStorage.getItem(COUNTDOWNS_LS_KEY)
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return null
    const items = parsed.filter((it): it is CountdownItem =>
      !!it
      && typeof it === 'object'
      && typeof (it as CountdownItem).id === 'string'
      && typeof (it as CountdownItem).label === 'string'
      && typeof (it as CountdownItem).date === 'number'
      && Number.isFinite((it as CountdownItem).date)
    )
    return items.length > 0 ? items : null
  } catch {
    return null
  }
}

/** 持久化倒计时配置，并广播变更事件（供 ExamCountdown 等订阅方刷新）。 */
export function saveCountdowns(items: CountdownItem[]): void {
  try { localStorage.setItem(COUNTDOWNS_LS_KEY, JSON.stringify(items)) } catch { /* ignore */ }
  try {
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent(COUNTDOWNS_CHANGED_EVENT))
    }
  } catch { /* ignore */ }
}

/** Date 毫秒 → <input type="date"> 的 YYYY-MM-DD。 */
export function dateToInputValue(ms: number): string {
  const d = new Date(ms)
  const pad = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** <input type="date"> 的 YYYY-MM-DD → 本地时间毫秒。 */
export function inputValueToDate(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y || 1970, Math.max(0, (m || 1) - 1), d || 1).getTime()
}

// ── 「今天要处理」聚合 ────────────────────────────────────────────────
export interface TodayTasks {
  overdue: TodoItem[]
  dueToday: TodoItem[]
  p0: TodoItem[]
  total: number
}

function startOfToday(): number {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

/**
 * 聚合「今天要处理」任务：逾期（未完成且 dueDate < 今天）→ 今日到期
 * （dueDate == 今天）→ 高优先级（priority===2 且无 dueDate）。
 * 独立于 DashboardCardContents 的 slice(0,5) 排序逻辑；逾期任务不会被新建任务挤出。
 */
export function selectTodayTasks(todos: TodoItem[]): TodayTasks {
  const todayStart = startOfToday()
  const todayEnd = todayStart + 86400000
  const overdue: TodoItem[] = []
  const dueToday: TodoItem[] = []
  const p0: TodoItem[] = []
  for (const t of todos) {
    if (t.done) continue
    if (typeof t.dueDate === 'number' && Number.isFinite(t.dueDate)) {
      if (t.dueDate < todayStart) overdue.push(t)
      else if (t.dueDate < todayEnd) dueToday.push(t)
    } else if (t.priority === 2) {
      p0.push(t)
    }
  }
  const byDueThenCreated = (a: TodoItem, b: TodoItem): number =>
    (a.dueDate ?? 0) - (b.dueDate ?? 0) || (b.createdAt || 0) - (a.createdAt || 0)
  overdue.sort(byDueThenCreated)
  dueToday.sort(byDueThenCreated)
  p0.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
  return { overdue, dueToday, p0, total: overdue.length + dueToday.length + p0.length }
}

export const TEMPLATES: { key: TemplateKey; label: string; desc: string }[] = [
  { key: 'balanced', label: '均衡', desc: '平衡展示所有卡片' },
  { key: 'focus', label: '专注', desc: '任务与建议优先' },
  { key: 'daily', label: '日常', desc: '今日待办与建议置顶' },
]
