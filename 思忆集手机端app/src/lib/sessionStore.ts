/**
 * 会话持久化 / Session persistence
 *
 * 为什么不用 localStorage：
 *   Zustand 的 persist 默认走 localStorage，容量 5MB 左右。
 *   聊天历史带工具轨迹后很容易超（一次多步任务就可能几百字 trace），
 *   写满后 persist 会静默失败，用户看到的就是「聊天记录不见了」。
 *   IndexedDB 容量按磁盘配额，几百 MB 无压力。
 *
 * 设计：
 *   - 多个会话（session），每个会话有 id / 标题 / 时间戳 / 消息列表
 *   - 当前会话 id 单独存 localStorage（很小，不占空间）
 *   - 自动清理：保留最近 50 个会话，超出删最旧的
 *   - 单会话消息上限 500 条，超出从头截断（保留最近的）
 */

export interface StoredMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

export interface StoredSession {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: StoredMessage[]
}

const DB_NAME = 'siyi-agent'
const DB_VERSION = 1
const STORE = 'sessions'
const CURRENT_KEY = 'siyi-current-session'

const MAX_SESSIONS = 50
const MAX_MESSAGES_PER_SESSION = 500

let dbPromise: Promise<IDBDatabase> | null = null

function openDB(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB 不可用'))
      return
    }
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        const os = db.createObjectStore(STORE, { keyPath: 'id' })
        os.createIndex('updatedAt', 'updatedAt')
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error ?? new Error('打开 IndexedDB 失败'))
  })
  return dbPromise
}

function tx<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T> | void): Promise<T | void> {
  return openDB().then(db => new Promise<T | void>((resolve, reject) => {
    const t = db.transaction(STORE, mode)
    const store = t.objectStore(STORE)
    let result: T | void
    const req = fn(store)
    if (req) req.onsuccess = () => { result = req.result }
    t.oncomplete = () => resolve(result)
    t.onerror = () => reject(t.error)
    t.onabort = () => reject(t.error)
  }))
}

/** 由首条用户消息生成标题 */
export function deriveTitle(text: string): string {
  const t = text.trim().replace(/\s+/g, ' ')
  if (!t) return '新会话'
  return t.length > 20 ? t.slice(0, 20) + '…' : t
}

export async function listSessions(): Promise<StoredSession[]> {
  try {
    const all = await tx<StoredSession[]>('readonly', s => s.getAll())
    const arr = (all as StoredSession[]) ?? []
    return arr.sort((a, b) => b.updatedAt - a.updatedAt)
  } catch {
    return []
  }
}

export async function getSession(id: string): Promise<StoredSession | null> {
  try {
    const r = await tx<StoredSession>('readonly', s => s.get(id))
    return (r as StoredSession) ?? null
  } catch {
    return null
  }
}

export async function saveSession(sess: StoredSession): Promise<void> {
  try {
    // 截断过长消息
    const trimmed: StoredSession = {
      ...sess,
      messages: sess.messages.length > MAX_MESSAGES_PER_SESSION
        ? sess.messages.slice(-MAX_MESSAGES_PER_SESSION)
        : sess.messages,
    }
    await tx('readwrite', s => s.put(trimmed))
    void pruneOldSessions()
  } catch {
    /* 存不了不阻塞对话 */
  }
}

export async function deleteSession(id: string): Promise<void> {
  try {
    await tx('readwrite', s => s.delete(id))
  } catch { /* ignore */ }
}

/** 只保留最近 MAX_SESSIONS 个会话 */
async function pruneOldSessions(): Promise<void> {
  try {
    const all = await listSessions()
    if (all.length <= MAX_SESSIONS) return
    const toDelete = all.slice(MAX_SESSIONS)
    for (const s of toDelete) {
      await tx('readwrite', store => store.delete(s.id))
    }
  } catch { /* ignore */ }
}

/** 当前会话 id（很小，放 localStorage） */
export function getCurrentSessionId(): string | null {
  try {
    return localStorage.getItem(CURRENT_KEY)
  } catch {
    return null
  }
}

export function setCurrentSessionId(id: string | null): void {
  try {
    if (id) localStorage.setItem(CURRENT_KEY, id)
    else localStorage.removeItem(CURRENT_KEY)
  } catch { /* ignore */ }
}

/** 生成会话 id */
export function newSessionId(): string {
  return `s_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
}

/**
 * 把当前消息列表落盘。
 * 供 store 在消息变化时调用（防抖由调用方负责）。
 */
export async function persistMessages(
  sessionId: string,
  messages: StoredMessage[],
): Promise<void> {
  const now = Date.now()
  const existing = await getSession(sessionId)
  const title = existing?.title
    ?? deriveTitle(messages.find(m => m.role === 'user')?.content ?? '')
  await saveSession({
    id: sessionId,
    title,
    createdAt: existing?.createdAt ?? now,
    updatedAt: now,
    messages,
  })
  setCurrentSessionId(sessionId)
}
