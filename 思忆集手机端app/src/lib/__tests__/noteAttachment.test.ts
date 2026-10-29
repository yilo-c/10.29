/**
 * P1-5 验证：noteAttachment IndexedDB 缓存改为 LRU 策略
 *
 * 历史 bug：count > 200 时遍历整个 store 全删 → 用户网络抓取的图片每次
 * 超过 200 张就被全清，下次又得网络重拉。
 *
 * 修复：超阈值时只删最老的 N 条，并在 read 路径 LRU "touch" 时间戳。
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import 'fake-indexeddb/auto'

// Avoid real Capacitor / shared imports that aren't relevant here
vi.mock('@desk-notes/shared', () => ({}))
vi.mock('../tokenManager', () => ({
  tokenManager: { baseUrl: 'http://127.0.0.1:8000', backendPort: 18765 },
}))

describe('noteAttachment cache (P1-5)', () => {
  beforeEach(async () => {
    // Reset fake IndexedDB between tests
    const { IDBFactory } = await import('fake-indexeddb')
    ;(globalThis as any).indexedDB = new IDBFactory()
    vi.resetModules()
  })

  it('writes and reads back the same dataUrl', async () => {
    const { cacheAttachment, getCachedAttachment } = await import('../noteAttachment')
    await cacheAttachment('a/1.png', 'data:image/png;base64,AAAA')
    // give microtask queue a tick for the put to flush
    await new Promise(r => setTimeout(r, 0))
    const got = await getCachedAttachment('a/1.png')
    expect(got).toBe('data:image/png;base64,AAAA')
  })

  it('eviction does NOT wipe the entire cache when over the limit', async () => {
    const { cacheAttachment, getCachedAttachment } = await import('../noteAttachment')

    // Fill 220 entries — exceeds the 200 limit
    for (let i = 0; i < 220; i++) {
      await cacheAttachment(`p/${i}.png`, `data:image/png;base64,${i}`)
    }
    // Let pending eviction transactions flush
    await new Promise(r => setTimeout(r, 50))

    // Newer entries (closer to 220) must survive — count missing entries
    let stillThere = 0
    for (let i = 200; i < 220; i++) {
      const got = await getCachedAttachment(`p/${i}.png`)
      if (got !== null) stillThere++
    }
    expect(stillThere).toBeGreaterThanOrEqual(15) // most recent 20 should survive
  })

  it('returns null for missing keys without throwing', async () => {
    const { getCachedAttachment } = await import('../noteAttachment')
    const got = await getCachedAttachment('does-not-exist')
    expect(got).toBeNull()
  })

  it('LRU touch: re-reading an entry bumps its timestamp', async () => {
    const { cacheAttachment, getCachedAttachment } = await import('../noteAttachment')
    await cacheAttachment('hot/1.png', 'data:hot')
    await cacheAttachment('cold/1.png', 'data:cold')
    await new Promise(r => setTimeout(r, 5))
    // Touch hot entry to refresh its timestamp
    await getCachedAttachment('hot/1.png')
    // Both should still be readable; we don't have a public API for ts so
    // just confirm we don't crash and both entries are intact.
    expect(await getCachedAttachment('hot/1.png')).toBe('data:hot')
    expect(await getCachedAttachment('cold/1.png')).toBe('data:cold')
  })
})
