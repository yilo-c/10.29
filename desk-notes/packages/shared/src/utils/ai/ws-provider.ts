/* WebSocket AI Provider — connects to 思忆集 Python backend via JSON-RPC. */
import type { AIConfig } from '../../types'
import { AI_PROMPTS, type AIProvider, type AIAction } from './types'
import type { StreamChunkType } from '../../ws-client/types'

/** Maps frontend AIAction to backend pattern.execute action_id */
const ACTION_TO_PATTERN_ID: Partial<Record<AIAction, string>> = {
  summarize: 'summarize',
  translate_zh: 'translate',
  translate_en: 'translate_en',
  polish: 'polish',
  continue: 'continue',
  organize: 'organize',
}
import { LocalAIProvider } from './local'
import { WsClient } from '../../ws-client/ws-client'

/**
 * AIProvider that routes requests through the 思忆集 Python backend.
 * Falls back to LocalAIProvider when the WebSocket is disconnected.
 */
export class WsAIProvider implements AIProvider {
  private _config: AIConfig
  private _client: WsClient
  private _localFallback: LocalAIProvider
  private _connected = false
  private _unsubStatus?: () => void

  /** Preferred provider name (e.g. "deepseek") for backend routing */
  preferredProvider?: string
  /** Preferred model name (e.g. "deepseek-chat") for backend routing */
  preferredModel?: string
  /** Extra params merged into the RPC call (e.g. context_messages for compression) */
  extraParams?: Record<string, unknown>

  constructor(config: AIConfig, client?: WsClient) {
    this._config = { ...config }
    this._client = client ?? new WsClient()
    this._localFallback = new LocalAIProvider(config)

    // Track connection status
    this._connected = this._client.status === 'connected'
    this._unsubStatus = this._client.onStatusChange((status) => {
      this._connected = status === 'connected'
    })

    // Start connecting
    this._client.connect()
  }

  getConfig(): AIConfig {
    return { ...this._config }
  }

  updateConfig(config: Partial<AIConfig>): void {
    this._config = { ...this._config, ...config }
    this._localFallback.updateConfig(config)
  }

  /** Release the onStatusChange listener. Call when discarding this instance. */
  dispose(): void {
    this._unsubStatus?.()
    this._unsubStatus = undefined
  }

  private _isConnected(): boolean {
    return this._connected && this._client.status === 'connected'
  }

  async processText(
    action: AIAction,
    text: string,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
    onTypedChunk?: (type: StreamChunkType, data: string) => void,
  ): Promise<string> {
    // Build a prompt from action + text, matching the backend's chat.send
    const message = _buildActionPrompt(action, text)

    if (!this._isConnected()) {
      return this._localFallback.processText(action, text, onChunk, signal)
    }

    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')

    let full = ''
    try {
      const params: Record<string, unknown> = { message }
      if (this.preferredProvider) params.provider = this.preferredProvider
      if (this.preferredModel) params.model = this.preferredModel
      if (this.extraParams) Object.assign(params, this.extraParams)
      for await (const chunk of this._client.stream<string>('chat.send', params, { onTypedChunk })) {
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
        full += chunk
        onChunk(full)
      }
    } catch (err) {
      // User-initiated abort — don't fall back, just propagate
      if (err instanceof DOMException && err.name === 'AbortError') throw err
      console.warn('[WsAIProvider] stream failed, falling back to local:', err)
      return this._localFallback.processText(action, text, onChunk, signal)
    }

    // Backend returned empty — fall back to local AI
    if (!full) {
      console.warn('[WsAIProvider] empty response from backend, falling back to local AI')
      const localResult = await this._localFallback.processText(action, text, onChunk, signal)
      if (!localResult) throw new Error('AI 服务暂不可用（后端和本地均无 AI 可用）')
      return localResult
    }

    return full
  }

  async processAction(
    action: AIAction,
    text: string,
    signal?: AbortSignal,
  ): Promise<string> {
    const patternId = ACTION_TO_PATTERN_ID[action]
    if (!patternId) {
      // Fallback: no mapping for this action, use processText
      return this.processText(action, text, () => {}, signal)
    }

    if (!this._isConnected()) {
      return this._localFallback.processAction(action, text, signal)
    }

    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')

    try {
      const result = await this._client.call<{ result: string; error?: string }>('pattern.execute', { action_id: patternId, text })
      if (result?.result) {
        return result.result
      }
      // Backend returned empty (or error) — log and fall back to local AI
      if (result?.error) {
        console.warn('[WsAIProvider] pattern.execute returned error:', result.error)
      }
    } catch (err) {
      console.warn('[WsAIProvider] pattern.execute failed, falling back to local:', err)
      return this._localFallback.processAction(action, text, signal)
    }

    // Empty result — fall back to local AI
    console.warn('[WsAIProvider] empty response from pattern.execute, falling back to local AI')
    return this._localFallback.processAction(action, text, signal)
  }

  async suggestTags(content: string, existingTags: string[]): Promise<string[]> {
    if (!this._isConnected()) {
      return this._localFallback.suggestTags(content, existingTags)
    }
    try {
      const result = await this._client.call<string[]>('notes.auto_tag', { content })
      return Array.isArray(result) ? result : []
    } catch {
      return this._localFallback.suggestTags(content, existingTags)
    }
  }

  async parseIntent(input: string) {
    if (!this._isConnected()) {
      return this._localFallback.parseIntent(input)
    }
    try {
      return await this._client.call<{ type: 'create_note' | 'create_todo' | 'query' | 'unknown'; title?: string; content?: string; tags?: string[]; dueDate?: number }>('ai.parse_intent', { text: input })
    } catch {
      return this._localFallback.parseIntent(input)
    }
  }

  async ask(question: string, context: string[], signal?: AbortSignal): Promise<string> {
    const message = `基于以下上下文回答问题：\n\n${context.join('\n---\n')}\n\n问题：${question}`

    if (!this._isConnected()) {
      return this._localFallback.ask(question, context, signal)
    }

    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')

    let full = ''
    try {
      const params: Record<string, unknown> = { message }
      if (this.preferredProvider) params.provider = this.preferredProvider
      if (this.preferredModel) params.model = this.preferredModel
      if (this.extraParams) Object.assign(params, this.extraParams)
      for await (const chunk of this._client.stream<string>('chat.send', params)) {
        if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
        full += chunk
      }
    } catch {
      return this._localFallback.ask(question, context, signal)
    }

    // Backend returned empty — fall back to local AI
    if (!full) {
      return this._localFallback.ask(question, context, signal)
    }

    return full
  }
}

function _buildActionPrompt(action: AIAction, text: string): string {
  const prefix = AI_PROMPTS[action] ?? ''
  return `${prefix}${text}`
}
