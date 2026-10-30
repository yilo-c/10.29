/**
 * 记忆桥 — 桌面 `memory.*` / `tags.list` / `memory.narrative.*` RPC 的薄封装。
 *
 * 设计原则：所有返回都做防御性归一化，调用方可以信任拿到的是 `MemoryItem[]`。
 */
import { callRpc } from '../rpcClient'

export interface MemoryItem {
  id: string | number
  text?: string
  content?: string
  summary?: string
  score?: number
  tags?: string[]
  created_at?: string | number
  updated_at?: string | number
}

function asArray(res: unknown): MemoryItem[] {
  if (Array.isArray(res)) return res as MemoryItem[]
  if (res && typeof res === 'object' && Array.isArray((res as { items?: unknown[] }).items)) {
    return (res as { items: MemoryItem[] }).items
  }
  return []
}

export async function searchMemory(query: string, limit = 10): Promise<MemoryItem[]> {
  try {
    const res = await callRpc<unknown>('memory.search', { query, limit })
    return asArray(res)
  } catch {
    return []
  }
}

export async function recentMemory(limit = 10): Promise<MemoryItem[]> {
  try {
    const res = await callRpc<unknown>('memory.recent', { limit })
    return asArray(res)
  } catch {
    return []
  }
}

export async function relatedById(memoryId: string | number, limit = 5): Promise<MemoryItem[]> {
  try {
    const res = await callRpc<unknown>('memory.related', { memory_id: memoryId, limit })
    return asArray(res)
  } catch {
    return []
  }
}

export async function relatedByText(text: string, limit = 5): Promise<MemoryItem[]> {
  try {
    const res = await callRpc<unknown>('memory.related_text', { text, limit })
    return asArray(res)
  } catch {
    return []
  }
}

export async function surfaceForInput(text: string): Promise<MemoryItem[]> {
  try {
    const res = await callRpc<unknown>('memory.surface_for_input', { text })
    return asArray(res)
  } catch {
    return []
  }
}

/**
 * 保存对话锚点。
 *
 * 后端签名 `memory_save_conversation_anchor(session_id, messages, text)`，
 * 没有 `anchor` 字段。此前传 `{session_id, anchor}` ——
 * `anchor` 落进 `**kwargs` 被吞、`text` 为空，
 * 后端拿不到任何内容，**恒返回 false，记忆锚点从未真正保存过**。
 *
 * 另外该返回值是裸 bool（不是 {success} 信封），所以要按布尔判断，
 * 不能只看「有没有抛异常」。
 */
export async function saveConversationAnchor(sessionId: string, anchor: string): Promise<boolean> {
  try {
    const res = await callRpc<boolean | { success?: boolean }>('memory.save_conversation_anchor', {
      session_id: sessionId,
      text: anchor,
    })
    if (typeof res === 'boolean') return res
    return res?.success === true
  } catch {
    return false
  }
}

export async function latestNarrative(): Promise<unknown> {
  try {
    return await callRpc('memory.narrative.latest', {})
  } catch {
    return null
  }
}

export async function narrativeTypes(): Promise<string[]> {
  try {
    const res = await callRpc<unknown>('memory.narrative.types', {})
    if (Array.isArray(res)) return res.filter((x): x is string => typeof x === 'string')
    return []
  } catch {
    return []
  }
}
