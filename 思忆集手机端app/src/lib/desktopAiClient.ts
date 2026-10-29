import { readSSEStream } from './cloudAiClient'

export interface DesktopAiResponse {
  reply: string
  sessionId?: string
}

export class DesktopAiClient {
  private baseUrl = ''
  private authToken: string | null = null

  configure(baseUrl: string, token: string | null): void {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
    this.authToken = token
  }

  async sendMessage(message: string, sessionId?: string): Promise<DesktopAiResponse> {
    if (!this.baseUrl) {
      throw new Error('DesktopAiClient not configured')
    }

    const url = `${this.baseUrl}/agent/ask`
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (this.authToken) {
      headers['Authorization'] = `Bearer ${this.authToken}`
    }

    const res = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        message,
        session_id: sessionId ?? null,
      }),
    })

    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`Desktop AI ${res.status}: ${text}`)
    }

    const json = await res.json() as { reply?: string; session_id?: string }
    return {
      reply: json.reply ?? '',
      sessionId: json.session_id,
    }
  }

  async sendMessageStreaming(
    message: string,
    onChunk: (chunk: string) => void,
    sessionId?: string,
  ): Promise<DesktopAiResponse> {
    if (!this.baseUrl) {
      throw new Error('DesktopAiClient not configured')
    }

    const url = `${this.baseUrl}/agent/ask`
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
    }
    if (this.authToken) {
      headers['Authorization'] = `Bearer ${this.authToken}`
    }

    const res = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        message,
        session_id: sessionId ?? null,
        stream: true,
      }),
    })

    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`Desktop AI ${res.status}: ${text}`)
    }

    // Check if response is SSE stream or JSON
    const contentType = res.headers.get('content-type') || ''

    if (contentType.includes('text/event-stream') || contentType.includes('application/x-ndjson')) {
      let fullReply = ''
      await readSSEStream(
        res.body,
        (chunk) => {
          fullReply += chunk
          onChunk(chunk)
        },
        () => {},
      )
      return { reply: fullReply, sessionId }
    }

    // Fallback: non-streaming response read as JSON
    const json = await res.json() as { reply?: string; session_id?: string }
    const reply = json.reply ?? ''
    if (reply) onChunk(reply)
    return { reply, sessionId: json.session_id }
  }
}
