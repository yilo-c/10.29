import { AI_PROMPTS, type AIProvider, type AIAction, type AIConfig } from './types'

export class LocalAIProvider implements AIProvider {
  private config: AIConfig

  constructor(config: AIConfig) {
    this.config = config
  }

  updateConfig(config: Partial<AIConfig>): void {
    this.config = { ...this.config, ...config }
  }

  getConfig(): AIConfig {
    return { ...this.config }
  }

  // ── Text processing (Ollama NDJSON stream) ──────────────────────────
  async processText(
    action: AIAction,
    text: string,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<string> {
    const prompt = AI_PROMPTS[action]
    const fullPrompt = `${prompt}${text}`

    if (this.config.protocol === 'openai' && this.config.apiKey) {
      return this.streamOpenAI(fullPrompt, onChunk, signal)
    }
    return this.streamOllama(fullPrompt, onChunk, signal)
  }

  async processAction(
    action: AIAction,
    text: string,
    signal?: AbortSignal,
  ): Promise<string> {
    // Local provider doesn't distinguish between pattern.execute and chat.send
    return this.processText(action, text, () => {}, signal)
  }

  private async streamOllama(
    prompt: string,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<string> {
    const res = await fetch(`${this.config.apiUrl}/api/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: this.config.model,
        prompt,
        stream: true,
      }),
      signal,
    })

    if (!res.ok) {
      const errText = await res.text().catch(() => 'Unknown error')
      throw new Error(`API ${res.status}: ${errText}`)
    }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('无法读取响应流')

    const decoder = new TextDecoder()
    let fullText = ''
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.trim()) continue
        try {
          const parsed = JSON.parse(line)
          if (parsed.response) {
            fullText += parsed.response
            onChunk(fullText)
          }
          if (parsed.done) return fullText
        } catch {
          // Partial JSON line, skip
        }
      }
    }

    return fullText
  }

  private async streamOpenAI(
    prompt: string,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<string> {
    const res = await fetch(`${this.config.apiUrl}/v1/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(this.config.apiKey ? { Authorization: `Bearer ${this.config.apiKey}` } : {}),
      },
      body: JSON.stringify({
        model: this.config.model,
        messages: [{ role: 'user', content: prompt }],
        stream: true,
      }),
      signal,
    })

    if (!res.ok) {
      const errText = await res.text().catch(() => 'Unknown error')
      throw new Error(`API ${res.status}: ${errText}`)
    }

    const reader = res.body?.getReader()
    if (!reader) throw new Error('无法读取响应流')

    const decoder = new TextDecoder()
    let fullText = ''
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed || !trimmed.startsWith('data: ')) continue
        const data = trimmed.slice(6)
        if (data === '[DONE]') return fullText

        try {
          const parsed = JSON.parse(data)
          const content = parsed.choices?.[0]?.delta?.content || ''
          if (content) {
            fullText += content
            onChunk(fullText)
          }
        } catch {
          // Partial JSON line, skip
        }
      }
    }

    return fullText
  }

  // ── Tag suggestion ──────────────────────────────────────────────────
  async suggestTags(content: string, existingTags: string[]): Promise<string[]> {
    if (!content.trim()) return []

    // Strip HTML and normalize whitespace
    const plainText = content
      .replace(/<[^>]*>/g, '')
      .replace(/&nbsp;/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()

    if (!plainText) return []

    // Few-shot prompt: explicitly tell the model to produce DIFFERENT tags from existing ones
    const tagHint = existingTags.length > 0
      ? `已有标签: ${existingTags.join('、')}。请从中选择最相关的，也可以补充新标签。`
      : ''

    const prompt = `你是一个标签推荐助手。
根据笔记内容推荐 2-5 个标签。
- 每个标签 2-4 个汉字
- 优先复用已有标签（如果合适）
- 如无合适旧标签再创建议新标签
${tagHint}
仅返回标签，用逗号分隔，不要序号或标点。

笔记：
${plainText.slice(0, 800)}`

    const result = await this.processText('continue' as AIAction, prompt, () => {})
    const tags = result
      .split(/[,，、]/)
      .map(t => t.replace(/[「」"''""\s\n\r]/g, '').trim())
      .filter(Boolean)
      .slice(0, 5)

    // Merge with existing tags, prefer new suggestions first
    const seen = new Set(existingTags.map(t => t.trim()))
    const merged = [...tags.filter(t => !seen.has(t)), ...existingTags.filter(t => tags.includes(t))]
      .slice(0, 5)

    return merged.length > 0 ? merged : []
  }

  // ── Natural language parsing ────────────────────────────────────────
  async parseIntent(input: string): Promise<{
    type: 'create_note' | 'create_todo' | 'query' | 'unknown'
    title?: string
    content?: string
    tags?: string[]
    dueDate?: number
  }> {
    const prompt = `解析以下用户输入，判断用户意图并提取信息。

可能的意图：
- create_note：创建笔记
- create_todo：创建待办事项
- query：查询/提问
- unknown：无法判断

以 JSON 格式返回：
{ "type": "...", "title": "...", "content": "...", "tags": [...], "dueDate": 时间戳或null }

输入：${input}`

    try {
      const result = await this.processText('continue' as AIAction, prompt, () => {})
      const jsonMatch = result.match(/\{[\s\S]*?\}/)
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0])
        return {
          type: parsed.type || 'unknown',
          title: parsed.title,
          content: parsed.content,
          tags: parsed.tags,
          dueDate: parsed.dueDate || undefined,
        }
      }
    } catch {
      // Parsing failed, fall through
    }

    return { type: 'unknown' }
  }

  // ── RAG question answering ──────────────────────────────────────────
  async ask(question: string, context: string[], signal?: AbortSignal): Promise<string> {
    const contextStr = context.length > 0
      ? `以下是相关笔记内容：\n\n${context.join('\n---\n')}`
      : '没有找到相关笔记内容。'

    const prompt = `${contextStr}

基于以上笔记内容，回答以下问题：
${question}

如果笔记内容不足以回答问题，请如实说明，不要编造信息。`

    return this.processText('continue' as AIAction, prompt, () => {}, signal)
  }
}
