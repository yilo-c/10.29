/**
 * AI 决策引擎 — 驱动桌宠自主行为
 *
 * 三层架构中的"本地规则引擎"层 (中频: 0.5-5s 决策周期):
 *   GPT-5.5 (低频决策) → 本地规则引擎 (中频评分) → 骨骼渲染 (60fps 插值)
 *
 * 设计原则:
 *   1. AI 不控制单关节 — 只选择姿态, 本地代码做插值
 *   2. 所有输出经过 JOINT_CONSTRAINTS 验证
 *   3. 纯确定性算法 — 0ms 延迟, 不需要 LLM
 *   4. 可被 GPT-5.5 覆盖 (setMood, suggestPose API)
 *
 * 使用方式:
 *   const engine = createDecisionEngine('playful')
 *   setInterval(() => {
 *     const decision = engine.decide(inputContext)
 *     if (decision.changed) {
 *       requestPose(interpState, decision.pose, decision.transitionSpeed)
 *     }
 *   }, 500) // 每 500ms 决策一次
 */

import {
  type PoseTarget,
  POSE_TARGETS,
} from './boneSystem'
import { validatePoseConstraints } from './poseInterpolator'

// ─── 个性模式 ────────────────────────────────────────────────────

export type PersonalityMode = 'normal' | 'playful' | 'lazy' | 'curious' | 'energetic'

export const MODE_LABELS: Record<PersonalityMode, { label: string; icon: string; color: string }> = {
  normal:    { label: '一般', icon: '😊', color: '#8b949e' },
  playful:   { label: '活泼', icon: '🎮', color: '#79cfff' },
  lazy:      { label: '慵懒', icon: '🦥', color: '#a5d6ff' },
  curious:   { label: '好奇', icon: '🔍', color: '#a78bfa' },
  energetic: { label: '元气', icon: '⚡', color: '#f0888e' },
}

// ─── 输入上下文 ───────────────────────────────────────────────────

export interface DecisionInput {
  /** 用户交互事件 */
  userAction: 'none' | 'click' | 'doubleClick' | 'hover' | 'drag' | 'longPress'
  /** 自上次交互以来的时间 ms */
  timeSinceInteraction: number
  /** 累计空闲帧数 (每 500ms +1) */
  idleFrames: number
  /** 当前小时 (0-23), 用于时段感知 */
  hourOfDay: number
  /** 后端推送的心情 (可选) */
  backendMood?: string
  /** 后端主动建议 (可选) */
  proactiveSuggestion?: { title: string; urgency: number }
  /** 是否正在对话中 */
  isChatting: boolean
  /** 是否有未读通知 */
  hasNotification: boolean
  /** 剪贴板是否有新内容 */
  hasClipboardContent: boolean
}

// ─── 决策输出 ────────────────────────────────────────────────────

export interface Decision {
  /** 选中的姿态 ID */
  pose: string
  /** 姿态是否改变 (用于判断是否需要触发过渡) */
  changed: boolean
  /** 过渡速度 (0-1/ms), 0.002=500ms, 0.01=100ms */
  transitionSpeed: number
  /** 置信度 0-1 */
  confidence: number
  /** 候选姿态 (最多 3 个, 用于调试) */
  candidates: { id: string; label: string; score: number }[]
  /** AI 选择的理由 (调试用) */
  reason: string
}

// ─── 引擎状态 ────────────────────────────────────────────────────

export interface DecisionEngineState {
  /** 个性模式 */
  mode: PersonalityMode
  /** 能量水平 0-1 */
  energy: number
  /** 当前姿态 */
  currentPose: string
  /** 上一姿态 */
  previousPose: string
  /** 冷却时间 (剩余 tick 数) */
  cooldown: number
  /** 上一次用户动作 */
  lastAction: string
  /** 日志 */
  log: string[]
}

// ─── 工厂函数 ────────────────────────────────────────────────────

export function createDecisionEngine(mode: PersonalityMode = 'normal'): DecisionEngineState {
  return {
    mode,
    energy: 0.7,
    currentPose: 'idle',
    previousPose: 'idle',
    cooldown: 0,
    lastAction: 'none',
    log: [],
  }
}

// ─── 主决策函数 ──────────────────────────────────────────────────

/**
 * 每 tick 调用一次 (建议 500ms 间隔)
 *
 * @param state 引擎状态 (会被修改)
 * @param input 当前输入上下文
 * @returns 决策结果
 */
