const STORAGE_KEY = 'siyi_pending_ops'
const MAX_QUEUE_SIZE = 500
const MAX_RETRIES = 5

export type PendingOpType =
  | 'note_create' | 'note_update' | 'note_delete'
  | 'task_create' | 'task_update' | 'task_delete'
  | 'quick_relay'

export interface PendingOp {
  id: string
  type: PendingOpType
  entityId: string
  payload: Record<string, string>
  createdAt: number
  retryCount: number
  maxRetries: number
}

type ReplayHandler = (op: PendingOp) => Promise<boolean>

function load(): PendingOp[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function save(ops: PendingOp[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(ops))
  } catch {
    // 存储不可用（满/隐私模式）：内存队列继续工作，但持久化失败必须可感知，
    // 否则用户以为"离线改动已保存"实际重启即丢（C-2 反馈闭环）
    console.warn('[pendingOps] failed to persist queue (storage unavailable?)')
  }
}

export class PendingOpManager {
  private ops: PendingOp[] = load()
  private replayHandler: ReplayHandler | null = null
  private autoReplayActive = false
  private boundOnlineHandler: (() => void) | null = null

  /**
   * C-2：条目重试超限被丢弃时的回调（由宿主接线到 toast 等可见提示）。
   * 旧行为静默 drop，用户以为"离线改动已保存"实际已丢——必须可感知。
   */
  onDrop: ((op: PendingOp) => void) | null = null

  get count(): number {
    return this.ops.length
  }

  canEnqueue(): boolean {
    return this.ops.length < MAX_QUEUE_SIZE
  }

  setReplayHandler(handler: ReplayHandler): void {
    this.replayHandler = handler
  }

  enqueue(op: Omit<PendingOp, 'id' | 'createdAt' | 'retryCount' | 'maxRetries'>): void {
    if (!this.canEnqueue()) {
      throw new Error('Pending operation queue is full')
    }
    const entry: PendingOp = {
      ...op,
      id: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
      createdAt: Date.now(),
      retryCount: 0,
      maxRetries: MAX_RETRIES,
    }
    // V2.1 批次4：同 (type, entityId) 的待办 op 去重 —— LWW 回推只关心「最新状态」，
    // 用新 payload 替换旧 op（保留原 createdAt），避免离线期间多次失败产生重复堆积。
    const idx = this.ops.findIndex(o => o.type === entry.type && o.entityId === entry.entityId)
    if (idx >= 0) {
      this.ops[idx] = entry
    } else {
      this.ops.push(entry)
    }
    save(this.ops)
  }

  enqueueNoteOp(action: 'note_create' | 'note_update' | 'note_delete', noteId: string, data?: Record<string, string>): void {
    this.enqueue({ type: action, entityId: noteId, payload: data ?? {} })
  }

  enqueueTaskOp(action: 'task_create' | 'task_update' | 'task_delete', taskId: string, data?: Record<string, string>): void {
    this.enqueue({ type: action, entityId: taskId, payload: data ?? {} })
  }

  async replayAll(): Promise<void> {
    if (!this.replayHandler) return

    const remaining: PendingOp[] = []
    const dropped: PendingOp[] = []
    for (const op of this.ops) {
      try {
        const ok = await this.replayHandler(op)
        if (!ok) {
          op.retryCount++
          if (op.retryCount < op.maxRetries) {
            remaining.push(op)
          } else {
            dropped.push(op) // C-2：超限丢弃不再静默
          }
        }
      } catch {
        op.retryCount++
        if (op.retryCount < op.maxRetries) {
          remaining.push(op)
        } else {
          dropped.push(op) // C-2：超限丢弃不再静默
        }
      }
    }
    this.ops = remaining
    save(this.ops)
    for (const op of dropped) {
      try { this.onDrop?.(op) } catch { /* 通知失败不影响队列状态 */ }
    }
  }

  startAutoReplay(): void {
    if (this.autoReplayActive) return
    this.autoReplayActive = true

    this.boundOnlineHandler = () => {
      this.replayAll().catch(() => {})
    }
    window.addEventListener('online', this.boundOnlineHandler)

    // Also replay immediately if already online
    if (navigator.onLine) {
      this.replayAll().catch(() => {})
    }
  }

  stopAutoReplay(): void {
    if (this.boundOnlineHandler) {
      window.removeEventListener('online', this.boundOnlineHandler)
      this.boundOnlineHandler = null
    }
    this.autoReplayActive = false
  }

  clear(type?: PendingOpType): void {
    if (type) {
      this.ops = this.ops.filter(op => op.type !== type)
    } else {
      this.ops = []
    }
    save(this.ops)
  }
}
