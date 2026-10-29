import { cloudChat, usingCloudAi } from './cloudAiClient'

/**
 * AI 待办自动化引擎 / AI task automation
 *
 * 现状问题：原来的 AI 待办只有一个 `tryAutoCreateTask` —— 把一句话解析成
 * 一个任务（标题+时间+优先级）。那只是「语音转表单」，不是自动化。
 *
 * 本模块提供四个真正有增量的能力：
 *   1. breakdown  把模糊目标拆成可执行子任务（带先后依赖）
 *   2. plan       给一批待办做今日排程（考虑优先级、截止时间、预计耗时）
 *   3. triage     对积压待办做分诊：哪些该做、哪些该丢、哪些该合并
 *   4. nextAction 针对单个任务给出「下一步具体动作」
 *
 * 设计约束：
 *   - 全部返回结构化 JSON，失败时给安全默认值而不是抛错（AI 不稳定不能拖垮 UI）
 *   - 隐私：调用前由调用方判断是否允许上云（这里只管生成）
 *   - 不直接写 store，只返回建议，由 UI 让用户确认后再落地
 */

/* ── 类型 ── */

export interface SubtaskSuggestion {
  text: string
  /** 相对顺序，1 最先做 */
  order: number
  /** 预计耗时（分钟） */
  minutes?: number
  /** 依赖的前置子任务 order，0 表示无依赖 */
  dependsOn?: number
}

export interface BreakdownResult {
  /** 对目标的一句话理解，让用户确认 AI 没理解偏 */
  understanding: string
  subtasks: SubtaskSuggestion[]
  /** 建议的首次可用时间段 */
  suggestedSlot?: string
}

export interface ScheduleSlot {
  todoId: string
  text: string
  /** HH:MM */
  start: string
  end: string
  /** 为什么排这个时间 */
  reason: string
}

export interface PlanResult {
  slots: ScheduleSlot[]
  /** 今天做不完而建议推迟的 */
  deferred: string[]
  note?: string
}

export interface TriageItem {
  todoId: string
  verdict: 'do' | 'defer' | 'drop' | 'merge'
  reason: string
  /** verdict=merge 时，建议合并到哪个 todoId */
  mergeInto?: string
}

export interface NextActionResult {
  action: string
  /** 实施这一步需要什么（工具/信息/人） */
  requires?: string
  /** 预计分钟 */
  minutes?: number
}

/* ── 工具 ── */

/** 从 AI 回复中抽出 JSON（模型常包 ```json 或带前言） */
function extractJson<T>(raw: string): T | null {
  if (!raw) return null
  let s = raw.trim()
  // 去掉 ```json ... ``` 包裹
  const fence = s.match(/```(?:json)?\s*([\s\S]*?)```/i)
  if (fence) s = fence[1].trim()
  // 找第一个 { 或 [
  const startObj = s.indexOf('{')
  const startArr = s.indexOf('[')
  let start = -1
  if (startObj === -1) start = startArr
  else if (startArr === -1) start = startObj
  else start = Math.min(startObj, startArr)
  if (start < 0) return null

  const openCh = s[start]
  const closeCh = openCh === '{' ? '}' : ']'
  let depth = 0
  let end = -1
  for (let i = start; i < s.length; i++) {
    if (s[i] === openCh) depth++
    else if (s[i] === closeCh) {
      depth--
      if (depth === 0) { end = i; break }
    }
  }
  if (end < 0) return null
  try {
    return JSON.parse(s.slice(start, end + 1)) as T
  } catch {
    return null
  }
}

function requireAi(): void {
  if (!usingCloudAi()) {
    throw new Error('未配置云 AI，请到 设置 → AI 配置 填入 API Key')
  }
}

/* ── 1. 目标拆解 ── */

export async function breakdownGoal(
  goal: string,
  opts: { context?: string; maxSubtasks?: number } = {},
): Promise<BreakdownResult> {
  requireAi()
  const max = opts.maxSubtasks ?? 6

  const system = `你是任务规划助手。把用户给的目标拆成最多 ${max} 个可立即执行的子任务。
要求：
- 每个子任务必须是具体动作（动词开头），不要「研究一下」这种模糊描述
- 按执行先后排序
- 估算每个子任务耗时（分钟）
- 只输出 JSON，不要解释`

  const user = `目标：${goal}
${opts.context ? `补充背景：${opts.context}` : ''}

输出格式：
{
  "understanding": "用一句话说明你理解的目标（便于用户校正）",
  "subtasks": [
    { "text": "具体动作", "order": 1, "minutes": 30, "dependsOn": 0 }
  ],
  "suggestedSlot": "建议时段，如 今晚 20:00-21:00"
}`

  const raw = await cloudChat({ system, user, maxTokens: 900 })
  const parsed = extractJson<BreakdownResult>(raw)

  if (!parsed || !Array.isArray(parsed.subtasks) || parsed.subtasks.length === 0) {
    // 兜底：至少把原目标变成一个任务，不让用户白等
    return {
      understanding: goal,
      subtasks: [{ text: goal, order: 1 }],
    }
  }

  return {
    understanding: String(parsed.understanding ?? goal).slice(0, 200),
    subtasks: parsed.subtasks
      .filter(s => s && typeof s.text === 'string' && s.text.trim())
      .slice(0, max)
      .map((s, i) => ({
        text: s.text.trim().slice(0, 120),
        order: typeof s.order === 'number' ? s.order : i + 1,
        minutes: typeof s.minutes === 'number' ? s.minutes : undefined,
        dependsOn: typeof s.dependsOn === 'number' ? s.dependsOn : 0,
      }))
      .sort((a, b) => a.order - b.order),
    suggestedSlot: typeof parsed.suggestedSlot === 'string' ? parsed.suggestedSlot : undefined,
  }
}