export function decidePose(state: DecisionEngineState, input: DecisionInput): Decision {
  // 冷却递减
  if (state.cooldown > 0) state.cooldown--

  // 能量恢复/消耗
  updateEnergy(state, input)

  // 构建候选列表
  const candidates = buildCandidates(state, input)

  // 排序取最优
  candidates.sort((a, b) => b.score - a.score)
  const best = candidates[0]
  const confidence = best.score / ((candidates[1]?.score ?? 0.1) + 0.01)

  const changed = best.id !== state.currentPose && best.score > 0.3

  if (changed) {
    state.previousPose = state.currentPose
    state.currentPose = best.id
    state.cooldown = 2 // 至少 1 秒冷却
    addLog(state, `${best.icon} ${best.label} (${best.score.toFixed(2)})`)
  }

  return {
    pose: state.currentPose,
    changed,
    transitionSpeed: changed ? computeSpeed(best.energy) : 0,
    confidence: Math.min(1, confidence),
    candidates: candidates.slice(0, 3).map(c => ({
      id: c.id,
      label: c.label,
      score: c.score,
    })),
    reason: `${best.label}: score=${best.score.toFixed(2)}, energy=${state.energy.toFixed(2)}, mode=${state.mode}`,
  }
}

// ─── 能量管理 ────────────────────────────────────────────────────

function updateEnergy(state: DecisionEngineState, input: DecisionInput): void {
  // 基础恢复
  state.energy = Math.min(1, state.energy + 0.003)

  // 交互消耗
  if (input.userAction === 'click') state.energy -= 0.02
  if (input.userAction === 'doubleClick') state.energy -= 0.04
  if (input.userAction === 'drag') state.energy -= 0.06

  // 模式修正
  if (state.mode === 'lazy') state.energy -= 0.001
  if (state.mode === 'energetic') state.energy += 0.002

  // 时段修正: 夜间降低
  if (input.hourOfDay >= 23 || input.hourOfDay < 6) {
    state.energy -= 0.002
  }

  // 对话中保持中等能量
  if (input.isChatting) {
    state.energy = Math.max(0.3, Math.min(0.7, state.energy))
  }

  state.energy = Math.max(0.01, Math.min(1, state.energy))
}

// ─── 候选构建 ────────────────────────────────────────────────────

interface Candidate {
  id: string
  label: string
  icon: string
  energy: number
  score: number
  tags: string[]
}

function buildCandidates(state: DecisionEngineState, input: DecisionInput): Candidate[] {
  const candidates: Candidate[] = []

  for (const [id, pose] of Object.entries(POSE_TARGETS)) {
    let score = 0.10 + Math.random() * 0.08 // 基础噪声

    // ── 用户交互响应 ──
    if (state.lastAction === 'click' || input.userAction === 'click') {
      if (id === 'surprised') score += 1.1
      else if (id === 'curious') score += 0.6
      else if (id === 'happy') score += 0.3
      else if (id === 'idle') score -= 0.2
    }
    if (state.lastAction === 'doubleClick' || input.userAction === 'doubleClick') {
      if (id === 'excited') score += 1.4
      else if (id === 'happy') score += 0.6
      else if (id === 'surprised') score += 0.4
      else if (id === 'idle') score -= 0.3
    }
    if (state.lastAction === 'hover' || input.userAction === 'hover') {
      if (id === 'curious') score += 0.9
      else if (id === 'thinking') score += 0.4
      else if (id === 'idle') score -= 0.1
    }
    if (state.lastAction === 'drag' || input.userAction === 'drag') {
      if (id === 'excited') score += 0.5
      else if (id === 'scared') score += 0.3
    }

    // ── 个性模式权重 ──
    const modeWeights: Record<PersonalityMode, Record<string, number>> = {
      normal:    {},
      playful:   { happy: 0.5, excited: 0.4, curious: 0.3, angry: 0.2, sleepy: -0.5, sad: -0.2 },
      lazy:      { sleepy: 1.2, idle: 0.4, thinking: 0.3, excited: -0.6, angry: -0.5, scared: -0.4 },
      curious:   { curious: 1.1, thinking: 0.7, surprised: 0.4, sleepy: -0.4, angry: -0.2 },
      energetic: { excited: 1.0, happy: 0.6, angry: 0.4, surprised: 0.5, sleepy: -1.0, sad: -0.5, idle: -0.3 },
    }
    const mw = modeWeights[state.mode] || {}
    score += mw[id] ?? 0

    // ── 能量匹配 ──
    if (state.energy < 0.25 && pose.energy > 0.3) score -= 0.6
    if (state.energy < 0.4 && pose.tags.includes('high')) score -= 0.4
    if (state.energy > 0.7 && pose.tags.includes('low')) score -= 0.3
    if (pose.tags.includes('neutral')) {
      // idle 在所有能量水平都可接受
      score += 0.05
    }

    // ── 空闲漂移 ──
    if (input.idleFrames > 400 && input.userAction === 'none') {
      if (id === 'sleepy') score += input.idleFrames > 700 ? 1.4 : 0.5
    }
    if (input.idleFrames > 300 && input.userAction === 'none' && id === 'thinking') {
      score += 0.4
    }

    // ── 时段感知 ──
    if (input.hourOfDay >= 22 || input.hourOfDay < 7) {
      if (id === 'sleepy') score += 0.5
      if (id === 'excited') score -= 0.3
    }
    if (input.hourOfDay >= 7 && input.hourOfDay < 10) {
      if (id === 'excited') score += 0.2 // 早起有精神
      if (id === 'sleepy') score -= 0.3
    }

    // ── 通知响应 ──
    if (input.hasNotification && id === 'curious') score += 0.3
    if (input.hasClipboardContent && id === 'thinking') score += 0.2

    // ── 后台事件 ──
    if (input.backendMood === 'happy' && (id === 'happy' || id === 'excited')) {
      score += 0.5
    }
    if (input.backendMood === 'alert' && id === 'surprised') {
      score += 0.6
    }
    if (input.proactiveSuggestion && id === 'curious') {
      score += input.proactiveSuggestion.urgency * 0.5
    }

    // ── 对话中 ──
    if (input.isChatting) {
      if (id === 'thinking') score += 0.4
      if (id === 'sleepy') score -= 0.5
    }

    // ── 防重复 (连续同一姿态降低得分) ──
    if (id === state.currentPose && !POSE_TARGETS[id]?.loop) {
      score -= 0.7
    }
    if (id === state.previousPose && id !== 'idle') {
      score -= 0.25
    }

    // ── 冷却期降低非 idle 得分 ──
    if (state.cooldown > 0 && id !== 'idle' && id !== state.currentPose) {
      score -= 0.3
    }

    candidates.push({
      id,
      label: pose.label,
      icon: pose.icon,
      energy: pose.energy,
      score,
      tags: pose.tags,
    })
  }

  return candidates
}

