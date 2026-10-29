import { useEffect, useRef } from 'react'
import { useStore, getAIProvider, DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL, type ProactiveSuggestion } from '@desk-notes/shared'

const IDLE_THRESHOLD_MS = 5 * 60 * 1000 // 5 minutes
const SUGGESTION_INTERVAL_MS = 60 * 1000  // Re-evaluate every minute

function getTodayStr(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/** Generate simple rule-based suggestions from current app state */
function generateSuggestions(): ProactiveSuggestion[] {
  const state = useStore.getState()
  const items: ProactiveSuggestion[] = []
  const now = Date.now()

  // Pending tasks
  const pendingTodos = state.todos.filter(t => !t.done)
  const overdue = pendingTodos.filter(t => t.dueDate && t.dueDate < now)
  if (overdue.length > 0) {
    items.push({
      id: `sug-overdue-${now}`,
      icon: 'fa-regular fa-clock',
      title: `你有 ${overdue.length} 个逾期任务`,
      description: `最早逾期于 ${new Date(Math.min(...overdue.map(t => t.dueDate!))).toLocaleDateString('zh-CN')}`,
      actionType: 'navigate',
      actionPayload: 'todo',
      timestamp: now,
      priority: 80,
    })
  }

  if (pendingTodos.length > 0) {
    items.push({
      id: `sug-pending-${now}`,
      icon: 'fa-regular fa-square-check',
      title: `${pendingTodos.length} 个待办任务`,
      description: '还有任务未完成，去查看',
      actionType: 'navigate',
      actionPayload: 'todo',
      timestamp: now,
      priority: 60,
    })
  }

  // Unread info brief (if data available)
  if (state.infoBrief && state.infoTopics.length > 0) {
    items.push({
      id: `sug-info-${now}`,
      icon: 'fa-regular fa-newspaper',
      title: '今日资讯简报',
      description: `关注领域：${state.infoTopics.slice(0, 3).join('、')}`,
      actionType: 'navigate',
      actionPayload: 'info',
      timestamp: now,
      priority: 50,
    })
  }

  // Recent notes without tags
  const untagged = state.floatingNotes.filter(n => !n.archived && n.content && (!n.tags || n.tags.length === 0))
  if (untagged.length > 2 && state.aiEnabled) {
    items.push({
      id: `sug-tags-${now}`,
      icon: 'fa-regular fa-tag',
      title: `${untagged.length} 条笔记尚未添加标签`,
      description: '启用 AI 自动标签或手动整理',
      actionType: 'navigate',
      actionPayload: 'notes',
      timestamp: now,
      priority: 30,
    })
  }

  return items
}

// ── Idle / morning-briefing tracked globally so the hook is idempotent ──
let _eventAttached = false
let _lastActivity = Date.now()

function onUserActivity() {
  _lastActivity = Date.now()
}

function getMillisSinceActivity(): number {
  return Date.now() - _lastActivity
}

function attachGlobalEvents() {
  if (_eventAttached) return
  _eventAttached = true
  const events = ['mousemove', 'mousedown', 'keydown', 'touchstart', 'scroll', 'wheel']
  const handler = () => onUserActivity()
  for (const ev of events) {
    window.addEventListener(ev, handler, { passive: true })
  }
}

export interface ProactiveEngineState {
  isIdle: boolean
  idleMinutes: number
  todayFirstVisit: boolean
}

/**
 * Hook that drives the proactive AI assistant:
 * - Tracks user idle time
 * - Detects first visit of the day (morning briefing trigger)
 * - Generates contextual suggestions
 * - Cleans up event listeners on unmount
 */
export function useProactiveEngine(): ProactiveEngineState {
  const proactiveEnabled = useStore(s => s.proactiveEnabled)
  const setProactiveSuggestions = useStore(s => s.setProactiveSuggestions)
  const setProactiveBrief = useStore(s => s.setProactiveBrief)
  const setProactiveBriefLoading = useStore(s => s.setProactiveBriefLoading)
  const proactiveBriefDate = useStore(s => s.proactiveBriefDate)
  const setProactiveBriefDate = useStore(s => s.setProactiveBriefDate)
  const latestBriefDateRef = useRef(proactiveBriefDate)
  latestBriefDateRef.current = proactiveBriefDate

  const isIdleRef = useRef(false)

  // Attach global activity listeners once
  useEffect(() => {
    attachGlobalEvents()
  }, [])

  // Idle detection
  useEffect(() => {
    if (!proactiveEnabled) return

    const tick = () => {
      const idleMs = getMillisSinceActivity()
      const nowIdle = idleMs >= IDLE_THRESHOLD_MS
      if (nowIdle !== isIdleRef.current) {
        isIdleRef.current = nowIdle
      }
    }

    const interval = setInterval(tick, 5000)
    return () => clearInterval(interval)
  }, [proactiveEnabled])

  // Morning briefing: detect first visit today
  const todayStr = getTodayStr()
  const isFirstVisitToday = proactiveBriefDate !== todayStr

  // Generate morning briefing on first visit
  useEffect(() => {
    if (!proactiveEnabled || !isFirstVisitToday) return
    const state = useStore.getState()
    if (!state.aiEnabled) return
    // Only generate if we haven't already started loading
    if (state.proactiveBriefLoading) return

    setProactiveBriefLoading(true)

    const pending = state.todos.filter(t => !t.done)
    const notesCount = state.floatingNotes.filter(n => !n.archived).length
    const hour = new Date().getHours()

    const timeGreeting =
      hour < 6 ? '凌晨' : hour < 9 ? '早上' : hour < 12 ? '上午' : hour < 14 ? '中午' : hour < 18 ? '下午' : '晚上'

    const prompt = `你是一个智能助理，请用中文生成一段简短的晨间简报（100 字以内），包含以下信息：

当前时间：${timeGreeting}
未完成任务数：${pending.length} 个
笔记总数：${notesCount} 篇
关注领域：${state.infoTopics.join('、')}

要求：
- 以问候开头
- 根据 ${timeGreeting} 给出合适的祝福
- 简要提示今日待办状态
- 语气自然温暖，不要过于机械
- 返回纯文本（不需要 Markdown）`

    getAIProvider({ enabled: true, apiUrl: DEFAULT_LOCAL_API_URL, model: DEFAULT_LOCAL_MODEL, protocol: 'ollama', autoTagging: false }).ask(prompt, []).then(brief => {
      setProactiveBrief(brief || `${timeGreeting}好！今天有 ${pending.length} 个待办任务等待处理，加油！`)
      setProactiveBriefDate(todayStr)
    }).catch(() => {
      // Fallback briefing on error
      setProactiveBrief(`${timeGreeting}好！今天有 ${pending.length} 个待办任务等待处理，加油！`)
      setProactiveBriefDate(todayStr)
    }).finally(() => {
      setProactiveBriefLoading(false)
    })
  }, [proactiveEnabled, isFirstVisitToday, todayStr, setProactiveBrief, setProactiveBriefDate, setProactiveBriefLoading])

  // Periodic suggestion generation
  useEffect(() => {
    if (!proactiveEnabled) return

    const generate = () => {
      const suggestions = generateSuggestions()
      setProactiveSuggestions(suggestions)
    }

    generate()
    const interval = setInterval(generate, SUGGESTION_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [proactiveEnabled, setProactiveSuggestions])

  return {
    isIdle: isIdleRef.current,
    idleMinutes: Math.floor(getMillisSinceActivity() / 60000),
    todayFirstVisit: isFirstVisitToday,
  }
}
