/**
 * LRU cache for translate.full results. Avoids re-translating identical text.
 */

interface CacheEntry<T> {
  value: T
  ts: number
}

const MAX_SIZE = 200
const MAX_AGE_MS = 30 * 60 * 1000 // 30 min

export class TranslationCache<T = unknown> {
  private map = new Map<string, CacheEntry<T>>()

  private buildKey(text: string, source: string, target: string, mode: string): string {
    return `${source}|${target}|${mode}|${text.trim().toLowerCase()}`
  }

  get(text: string, source: string, target: string, mode: string): T | undefined {
    const key = this.buildKey(text, source, target, mode)
    const entry = this.map.get(key)
    if (!entry) return undefined
    if (Date.now() - entry.ts > MAX_AGE_MS) {
      this.map.delete(key)
      return undefined
    }
    // Move to end (LRU)
    this.map.delete(key)
    this.map.set(key, entry)
    return entry.value
  }

  set(text: string, source: string, target: string, mode: string, value: T): void {
    const key = this.buildKey(text, source, target, mode)
    // Evict oldest if at capacity
    if (this.map.size >= MAX_SIZE) {
      const first = this.map.keys().next()
      if (!first.done) this.map.delete(first.value)
    }
    this.map.set(key, { value, ts: Date.now() })
  }
}
