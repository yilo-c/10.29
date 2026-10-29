import { errorStore } from '../store/errorStore'
import { tStatic } from '../i18n'
import type { WsClient } from '../ws-client/ws-client'

/**
 * 1.1 写操作持久化队列（upgrade-plan 阶段一）
 *
 * 背景：旧 syncCall 为 fire-and-forget——断连时直接丢弃调用，笔记/待办写操作
 * 表面成功实际永久丢失。本模块将 syncCall 改为入队 + 重连补发：
 * - 队列持久化到 localStorage，跨应用重启不丢；
 * - FIFO 顺序补发（同实体 create→update→delete 语义不乱）；
 * - 单条失败重试上限 MAX_RETRY，超限弹 errorStore 并出队（避免毒丸阻塞）；
 * - 断连期间的失败在下次 connected 或 30s 定时兜底时继续；
 * - 超出容量上限时明确报错（而非静默丢弃）。
 */

const STORAGE_KEY = 'siyi_sync_op_queue_v1'
const MAX_QUEUE = 500
const MAX_RETRY = 5
const RETRY_DELAY_MS = 30_000

export interface QueuedOp {
  id: string
  method: string
  params: Record<string, unknown>
  attempts: number
  ts: number
}

let queue: QueuedOp[] = []
let loaded = false
let draining = false
let retryTimer: ReturnType<typeof setTimeout> | null = null
let seq = 0

const listeners = new Set<(pending: number) => void>()
/** C-2：毒丸丢弃观察者——条目重试超限被放弃时通知 UI 做关联标记 */
const dropListeners = new Set<(op: QueuedOp) => void>()

let persistWarned = false

function safeLoad(): void {
  if (loaded) return
  loaded = true
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return
    const parsed: unknown = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      queue = parsed.filter(
        (op): op is QueuedOp =>
          !!op && typeof (op as QueuedOp).method === 'string' &&
          typeof (op as QueuedOp).id === 'string',
      )
      seq = queue.reduce((m, op) => {
        const n = Number(op.id.split('_').pop())
        return Number.isFinite(n) && n > m ? n : m
      }, 0)
    }
  } catch {
    queue = []
  }
}

function persist(): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(queue))
  } catch {
    // C-2：storage 满/不可用——内存队列仍可工作，但必须让用户知道重启会丢
    if (!persistWarned) {
      persistWarned = true
      errorStore.warn(tStatic('syncQueue.persistFailed'))
    }
  }
  const n = queue.length
  for (const fn of listeners) {
    try { fn(n) } catch { /* ignore */ }
  }
}

/** 入队一条写操作；容量超限时返回 false 并提示用户（调用方本地状态已生效，仅上行失败）。 */
export function enqueueSyncOp(method: string, params: Record<string, unknown>): boolean {
  safeLoad()
  if (queue.length >= MAX_QUEUE) {
    errorStore.error(tStatic('syncQueue.overflow'))
    return false
  }
  queue.push({ id: `op_${Date.now()}_${++seq}`, method, params, attempts: 0, ts: Date.now() })
  persist()
  return true
}

export function getPendingSyncCount(): number {
  safeLoad()
  return queue.length
}

export function subscribeSyncQueue(fn: (pending: number) => void): () => void {
  safeLoad()
  listeners.add(fn)
  fn(queue.length)
  return () => listeners.delete(fn)
}

/** C-2：订阅条目丢弃事件（重试超限的毒丸出队）。返回退订函数。 */
export function subscribeSyncQueueDrops(fn: (op: QueuedOp) => void): () => void {
  dropListeners.add(fn)
  return () => dropListeners.delete(fn)
}

async function drain(client: WsClient): Promise<void> {
  if (draining) return
  draining = true
  try {
    safeLoad()
    while (queue.length > 0) {
      if (client.status !== 'connected') break
      const op = queue[0]
      try {
        await client.call(op.method, op.params)
        queue.shift()
        persist()
      } catch (e) {
        op.attempts += 1
        if (op.attempts >= MAX_RETRY) {
          queue.shift()
          persist()
          errorStore.error(tStatic('syncQueue.permanentFail', { n: MAX_RETRY, method: op.method }))
          // C-2：通知 UI 关联标记（如 IdeaPanel 把对应 pending 条目转为 failed 可删）
          for (const fn of dropListeners) {
            try { fn(op) } catch { /* ignore */ }
          }
          continue // 毒丸出队，继续补发后续操作
        }
        persist()
        // 网络类失败等重连事件驱动；服务端错误则定时兜底重试
        if (client.status === 'connected') scheduleRetryDrain()
        break
      }
    }
  } finally {
    draining = false
  }
}

function scheduleRetryDrain(): void {
  if (retryTimer) return
  retryTimer = setTimeout(() => {
    retryTimer = null
    void activeDrain()
  }, RETRY_DELAY_MS)
}

let clientRef: WsClient | null = null

function activeDrain(): void {
  if (clientRef && clientRef.status === 'connected') void drain(clientRef)
}

/** 在 WsClient 单例创建处调用：绑定重连补发 + 启动时恢复队列。 */
export function attachSyncQueue(client: WsClient): void {
  clientRef = client
  client.onStatusChange((s) => {
    if (s === 'connected') void drain(client)
  })
  safeLoad()
  if (queue.length > 0 && client.status === 'connected') void drain(client)
}

/** 测试钩子：重置内部状态。 */
export function __resetSyncQueueForTest(): void {
  queue = []
  loaded = false
  draining = false
  seq = 0
  persistWarned = false
  dropListeners.clear()
  if (retryTimer) { clearTimeout(retryTimer); retryTimer = null }
  try { localStorage.removeItem(STORAGE_KEY) } catch { /* ignore */ }
}
