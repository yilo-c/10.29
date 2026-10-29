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
