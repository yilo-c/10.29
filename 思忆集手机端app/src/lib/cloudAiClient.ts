import { tokenManager } from './tokenManager'
import { swallowError } from './swallowError'

export interface CloudAiResponse {
  reply: string
  sessionId?: string
}

interface ChatMessage {
  role: string
  content: string
}

interface ChatCompletionRequest {
  model: string
  messages: ChatMessage[]
  stream: boolean
}

/**
 * 把 HTTP 错误翻译成人能看懂的话。
 *
 * 为什么需要：此前一律 `throw new Error(\`Cloud AI ${status}: ${text}\`)`，
 * 用户看到的是 `Cloud AI 429: {"error":{"message":"Rate limit..."}}`
 * 这种技术噪音，既不知道该等还是该改配置，也不知道是自己的问题还是服务商的。
 * 常见情形必须给出可执行的动作。
 */
export function friendlyHttpError(status: number, rawBody: string): string {
  const body = (rawBody || '').slice(0, 200)
  switch (status) {
    case 401:
      return 'API Key 无效或已过期。请到 设置 → AI 配置 检查 Key 是否填错、是否已被撤销。'
    case 402:
      return '账户余额不足。请到服务商控制台充值后重试。'
    case 403:
      return '该 Key 无权访问此模型。请检查「模型名」是否有权限，或换一个有权限的模型。'
    case 404:
      return '接口地址或模型不存在。请检查「API 地址」是否为 .../v1 结尾，以及「模型名」是否拼写正确。'
    case 429:
      return '请求过于频繁或额度用尽，已被服务商限流。请稍等再试。'
    case 500:
    case 502:
    case 503:
    case 504:
      return '服务商暂时不可用（服务端错误）。稍后重试；若持续出现请换服务商。'
    default:
      return `请求失败（HTTP ${status}）：${body || '无详细原因'}`
  }
}

/**
 * 解析实际使用的模型名。
 *
 * 优先级：用户显式配置 > 按 API 地址推断。
 * 用户配置为空时保持旧行为（按地址猜），避免升级后老用户配置失效。
 */
export function resolveModel(baseUrl: string): string {
  const configured = (tokenManager.cloudModel || '').trim()
  if (configured) return configured
  return inferModel(baseUrl)
}

function inferModel(baseUrl: string): string {
  const url = baseUrl.toLowerCase()
  /*
   * 注意：这里以前返回 'deepseek-v4-flash'。
   * 该模型名无法在公开文档中核实（DeepSeek 官方现行名称是 deepseek-chat /
   * deepseek-reasoner 系列），一旦服务商不接受就会返回 400，而界面上
   * 只会显示一句「AI 服务不可用」，用户无从判断是模型名的问题。
   * 故回落值改为 deepseek-chat，并开放「模型名」配置让用户自行指定。
   */
  if (url.includes('deepseek')) return 'deepseek-chat'
  if (url.includes('openai') || url.includes('gpt')) return 'gpt-4o-mini'
  if (url.includes('moonshot') || url.includes('kimi')) return 'moonshot-v1-8k'
  if (url.includes('together')) return 'deepseek-ai/DeepSeek-V3'
  if (url.includes('groq')) return 'llama-3.3-70b-versatile'
  return 'deepseek-chat'
}

/* ── Shared SSE stream parser ── */

export async function readSSEStream(
  body: ReadableStream<Uint8Array> | null,
  onChunk: (text: string) => void,
  onDone: () => void,
): Promise<void> {
  if (!body) {
    onDone()
    return
  }

  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''  // keep incomplete line

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue                       // empty line = event boundary
        if (trimmed === 'data: [DONE]') { onDone(); return }
        if (trimmed.startsWith('data: ')) {
          const json = trimmed.slice(6)
          try {
            const parsed = JSON.parse(json)
            const delta = parsed.choices?.[0]?.delta?.content
            if (delta) onChunk(delta)
          } catch (e) {
            // non-JSON data line — ignore
            swallowError('cloudAiClient:64', e)
          }
        }
        // ignore "event:", "id:", "retry:" lines
      }
    }
  } finally {
    reader.releaseLock()
  }

  onDone()
}

