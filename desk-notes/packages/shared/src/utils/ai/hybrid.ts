import type { AIProvider, AIAction } from './types'
import { LocalAIProvider } from './local'
import { RuleAIProvider } from './rule'

type HealthStatus = 'unknown' | 'online' | 'offline'

/**
 * HybridAIProvider wraps LocalAIProvider (online) with RuleAIProvider (offline).
 * - Auto-detects network / Ollama availability via lightweight health check
 * - Falls back to rule-based when offline
 * - Caches health status to avoid excessive pings
 */
export class HybridAIProvider implements AIProvider {
  private local: LocalAIProvider
  private rule: RuleAIProvider
  private health: HealthStatus = 'unknown'
  private healthCheckPromise: Promise<HealthStatus> | null = null
  private lastCheck = 0
  private readonly CHECK_INTERVAL = 30_000 // re-check every 30s
  private readonly PING_TIMEOUT = 3000

  constructor(config: import('../../types').AIConfig) {
    this.local = new LocalAIProvider(config)
    this.rule = new RuleAIProvider()
  }

  private async checkHealth(): Promise<HealthStatus> {
    const now = Date.now()
    if (now - this.lastCheck < this.CHECK_INTERVAL && this.health !== 'unknown') {
      return this.health
    }

    if (!navigator.onLine) {
      this.health = 'offline'
      this.lastCheck = now
      return 'offline'
    }

    // Health check in-flight coalescing
    if (this.healthCheckPromise) return this.healthCheckPromise

    this.healthCheckPromise = this.ping().then(status => {
      this.health = status
      this.lastCheck = Date.now()
      this.healthCheckPromise = null
      return status
    }).catch(() => {
      this.health = 'offline'
      this.lastCheck = Date.now()
      this.healthCheckPromise = null
      return 'offline' as HealthStatus
    })

    return this.healthCheckPromise
  }

  private async ping(): Promise<HealthStatus> {
    const config = this.local.getConfig()
    const controller = new AbortController()
    const timer = setTimeout(() => controller.abort(), this.PING_TIMEOUT)

    try {
      const endpoint = config.protocol === 'openai'
        ? `${config.apiUrl.replace(/\/+$/, '')}/v1/models`
        : `${config.apiUrl.replace(/\/+$/, '')}/api/tags`
      const res = await fetch(endpoint, { signal: controller.signal })
      return res.ok ? 'online' : 'offline'
    } catch {
      return 'offline'
    } finally {
      clearTimeout(timer)
    }
  }

  /** Ping remote and return whether AI is reachable */
  async isOnline(): Promise<boolean> {
    return (await this.checkHealth()) === 'online'
  }

  /** Force re-check on next call */
  resetHealth(): void {
    this.health = 'unknown'
    this.lastCheck = 0
  }

  /** Delegate to wrapped LocalAIProvider config */
  getConfig(): import('../../types').AIConfig {
    return this.local.getConfig()
  }

  updateConfig(config: Partial<import('../../types').AIConfig>): void {
    this.local.updateConfig(config)
  }

  private async pick(): Promise<AIProvider> {
    const status = await this.checkHealth()
    return status === 'online' ? this.local : this.rule
  }

  async processText(
    action: AIAction,
    text: string,
    onChunk: (text: string) => void,
    signal?: AbortSignal,
  ): Promise<string> {
    const provider = await this.pick()
    return provider.processText(action, text, onChunk, signal)
  }

  async processAction(
    action: AIAction,
    text: string,
    signal?: AbortSignal,
  ): Promise<string> {
    const provider = await this.pick()
    return provider.processAction(action, text, signal)
  }

  async suggestTags(content: string, existingTags: string[]): Promise<string[]> {
    const provider = await this.pick()
    return provider.suggestTags(content, existingTags)
  }

  async parseIntent(input: string): Promise<{
    type: 'create_note' | 'create_todo' | 'query' | 'unknown'
    title?: string
    content?: string
    tags?: string[]
    dueDate?: number
  }> {
    const provider = await this.pick()
    return provider.parseIntent(input)
  }

  async ask(question: string, context: string[], signal?: AbortSignal): Promise<string> {
    const provider = await this.pick()
    return provider.ask(question, context, signal)
  }
}
