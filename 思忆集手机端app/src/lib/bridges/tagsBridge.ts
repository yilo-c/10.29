/**
 * 标签桥 — 从桌面端 `tags.list` 同步全局标签集合并提供缓存。
 *
 * 缓存策略：
 *   - 内存 LRU + localStorage，TTL 默认 5 分钟；
 *   - `refresh()` 强制刷新；`getCachedTags()` 同步返回旧值（无则返回空）；
 *   - 桌面端如果通过 `tags.changed` 事件推送，这里也接受外部 invalidate。
 */
import { callRpc } from '../rpcClient'

const STORAGE_KEY = 'siyi_mobile_tags_cache_v1'
const DEFAULT_TTL_MS = 5 * 60_000

interface TagsCacheBlob {
  tags: string[]
  fetchedAt: number
}

let memoryCache: TagsCacheBlob | null = null

function readPersisted(): TagsCacheBlob | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as TagsCacheBlob
    if (Array.isArray(parsed.tags) && typeof parsed.fetchedAt === 'number') return parsed
    return null
  } catch {
    return null
  }
}

function writePersisted(blob: TagsCacheBlob): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(blob)) } catch { /* ignore */ }
}

function loadCache(): TagsCacheBlob | null {
  if (memoryCache) return memoryCache
  memoryCache = readPersisted()
  return memoryCache
}

export function getCachedTags(): string[] {
  return loadCache()?.tags ?? []
}

export function invalidateTagsCache(): void {
  memoryCache = null
  try { localStorage.removeItem(STORAGE_KEY) } catch { /* ignore */ }
}

export async function listTags(opts?: { ttlMs?: number; force?: boolean }): Promise<string[]> {
  const ttl = opts?.ttlMs ?? DEFAULT_TTL_MS
  const cache = loadCache()
  if (!opts?.force && cache && Date.now() - cache.fetchedAt < ttl) {
    return cache.tags
  }
  try {
    const res = await callRpc<unknown>('tags.list', {})
    let tags: string[] = []
    if (Array.isArray(res)) {
      tags = res.filter((x): x is string => typeof x === 'string')
    } else if (res && typeof res === 'object' && Array.isArray((res as { tags?: unknown[] }).tags)) {
      tags = (res as { tags: unknown[] }).tags.filter((x): x is string => typeof x === 'string')
    }
    const blob: TagsCacheBlob = { tags, fetchedAt: Date.now() }
    memoryCache = blob
    writePersisted(blob)
    return tags
  } catch {
    return cache?.tags ?? []
  }
}
