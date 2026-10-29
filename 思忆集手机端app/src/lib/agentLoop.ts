/**
 * Agent 多步循环 / Multi-step agent loop
 *
 * 为什么需要它：
 *   原来的链路是「匹配工具 → 执行一次 → 结束」，
 *   只能完成单步动作（如"记一条笔记"）。
 *   但真实任务几乎都是多步的，例如：
 *
 *     「看看屏幕上有什么，然后点第二个按钮」
 *       → read_screen
 *       → 基于结果判断坐标
 *       → tap
 *       → read_screen 确认
 *
 *   这个模块把「执行一次」变成「循环执行直到完成」。
 *
 * 设计要点：
 *  1. 每轮都由模型决定下一步（调工具 or 给最终答案），不做硬编码流程
 *  2. 工具结果以 role=tool 回灌，模型能看到真实返回值再决策
 *  3. 有步数上限与总超时，防止模型陷入死循环
 *  4. 高风险工具（R3）可要求用户确认，确认前不执行
 *  5. 每步都产出 trace，UI 可以展示「AI 在做什么」，而不是干等
 */

import {
  chatWithTools,
  usingCloudAi,
  type AgentMessage,
  type ToolSchema,
} from './cloudAiClient'
import { ToolRegistry, type AiTool, type RiskLevel } from './toolRegistry'

/* ── 配置 ── */

export const AGENT_LIMITS = {
  /** 最多几步（含工具调用）。10 步足够覆盖绝大多数手机操作任务 */
  maxSteps: 10,
  /** 总超时，防止单步卡死拖垮整个任务 */
  totalTimeoutMs: 120_000,
  /** 单步工具超时 */
  stepTimeoutMs: 20_000,
}

/* ── 轨迹（UI 展示用） ── */

export interface AgentStepTrace {
  index: number
  type: 'tool_call' | 'tool_result' | 'final' | 'error' | 'rejected'
  toolId?: string
  /** 工具入参（已脱敏展示） */
  args?: Record<string, unknown>
  /** 工具返回值摘要 */
  result?: string
  /** 模型这一步的文字说明 */
  text?: string
  /** 耗时 */
  ms?: number
}

export interface AgentRunResult {
  /** 最终回答 */
  text: string
  /** 是否成功走到最终答案（false = 超步数/超时/出错） */
  completed: boolean
  /** 全程轨迹 */
  trace: AgentStepTrace[]
  /** 用到的工具 id 列表 */
  usedTools: string[]
  /** 失败原因 */
  error?: string
  /** 实际步数 */
  steps: number
}

/* ── 审批钩子 ── */

export interface ApprovalDecision {
  approved: boolean
  /** 用户拒绝时的原因 */
  reason?: string
}

export interface AgentRunOptions {
  /** 历史对话（可选，用于多轮上下文） */
  history?: AgentMessage[]
  /** 额外上下文（如用户状态摘要） */
  systemContext?: string
  /** 高风险工具的审批钩子；不传则 R3 一律执行（默认行为，向后兼容） */
  requestApproval?: (info: {
    tool: AiTool
    args: Record<string, unknown>
    callId: string
  }) => Promise<ApprovalDecision>
  /** 每步回调，UI 实时展示进度 */
  onStep?: (trace: AgentStepTrace) => void
  /** 允许使用的最少风险等级（如设为 R2 则 R3 工具不可用） */
  maxRiskLevel?: RiskLevel
  /** 覆盖默认上限 */
  maxSteps?: number
}

/* ── 风险等级排序 ── */

const RISK_ORDER: Record<RiskLevel, number> = {
  R0_INFO: 0,
  R1_LIGHT: 1,
  R2_DRAFT: 2,
  R3_MODIFY: 3,
}

function exceedsRisk(tool: AiTool, max?: RiskLevel): boolean {
  if (!max) return false
  return RISK_ORDER[tool.riskLevel] > RISK_ORDER[max]
}

/* ── 工具 → OpenAI schema ── */

