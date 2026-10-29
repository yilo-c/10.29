import { describe, it, expect, beforeEach, vi } from 'vitest'
import {
  enqueueSyncOp,
  getPendingSyncCount,
  subscribeSyncQueue,
  attachSyncQueue,
  __resetSyncQueueForTest,
} from '../syncQueue'
import { errorStore } from '../../store/errorStore'
import type { WsClient } from '../../ws-client/ws-client'

/** Minimal WsClient-shaped mock — only the surface syncQueue touches. */
function makeMockClient(initial: 'connected' | 'disconnected' = 'disconnected') {
  let status = initial
  const statusListeners = new Set<(s: string) => void>()
  const callImpl = vi.fn(async (..._args: unknown[]) => undefined as unknown)
  const client = {
    get status() { return status },
    onStatusChange(fn: (s: string) => void) {
      statusListeners.add(fn as (s: string) => void)
      return () => { statusListeners.delete(fn as (s: string) => void) }
    },
    call: callImpl,
    _call: callImpl,
    setStatus(s: typeof status) {
      status = s
      for (const fn of statusListeners) fn(s)
    },
  }
  return client as unknown as WsClient & { setStatus: (s: typeof status) => void; _call: typeof callImpl }
}

describe('syncQueue (1.1 写操作持久化队列)', () => {
  beforeEach(() => {
    __resetSyncQueueForTest()
    errorStore.clear()
    vi.useRealTimers()
  })

  it('断连时入队并持久化到 localStorage', () => {
    expect(enqueueSyncOp('notes.create', { title: 'a' })).toBe(true)
    expect(getPendingSyncCount()).toBe(1)
    expect(localStorage.getItem('siyi_sync_op_queue_v1')).toContain('notes.create')
  })

  it('跨实例恢复：持久化队列在重新 load 后仍在（模拟应用重启）', () => {
    enqueueSyncOp('notes.create', { title: 'a' })
    enqueueSyncOp('notes.update', { note_id: 1 })
    // 模拟重启：清内存态但保留 localStorage
    const raw = localStorage.getItem('siyi_sync_op_queue_v1')
    __resetSyncQueueForTest()
    localStorage.setItem('siyi_sync_op_queue_v1', raw!)
    const client = makeMockClient('connected')
    attachSyncQueue(client)
    expect(getPendingSyncCount()).toBe(2)
  })

  it('重连后按 FIFO 顺序补发并清空队列', async () => {
    const client = makeMockClient('disconnected')
    attachSyncQueue(client)
    enqueueSyncOp('notes.create', { n: 1 })
    enqueueSyncOp('notes.update', { n: 2 })
    enqueueSyncOp('notes.delete', { n: 3 })
    expect(getPendingSyncCount()).toBe(3)

    client.setStatus('connected')
    await vi.waitFor(() => {
      expect(getPendingSyncCount()).toBe(0)
    })
    const methods = client._call.mock.calls.map((c: unknown[]) => c[0])
    expect(methods).toEqual(['notes.create', 'notes.update', 'notes.delete'])
    expect(localStorage.getItem('siyi_sync_op_queue_v1')).toBe('[]')
  })

  it('补发失败的常规错误保留在队首等下次 connected 再补', async () => {
    const client = makeMockClient('disconnected')
    attachSyncQueue(client)
    client._call.mockRejectedValueOnce(new Error('transient'))
    enqueueSyncOp('notes.create', { n: 1 })
    enqueueSyncOp('notes.update', { n: 2 })

    client.setStatus('connected')
    await vi.waitFor(() => {
      expect(client._call).toHaveBeenCalledTimes(1)
    })
    // 首条失败 → 停止本轮，队列保留 2 条（首条 attempts=1）
    expect(getPendingSyncCount()).toBe(2)
    // 重连恢复：再次 connected 且 call 成功 → 全部补发
    client.setStatus('disconnected')
    client.setStatus('connected')
    await vi.waitFor(() => {
      expect(getPendingSyncCount()).toBe(0)
    })
  })

  it('单条失败达到重试上限后出队并弹 errorStore（毒丸不阻塞后续）', async () => {
    const client = makeMockClient('disconnected')
    attachSyncQueue(client)
    client._call.mockImplementation(async (method: string) => {
      if (method === 'notes.create') throw new Error('server rejects')
    })
    enqueueSyncOp('notes.create', { poison: true })
    enqueueSyncOp('notes.update', { fine: true })

    // 连续 connected 周期消耗重试次数（MAX_RETRY=5）
    for (let i = 0; i < 6; i++) {
      client.setStatus('disconnected')
      client.setStatus('connected')
      await vi.waitFor(() => {
        expect(client._call).toHaveBeenCalled()
      })
    }
    // 毒丸被丢弃（errorStore 收到报错），正常操作补发成功
    expect(errorStore.activeErrors.some(m => m.message.includes('notes.create'))).toBe(true)
    expect(getPendingSyncCount()).toBe(0)
  })

  it('队列超限时明确报错而非静默丢弃', () => {
    for (let i = 0; i < 500; i++) enqueueSyncOp('notes.update', { i })
    expect(enqueueSyncOp('notes.update', { overflow: true })).toBe(false)
    expect(errorStore.activeErrors.some(m => m.message.length > 0)).toBe(true)
    expect(getPendingSyncCount()).toBe(500)
  })

  it('订阅者能收到待同步数量变化', () => {
    const fn = vi.fn()
    const unsub = subscribeSyncQueue(fn)
    expect(fn).toHaveBeenCalledWith(0)
    enqueueSyncOp('notes.create', {})
    expect(fn).toHaveBeenLastCalledWith(1)
    unsub()
    enqueueSyncOp('notes.create', {})
    expect(fn).toHaveBeenCalledTimes(2) // 取消订阅后不再收到
  })
})
