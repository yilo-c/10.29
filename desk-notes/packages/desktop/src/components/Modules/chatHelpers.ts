import { useStore } from '../../store/useStore'
import { getTranslations } from '../../i18n'
import type { ModelInfo } from '@desk-notes/shared'

// ── Types ─────────────────────────────────────────────────────────────────

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export interface Conversation {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: ChatMessage[]
}

export type StudySubject = 'politics' | 'english' | 'math' | 'mechanics' | 'agriculture' | null

export interface StudySubjectInfo {
  label: string
  icon: string
  color: string
  prompt: string
}

// ── Helpers ───────────────────────────────────────────────────────────────

export function getConversationTitle(messages: ChatMessage[]): string {
  for (const m of messages) {
    if (m.role === 'user' && m.content.length > 0) {
      return m.content.length > 30 ? m.content.slice(0, 30) + '…' : m.content
    }
  }
  return getTranslations().chatModule.newConversation
}

export function formatTime(ts: number): string {
  const tr = getTranslations().chatModule
  const d = new Date(ts)
  const now = new Date()
  const isToday = d.toDateString() === now.toDateString()
  const yesterday = new Date(now); yesterday.setDate(yesterday.getDate() - 1)
  const isYesterday = d.toDateString() === yesterday.toDateString()
  const hhmm = `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  if (isToday) return tr.timeTodayFmt.replace('{time}', hhmm)
  if (isYesterday) return tr.timeYesterdayFmt.replace('{time}', hhmm)
  return `${d.getMonth() + 1}/${d.getDate()} ${hhmm}`
}

/**
 * Map a backend stream chunk type to a small emoji icon for inline status
 * rendering. Used by the `agentStatus` panel below the streaming bubble.
 */
export function _chunkTypeIcon(type: string): string {
  switch (type) {
    case 'tool_progress':       return '🔧'
    case 'tool_result':         return '✅'
    case 'planner':             return '📋'
    case 'subagent_start':      return '🚀'
    case 'subagent_done':       return '✔️'
    case 'multi_agent_summary': return '🤝'
    case 'error':               return '⚠️'
    default:                    return '•'
  }
}

export function groupByDate(conversations: Conversation[]): { label: string; items: Conversation[] }[] {
  const tr = getTranslations().chatModule
  const now = new Date()
  const today = now.toDateString()
  const yesterday = new Date(now); yesterday.setDate(yesterday.getDate() - 1); const yStr = yesterday.toDateString()
  const thisWeek = new Date(now); thisWeek.setDate(thisWeek.getDate() - thisWeek.getDay()); const wStr = thisWeek.toDateString()
  const thisMonth = new Date(now.getFullYear(), now.getMonth(), 1).toDateString()

  const groups: Record<string, Conversation[]> = { today: [], yesterday: [], thisWeek: [], thisMonth: [], older: [] }
  const labels: Record<string, string> = {
    today: tr.groupToday,
    yesterday: tr.groupYesterday,
    thisWeek: tr.groupThisWeek,
    thisMonth: tr.groupThisMonth,
    older: tr.groupOlder,
  }

  for (const c of conversations) {
    const d = new Date(c.updatedAt).toDateString()
    if (d === today) groups.today.push(c)
    else if (d === yStr) groups.yesterday.push(c)
    else if (d >= wStr) groups.thisWeek.push(c)
    else if (d >= thisMonth) groups.thisMonth.push(c)
    else groups.older.push(c)
  }

  const result: { label: string; items: Conversation[] }[] = []
  for (const key of ['today', 'yesterday', 'thisWeek', 'thisMonth', 'older'] as const) {
    if (groups[key].length > 0) result.push({ label: labels[key], items: groups[key] })
  }
  return result
}

// ── AI Study Presets ────────────────────────────────────────────────────────

export function getStudySubjects(): Record<string, StudySubjectInfo> {
  const tr = getTranslations().chatModule
  return {
    politics: {
      label: tr.subjectPolitics, icon: 'fa-solid fa-flag', color: '#ef4444',
      prompt: tr.promptPolitics,
    },
    english: {
      label: tr.subjectEnglish, icon: 'fa-solid fa-language', color: '#3b82f6',
      prompt: tr.promptEnglish,
    },
    math: {
      label: tr.subjectMath, icon: 'fa-solid fa-square-root-variable', color: '#22c55e',
      prompt: tr.promptMath,
    },
    mechanics: {
      label: tr.subjectMechanics, icon: 'fa-solid fa-gears', color: '#eab308',
      prompt: tr.promptMechanics,
    },
    agriculture: {
      label: tr.subjectAgriculture, icon: 'fa-solid fa-seedling', color: '#a855f7',
      prompt: tr.promptAgriculture,
    },
  }
}

// ── Default model helpers ───────────────────────────────────────────────────
export function usePreferredModel(): ModelInfo | null {
  const provider = useStore(s => s.preferredModelProvider)
  const model = useStore(s => s.preferredModelName)
  if (!provider || !model) return null
  return { provider, model, api_base: '' }
}