function toSchema(tool: AiTool): ToolSchema {
  return {
    type: 'function',
    function: {
      // OpenAI 的 function name 只允许 a-zA-Z0-9_-，工具 id 里的 . 要替换
      name: tool.id.replace(/\./g, '__'),
      description: `${tool.description}（风险等级 ${tool.riskLevel}）`,
      parameters: tool.parameters ?? { type: 'object', properties: {} },
    },
  }
}

/** 从 schema name 还原工具 id */
function buildNameMap(tools: AiTool[]): Map<string, AiTool> {
  const m = new Map<string, AiTool>()
  for (const t of tools) m.set(t.id.replace(/\./g, '__'), t)
  return m
}

/* ── 带超时的执行 ── */

function withTimeout<T>(p: Promise<T>, ms: number, label: string): Promise<T> {
  return Promise.race([
    p,
    new Promise<T>((_, rej) => setTimeout(() => rej(new Error(`${label} 超时（${ms}ms）`)), ms)),
  ])
}

/* ── 主循环 ── */

const SYSTEM_PROMPT = `你是一个手机助手，可以直接操作这台手机。

工作方式：
1. 需要了解屏幕内容时，先调用 phone__read_screen，不要凭空猜测。
2. 操作屏幕后，再次 read_screen 确认结果是否符合预期。
3. 多步任务逐步推进：每轮只做一件事，看到结果再决定下一步。
4. 任务完成后，用简洁的中文告诉用户你做了什么、结果如何。

安全边界（必须遵守）：
- 涉及付款、删除数据、发送消息、修改账号等不可逆操作，
  先用文字说明你打算做什么，等用户确认后再调用工具。
- 不确定的地方不要猜，先读屏或询问用户。
- 不要为了"完成任务"而跳过确认步骤。`