export class CloudAiClient {
  async sendMessage(
    message: string,
    _sessionId?: string,
    overrides?: { apiKey?: string; baseUrl?: string; model?: string },
  ): Promise<CloudAiResponse> {
    const apiKey = overrides?.apiKey ?? tokenManager.cloudApiKey
    const baseUrl = overrides?.baseUrl ?? tokenManager.cloudApiBaseUrl
    const model = overrides?.model ?? resolveModel(baseUrl)

    if (!apiKey) {
      throw new Error('Cloud AI API key not configured')
    }

    const url = `${baseUrl.replace(/\/+$/, '')}/v1/chat/completions`

    const body: ChatCompletionRequest = {
      model,
      messages: [{ role: 'user', content: message }],
      stream: false,
    }

    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${apiKey}`,
      },
      body: JSON.stringify(body),
    })

    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(friendlyHttpError(res.status, text))
    }

    const json = await res.json() as {
      choices?: { message?: { content?: string } }[]
    }
    const reply = json.choices?.[0]?.message?.content ?? ''

    return { reply, sessionId: _sessionId }
  }

  async sendMessageStreaming(
    message: string,
    onChunk: (chunk: string) => void,
    _sessionId?: string,
    overrides?: { apiKey?: string; baseUrl?: string; model?: string },
    /*
     * 取消支持 / Cancellation。
     *
     * 为什么必须支持：此前 7 处 fetch 全部没有 signal，用户发出消息后
     * 只能干等——无法中止、切走页面请求还在跑、组件卸载后回调仍写 state。
     * 移动端网络差时尤其难受：一次卡住的请求会让输入框一直锁着。
     */
    signal?: AbortSignal,
  ): Promise<CloudAiResponse> {
    const apiKey = overrides?.apiKey ?? tokenManager.cloudApiKey
    const baseUrl = overrides?.baseUrl ?? tokenManager.cloudApiBaseUrl
    const model = overrides?.model ?? resolveModel(baseUrl)

    if (!apiKey) {
      throw new Error('Cloud AI API key not configured')
    }

    const url = `${baseUrl.replace(/\/+$/, '')}/v1/chat/completions`

    const body: ChatCompletionRequest = {
      model,
      messages: [{ role: 'user', content: message }],
      stream: true,
    }

    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${apiKey}`,
      },
      body: JSON.stringify(body),
      signal,
    })

    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(friendlyHttpError(res.status, text))
    }

    let fullReply = ''

    await readSSEStream(
      res.body,
      (chunk) => {
        fullReply += chunk
        onChunk(chunk)
      },
      () => {},
    )

    return { reply: fullReply, sessionId: _sessionId }
  }
}

/* ── 笔记 AI 助手专用：带 system prompt 的单次对话 ── */

/**
 * 云端 AI 是否可用。
 *
 * ⚠️ 历史 bug：此前只判断「有没有填 Key」，
 * 于是用户明确关掉「启用云端 AI」开关后，单轮对话确实走本地/排队，
 * 但多步任务（Agent 循环）照样调用 `usingCloudAi()` 通过并上云——
 * **开关对 Agent 路径完全无效**，这是个隐私预期违背：
 * 用户以为关了就是不上云。
 *
 * 现在要求三件事同时成立：
 *   1. 有 Key（或已选 profile 提供 Key）
 *   2. 用户打开了 cloudAiEnabled 开关
 *   3. 没勾「上云前确认」——该选项语义是「每次上云前先问」，
 *      而 Agent 循环是后台多步执行，无法逐次询问，故视为不允许。
 */
export function usingCloudAi(): boolean {
  return !!(tokenManager.cloudApiKey)
    && tokenManager.cloudAiEnabled
    && !tokenManager.promptBeforeCloud
}

export interface ChatOnceArgs {
  system?: string
  user: string
  maxTokens?: number
}

/**
 * 单轮对话，支持 system prompt。
 *
 * 与 sendMessage 的区别：
 *   - sendMessage 只发一条 user 消息，用于聊天页（历史由桌面端维护）
 *   - 这里用于「选中文本 + 动作」的一次性改写，需要 system 约定输出格式
 * 不带流式：笔记改写通常 1-3 秒返回，一次拿全比逐字更省事，
 * 且避免把编辑器 UI 搞得太复杂。
 */
export async function cloudChat(args: ChatOnceArgs): Promise<string> {
  const apiKey = tokenManager.cloudApiKey
  if (!apiKey) throw new Error('未配置云 AI API Key')

  const baseUrl = tokenManager.cloudApiBaseUrl
  const model = resolveModel(baseUrl)
  const url = `${baseUrl.replace(/\/+$/, '')}/v1/chat/completions`

  const messages: ChatMessage[] = []
  if (args.system) messages.push({ role: 'system', content: args.system })
  messages.push({ role: 'user', content: args.user })

  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages,
      stream: false,
      max_tokens: args.maxTokens ?? 1200,
      temperature: 0.7,
    } as ChatCompletionRequest & { max_tokens: number; temperature: number }),
  })

  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(friendlyHttpError(res.status, text))
  }

  const json = await res.json() as { choices?: { message?: { content?: string } }[] }
  return json.choices?.[0]?.message?.content ?? ''
}

/**
 * 多轮对话（带完整历史）—— 笔记编辑页「对话」标签用。
 *
 * 为什么需要单独一个函数：
 *   cloudChat 只能发「一条 system + 一条 user」，每轮都是无状态的独立请求，
 *   模型看不到上一轮说了什么。笔记里追问（"再短一点""换成正式语气"）
 *   必须带历史，否则用户每问一句都要重新交代上下文。
 *
 * 与 cloudChat 的差异仅在 messages 构造：其余 URL/鉴权/模型推断完全一致，
 * 故这里复用同一套逻辑而不另起一套请求代码。
 */
