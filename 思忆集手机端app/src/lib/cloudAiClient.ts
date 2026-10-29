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