export async function runAgent(
  userInput: string,
  registry: ToolRegistry,
  opts: AgentRunOptions = {},
): Promise<AgentRunResult> {
  const trace: AgentStepTrace[] = []
  const usedTools: string[] = []
  const startedAt = Date.now()
  const maxSteps = opts.maxSteps ?? AGENT_LIMITS.maxSteps

  if (!usingCloudAi()) {
    return {
      text: '',
      completed: false,
      trace,
      usedTools,
      steps: 0,
      error: '未配置云 AI，无法运行多步任务。请到 设置 → AI 配置 填入 API Key。',
    }
  }

  // 可用工具（按风险上限过滤）
  const tools = registry.getAllTools().filter(t => !exceedsRisk(t, opts.maxRiskLevel))
  if (tools.length === 0) {
    return { text: '', completed: false, trace, usedTools, steps: 0, error: '没有可用的工具' }
  }

  const nameMap = buildNameMap(tools)
  const schemas = tools.map(toSchema)

  const messages: AgentMessage[] = [
    {
      role: 'system',
      content: SYSTEM_PROMPT + (opts.systemContext ? `\n\n【当前状态】\n${opts.systemContext}` : ''),
    },
    ...(opts.history ?? []),
    { role: 'user', content: userInput },
  ]

  let steps = 0

  while (steps < maxSteps) {
    steps++

    // 总超时保护
    if (Date.now() - startedAt > AGENT_LIMITS.totalTimeoutMs) {
      const t: AgentStepTrace = { index: steps, type: 'error', text: '任务总时长超限，已中止' }
      trace.push(t); opts.onStep?.(t)
      return {
        text: '任务耗时过长，已中止。可以让我继续完成剩余部分。',
        completed: false, trace, usedTools, steps,
        error: 'total timeout',
      }
    }

    let round
    try {
      round = await withTimeout(
        chatWithTools({ messages, tools: schemas }),
        60_000,
        'AI 决策',
      )
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'AI 调用失败'
      const t: AgentStepTrace = { index: steps, type: 'error', text: msg }
      trace.push(t); opts.onStep?.(t)
      return { text: '', completed: false, trace, usedTools, steps, error: msg }
    }

    // 模型没有要求调工具 → 这是最终答案
    if (round.toolCalls.length === 0) {
      const text = round.content?.trim() || '（模型未返回内容）'
      const t: AgentStepTrace = { index: steps, type: 'final', text }
      trace.push(t); opts.onStep?.(t)
      return { text, completed: true, trace, usedTools, steps }
    }

    // 记录 assistant 这一步（含 tool_calls 声明），必须回灌给模型
    messages.push(round.raw)

    if (round.content?.trim()) {
      const t: AgentStepTrace = { index: steps, type: 'tool_call', text: round.content.trim() }
      trace.push(t); opts.onStep?.(t)
    }

    // 依次执行本轮所有工具调用（模型可能一次要调多个）
    for (const call of round.toolCalls) {
      const tool = nameMap.get(call.function.name)

      if (!tool) {
        const errText = `未知工具：${call.function.name}`
        messages.push({
          role: 'tool', tool_call_id: call.id, name: call.function.name, content: errText,
        })
        const t: AgentStepTrace = { index: steps, type: 'error', toolId: call.function.name, text: errText }
        trace.push(t); opts.onStep?.(t)
        continue
      }

      // 解析入参
      let args: Record<string, unknown> = {}
      try {
        args = call.function.arguments ? JSON.parse(call.function.arguments) : {}
      } catch {
        const errText = '参数不是合法 JSON'
        messages.push({ role: 'tool', tool_call_id: call.id, name: call.function.name, content: errText })
        const t: AgentStepTrace = { index: steps, type: 'error', toolId: tool.id, text: errText }
        trace.push(t); opts.onStep?.(t)
        continue
      }

      // R3 审批
      if (tool.riskLevel === 'R3_MODIFY' && opts.requestApproval) {
        let decision: ApprovalDecision = { approved: false, reason: '未授权' }
        try {
          decision = await opts.requestApproval({ tool, args, callId: call.id })
        } catch {
          decision = { approved: false, reason: '审批界面异常' }
        }
        if (!decision.approved) {
          const rejText = `用户拒绝执行该操作${decision.reason ? `：${decision.reason}` : ''}`
          messages.push({ role: 'tool', tool_call_id: call.id, name: call.function.name, content: rejText })
          const t: AgentStepTrace = {
            index: steps, type: 'rejected', toolId: tool.id, args, text: rejText,
          }
          trace.push(t); opts.onStep?.(t)
          continue
        }
      }

      // 执行
      const toolStart = Date.now()
      let resultText: string
      try {
        const r = await withTimeout(
          tool.execute(args),
          AGENT_LIMITS.stepTimeoutMs,
          `工具 ${tool.id}`,
        )
        resultText = r.success ? (r.data ?? '完成') : `执行失败：${r.error ?? '未知错误'}`
      } catch (e) {
        resultText = `执行异常：${e instanceof Error ? e.message : String(e)}`
      }
      const ms = Date.now() - toolStart

      if (!usedTools.includes(tool.id)) usedTools.push(tool.id)

      messages.push({
        role: 'tool',
        tool_call_id: call.id,
        name: call.function.name,
        content: resultText.slice(0, 4000),
      })

      const t: AgentStepTrace = {
        index: steps, type: 'tool_result', toolId: tool.id, args,
        result: resultText.slice(0, 500), ms,
      }
      trace.push(t); opts.onStep?.(t)
    }
  }

  // 达到步数上限
  return {
    text: `已达到最大步数（${maxSteps}），任务可能未完全完成。可以让我继续。`,
    completed: false,
    trace,
    usedTools,
    steps,
    error: 'max steps reached',
  }
}

/** 判断一句话是否像需要多步操作的任务（用于决定是否走 Agent 循环） */
export function looksLikeMultiStepTask(text: string): boolean {
  const t = text.toLowerCase()
  // 含序数/递进词，或同时提到"操作 + 确认"类动作
  const stepMarkers = ['然后', '接着', '再', '之后', '并且', '顺便', '第一步', '第二步']
  const actionVerbs = ['打开', '点', '滑动', '返回', '输入', '搜索', '启动', '找到', '看看']
  const hasStep = stepMarkers.some(m => t.includes(m))
  const actionCount = actionVerbs.filter(v => t.includes(v)).length
  return hasStep || actionCount >= 2
}