export async function cloudChatMultiTurn(args: {
  system?: string
  messages: { role: 'user' | 'assistant'; content: string }[]
  maxTokens?: number
  temperature?: number
}): Promise<string> {
  const apiKey = tokenManager.cloudApiKey
  if (!apiKey) throw new Error('未配置云 AI API Key')

  const baseUrl = tokenManager.cloudApiBaseUrl
  const model = resolveModel(baseUrl)
  const url = `${baseUrl.replace(/\/+$/, '')}/v1/chat/completions`

  const messages: ChatMessage[] = []
  if (args.system) messages.push({ role: 'system', content: args.system })
  for (const m of args.messages) messages.push({ role: m.role, content: m.content })

  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`,
    },
    body: JSON.stringify({
      model,
      messages,
      stream: false,
      max_tokens: args.maxTokens ?? 1500,
      temperature: args.temperature ?? 0.6,
    } as ChatCompletionRequest & { max_tokens: number; temperature: number }),
  })

  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(friendlyHttpError(res.status, text))
  }

  const json = await res.json() as { choices?: { message?: { content?: string } }[] }
  return json.choices?.[0]?.message?.content ?? ''
}

/* ── Function Calling 支持（多步 Agent 循环的基础） ── */

export interface ToolCall {
  id: string
  type: 'function'
  function: { name: string; arguments: string }
}

export interface AgentMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string | null
  /** assistant 决定调用工具时携带 */
  tool_calls?: ToolCall[]
  /** role=tool 时指向对应的 tool_call id */
  tool_call_id?: string
  /** role=tool 时的函数名，便于日志 */
  name?: string
}

export interface ToolSchema {
  type: 'function'
  function: {
    name: string
    description: string
    parameters: Record<string, unknown> | null
    /**
     * 严格模式（DeepSeek / OpenAI 均支持，Beta）。
     *
     * 开启后模型输出必须严格符合 parameters 的 JSON Schema，
     * 能显著减少"参数缺失/多传字段/类型不对"这类问题 ——
     * 对 tap 这种坐标参数尤其重要（传个字符串 "100" 就会失败）。
     *
     * 注意：开启 strict 后 schema 必须写完整（properties 全部列出
     * 且 required 覆盖所有必填），否则部分端点会直接报错。
     * 因此这里默认不开，由调用方按工具决定。
     */
    strict?: boolean
  }
}

export interface ChatWithToolsResult {
  /** AI 的文本回复（可能为空，若它决定先调工具） */
  content: string
  /** AI 决定调用的工具列表 */
  toolCalls: ToolCall[]
  /** 用于后续轮次继续对话 */
  raw: AgentMessage
}

/**
 * 带工具定义的一轮对话。
 *
 * 与 cloudChat 的区别：这里把 tools schema 一起发给模型，
 * 模型可以选择「直接回答」或「返回 tool_calls 让我去执行」。
 * 这正是 Agent 多步循环的基础：
 *
 *   用户 → AI 决定调 phone.read_screen
 *        → 我们执行，把结果作为 role=tool 消息追加
 *        → 再把完整 messages 发回去，AI 基于结果决定下一步
 *
 * 兼容性：绝大多数 OpenAI 兼容端点（DeepSeek/Moonshot/OpenAI）都支持该字段。
 * 若某端点不支持，会忽略 tools 字段并直接返回 content —— 此时退化为单轮，
 * 不会崩，只是失去多步能力。
 */
export async function chatWithTools(args: {
  messages: AgentMessage[]
  tools: ToolSchema[]
  maxTokens?: number
  temperature?: number
}): Promise<ChatWithToolsResult> {
  const apiKey = tokenManager.cloudApiKey
  if (!apiKey) throw new Error('未配置云 AI API Key')

  const baseUrl = tokenManager.cloudApiBaseUrl
  const model = resolveModel(baseUrl)
  const url = `${baseUrl.replace(/\/+$/, '')}/v1/chat/completions`

  const body: Record<string, unknown> = {
    model,
    messages: args.messages,
    stream: false,
    max_tokens: args.maxTokens ?? 1500,
    temperature: args.temperature ?? 0.3,
  }
  if (args.tools.length > 0) {
    body.tools = args.tools
    body.tool_choice = 'auto'
  }

  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
    body: JSON.stringify(body),
  })

  if (!res.ok) {
    const t = await res.text().catch(() => '')
    throw new Error(friendlyHttpError(res.status, t))
  }

  const json = await res.json() as {
    choices?: Array<{
      message?: {
        content?: string | null
        tool_calls?: ToolCall[]
      }
    }>
  }

  const msg = json.choices?.[0]?.message
  const content = msg?.content ?? ''
  const toolCalls = Array.isArray(msg?.tool_calls) ? msg!.tool_calls! : []

  return {
    content,
    toolCalls,
    raw: {
      role: 'assistant',
      content: content || null,
      ...(toolCalls.length > 0 ? { tool_calls: toolCalls } : {}),
    },
  }
}