/* ── 2. 今日排程 ── */

export interface PlanInput {
  id: string
  text: string
  priority?: number
  dueDate?: number
  category?: string
}

export async function planDay(
  todos: PlanInput[],
  opts: { freeFrom?: string; freeTo?: string; energy?: 'high' | 'mid' | 'low' } = {},
): Promise<PlanResult> {
  requireAi()
  if (todos.length === 0) return { slots: [], deferred: [], note: '没有待排程的任务' }

  const from = opts.freeFrom ?? '09:00'
  const to = opts.freeTo ?? '21:00'

  const list = todos.slice(0, 20).map(t => {
    const due = t.dueDate ? new Date(t.dueDate).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '无'
    return `- id=${t.id} | ${t.text} | 优先级${t.priority ?? 2}(0最高) | 截止:${due}`
  }).join('\n')

  const system = `你是日程规划助手。把待办排进用户可用时段。
原则：
- 高优先级 / 临近截止的排前面
- 同类任务集中处理，减少切换成本
- 预留 30% 缓冲时间，不要把时段排满
- 排不下的放 deferred
只输出 JSON。`

  const user = `可用时段：${from} - ${to}
当前精力状态：${opts.energy ?? 'mid'}

待办列表：
${list}

输出格式：
{
  "slots": [{ "todoId": "id", "text": "任务", "start": "09:00", "end": "09:30", "reason": "为什么排这里" }],
  "deferred": ["排不下而推迟的任务文字"],
  "note": "一句话总结今天的策略"
}`

  const raw = await cloudChat({ system, user, maxTokens: 1000 })
  const parsed = extractJson<PlanResult>(raw)

  if (!parsed || !Array.isArray(parsed.slots)) {
    return { slots: [], deferred: todos.map(t => t.text), note: 'AI 未能生成排程，已全部保留为待办' }
  }

  return {
    slots: parsed.slots.filter(s => s && typeof s.todoId === 'string' && typeof s.start === 'string'),
    deferred: Array.isArray(parsed.deferred) ? parsed.deferred.filter(x => typeof x === 'string') : [],
    note: typeof parsed.note === 'string' ? parsed.note : undefined,
  }
}

/* ── 3. 积压分诊 ── */

export async function triageTodos(todos: PlanInput[]): Promise<TriageItem[]> {
  requireAi()
  if (todos.length === 0) return []

  const list = todos.slice(0, 30).map(t => {
    const age = Math.floor((Date.now() - (t.dueDate ?? Date.now())) / 86400000)
    return `- id=${t.id} | ${t.text} | 优先级${t.priority ?? 2} | 逾期${age > 0 ? age : 0}天`
  }).join('\n')

  const system = `你是任务管理教练。对积压待办做分诊：
- do：确实该做，保留
- defer：有价值但现在不合适，推迟
- drop：没价值/已过时，建议删除
- merge：与另一条重复，建议合并
判断要基于「这件事还有没有意义」，不是简单按优先级排序。
只输出 JSON 数组。`

  const user = `待办列表：
${list}

输出格式（JSON 数组）：
[{ "todoId": "id", "verdict": "do", "reason": "简短理由", "mergeInto": "仅merge时填目标id" }]`

  const raw = await cloudChat({ system, user, maxTokens: 900 })
  const parsed = extractJson<TriageItem[]>(raw)
  if (!Array.isArray(parsed)) return []

  return parsed
    .filter(t => t && typeof t.todoId === 'string' && ['do', 'defer', 'drop', 'merge'].includes(t.verdict))
    .map(t => ({
      todoId: t.todoId,
      verdict: t.verdict,
      reason: String(t.reason ?? '').slice(0, 120),
      mergeInto: t.mergeInto,
    }))
}

/* ── 4. 下一步动作 ── */

export async function suggestNextAction(
  todoText: string,
  opts: { context?: string } = {},
): Promise<NextActionResult | null> {
  requireAi()

  const system = '你是执行教练。针对一个任务，给出「现在就能做的第一个具体动作」。要小到可以在 5 分钟内开始。只输出 JSON。'
  const user = `任务：${todoText}
${opts.context ? `背景：${opts.context}` : ''}

输出格式：
{ "action": "具体动作", "requires": "需要什么", "minutes": 15 }`

  const raw = await cloudChat({ system, user, maxTokens: 300 })
  const parsed = extractJson<NextActionResult>(raw)
  if (!parsed || typeof parsed.action !== 'string') return null

  return {
    action: parsed.action.slice(0, 200),
    requires: typeof parsed.requires === 'string' ? parsed.requires.slice(0, 100) : undefined,
    minutes: typeof parsed.minutes === 'number' ? parsed.minutes : undefined,
  }
}