// ─── 辅助函数 ────────────────────────────────────────────────────

function computeSpeed(poseEnergy: number): number {
  // 高能量姿态 → 快速过渡, 低能量 → 慢速过渡
  if (poseEnergy > 0.5) return 0.008 // ~125ms
  if (poseEnergy > 0.2) return 0.004 // ~250ms
  return 0.0025 // ~400ms
}

function addLog(state: DecisionEngineState, msg: string): void {
  const now = new Date()
  const ts = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
  state.log.unshift(`[${ts}] ${msg}`)
  if (state.log.length > 30) state.log.pop()
}

// ─── 外部控制 API (供 GPT-5.5 / 后端事件 调用) ───────────────────

/**
 * GPT-5.5 或后端事件可以主动设置心情，
 * 覆盖本地规则引擎的个性模式。
 */
export function setPersonalityMode(state: DecisionEngineState, mode: PersonalityMode): void {
  state.mode = mode
  addLog(state, `模式切换 → ${MODE_LABELS[mode].icon} ${MODE_LABELS[mode].label}`)
}

/**
 * 强制切换到指定姿态 (高优先级事件)
 *
 * 用于: 后端推送通知、用户显式指令、GPT-5.5 决策
 */
export function forcePose(state: DecisionEngineState, poseId: string): string {
  if (!POSE_TARGETS[poseId]) {
    addLog(state, `⚠ 未知姿态: ${poseId}`)
    return state.currentPose
  }

  // 验证约束
  const violations = validatePoseConstraints(POSE_TARGETS[poseId])
  if (violations.length > 0) {
    addLog(state, `⚠ 姿态 ${poseId} 违反约束: ${violations.map(v => `${v.bone}.${v.axis}`).join(', ')}`)
    return state.currentPose
  }

  state.previousPose = state.currentPose
  state.currentPose = poseId
  addLog(state, `🎯 强制姿态: ${POSE_TARGETS[poseId].icon} ${POSE_TARGETS[poseId].label}`)
  return poseId
}

/**
 * 用户交互事件
 *
 * 调用后会影响下一 tick 的决策。
 */
export function handleUserAction(
  state: DecisionEngineState,
  action: DecisionInput['userAction']
): void {
  state.lastAction = action
  // 立即消耗能量
  if (action === 'click') state.energy -= 0.03
  if (action === 'doubleClick') state.energy -= 0.06
  if (action === 'drag') state.energy -= 0.08
  state.energy = Math.max(0.01, state.energy)
  // 交互重置空闲计数
  state.cooldown = 0
}

/**
 * 获取当前姿态的完整参数 (用于渲染)
 */
export function getCurrentPoseTarget(state: DecisionEngineState): PoseTarget {
  return POSE_TARGETS[state.currentPose] ?? POSE_TARGETS.idle
}

/**
 * 获取决策统计信息 (调试用)
 */
export function getDebugInfo(state: DecisionEngineState): {
  mode: string
  energy: number
  currentPose: string
  cooldown: number
  lastAction: string
  recentLog: string[]
} {
  return {
    mode: MODE_LABELS[state.mode].label,
    energy: state.energy,
    currentPose: state.currentPose,
    cooldown: state.cooldown,
    lastAction: state.lastAction,
    recentLog: state.log.slice(0, 10),
  }
}
