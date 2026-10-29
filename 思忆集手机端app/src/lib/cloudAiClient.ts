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

function inferModel(baseUrl: string): string {
  const url = baseUrl.toLowerCase()
  // deepseek-chat / deepseek-reasoner 已于 2026-07-24 退役，现用 deepseek-v4-flash
  if (url.includes('deepseek')) return 'deepseek-v4-flash'
  if (url.includes('openai') || url.includes('gpt')) return 'gpt-4o-mini'
  if (url.includes('moonshot') || url.includes('kimi')) return 'moonshot-v1-8k'
  if (url.includes('together')) return 'deepseek-ai/DeepSeek-V3'
  if (url.includes('groq')) return 'llama-3.3-70b-versatile'
  return 'deepseek-v4-flash'
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
    const model = overrides?.model ?? inferModel(baseUrl)

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
      throw new Error(`Cloud AI ${res.status}: ${text}`)
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
  ): Promise<CloudAiResponse> {
    const apiKey = overrides?.apiKey ?? tokenManager.cloudApiKey
    const baseUrl = overrides?.baseUrl ?? tokenManager.cloudApiBaseUrl
    const model = overrides?.model ?? inferModel(baseUrl)

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
    })

    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`Cloud AI ${res.status}: ${text}`)
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

/** 是否已配置云 AI（有 key） */
export function usingCloudAi(): boolean {
  return !!tokenManager.cloudApiKey
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
  const model = inferModel(baseUrl)
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
    throw new Error(`AI ${res.status}: ${text.slice(0, 120)}`)
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
  const model = inferModel(baseUrl)
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
    throw new Error(`AI ${res.status}: ${t.slice(0, 150)}`)
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
