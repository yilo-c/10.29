const CACHE_KEY = 'siyi_ai_cache'
const PENDING_KEY = 'siyi_ai_pending'
const CACHE_TTL = 7 * 24 * 60 * 60 * 1000 // 7 days

interface CachedEntry {
  query: string
  response: string
  timestamp: number
}

interface PendingRequest {
  message: string
  sessionId: string | null
  target: string
  timestamp: number
}

function sha256(msg: string): string {
  let h = 0x811c9dc5
  const enc = new TextEncoder().encode(msg)
  for (const b of enc) {
    h ^= b
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

function loadCache(): CachedEntry[] {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveCache(entries: CachedEntry[]): void {
  localStorage.setItem(CACHE_KEY, JSON.stringify(entries))
}

function loadPending(): PendingRequest[] {
  try {
    const raw = localStorage.getItem(PENDING_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function savePending(entries: PendingRequest[]): void {
  localStorage.setItem(PENDING_KEY, JSON.stringify(entries))
}

export class OfflineAiFallback {
  private cache: CachedEntry[] = loadCache()
  private pendingQueue: PendingRequest[] = loadPending()

  async tryCached(query: string): Promise<{ response: string; timestamp: number } | null> {
    const key = sha256(query.trim().toLowerCase())
    const now = Date.now()

    // Evict expired entries while searching
    this.cache = this.cache.filter(entry => {
      if (now - entry.timestamp > CACHE_TTL) return false
      return true
    })

    const match = this.cache.find(entry => {
      const entryKey = sha256(entry.query.trim().toLowerCase())
      return entryKey === key
    })

    if (match) {
      return { response: match.response, timestamp: match.timestamp }
    }

    return null
  }

  cacheResponse(query: string, responseText: string): void {
    const now = Date.now()

    // Remove oldest entry if cache is too large (500 limit)
    if (this.cache.length >= 500) {
      this.cache.sort((a, b) => a.timestamp - b.timestamp)
      this.cache = this.cache.slice(-499)
    }

    this.cache.push({ query, response: responseText, timestamp: now })
    saveCache(this.cache)
  }

  enqueueForRetry(message: string, sessionId: string | null, target: string): void {
    this.pendingQueue.push({ message, sessionId, target, timestamp: Date.now() })
    savePending(this.pendingQueue)
  }

  getPendingCount(): number {
    return this.pendingQueue.length
  }

  async retryPending(retryHandler: (msg: string, sessionId: string | null) => Promise<boolean>): Promise<void> {
    const remaining: PendingRequest[] = []

    for (const req of this.pendingQueue) {
      try {
        const ok = await retryHandler(req.message, req.sessionId)
        if (!ok) {
          // Keep for next retry if less than 1 hour old
          if (Date.now() - req.timestamp < 3600000) {
            remaining.push(req)
          }
        }
      } catch {
        if (Date.now() - req.timestamp < 3600000) {
          remaining.push(req)
        }
      }
    }

    this.pendingQueue = remaining
    savePending(this.pendingQueue)
  }

  clearCache(): void {
    this.cache = []
    this.pendingQueue = []
    localStorage.removeItem(CACHE_KEY)
    localStorage.removeItem(PENDING_KEY)
  }
}
