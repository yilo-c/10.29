/**
 * 笔记桥 — 与桌面 `notes.*` RPC 交互的薄封装。
 *
 * 主要复刻能力：
 *   - `pullNotes()`：把桌面笔记合并入手机端 floatingNotes（在 `contentSync.ts` 之外
 *     提供一个不依赖 store 的纯函数版本，便于其他组件按需调用）；
 *   - `getNote(id)` / `updateNote(...)` / `deleteNote(id)`：单条增删改；
 *   - `autoTag(id)` / `generateSummary(id)`：调用桌面 AI 能力。
 */
import { callRpc } from '../rpcClient'

export interface RemoteNote {
  id: string | number
  title?: string
  content?: string
  tags?: string[]
  folder?: string
  summary?: string
  created_at?: string | number
  updated_at?: string | number
}

export async function listNotes(limit = 200): Promise<RemoteNote[]> {
  try {
    const res = await callRpc<unknown>('notes.list', { limit })
    if (Array.isArray(res)) return res as RemoteNote[]
    if (res && typeof res === 'object' && Array.isArray((res as { items?: unknown[] }).items)) {
      return (res as { items: RemoteNote[] }).items
    }
    return []
  } catch {
    return []
  }
}

export async function getNote(noteId: string | number): Promise<RemoteNote | null> {
  try {
    return await callRpc<RemoteNote>('notes.get', { note_id: noteId })
  } catch {
    return null
  }
}

export async function createNote(args: {
  title?: string
  content: string
  folder?: string
  tags?: string[]
}): Promise<RemoteNote | null> {
  try {
    return await callRpc<RemoteNote>('notes.create', {
      title: args.title ?? '',
      content: args.content,
      folder: args.folder,
      tags: args.tags ?? [],
    })
  } catch {
    return null
  }
}

export async function updateNote(noteId: string | number, patch: Partial<RemoteNote>): Promise<boolean> {
  try {
    await callRpc('notes.update', { note_id: noteId, ...patch })
    return true
  } catch {
    return false
  }
}

export async function deleteNote(noteId: string | number): Promise<boolean> {
  try {
    await callRpc('notes.delete', { note_id: noteId })
    return true
  } catch {
    return false
  }
}

/**
 * 自动标签：注意后端 `notes.auto_tag` 接收的是 **正文 content**（不是 note_id），
 * 直接把 AI 抽取的标签数组返回。这里宽容两种入参写法，方便调用方传字符串或对象。
 */
export async function autoTag(content: string): Promise<string[]> {
  try {
    const res = await callRpc<unknown>('notes.auto_tag', { content })
    if (Array.isArray(res)) return res.filter((x): x is string => typeof x === 'string')
    if (res && typeof res === 'object' && Array.isArray((res as { tags?: unknown[] }).tags)) {
      return (res as { tags: unknown[] }).tags.filter((x): x is string => typeof x === 'string')
    }
    return []
  } catch {
    return []
  }
}

/** 摘要生成：同上，后端拿的是正文 content。 */
export async function generateSummary(content: string): Promise<string> {
  try {
    const res = await callRpc<unknown>('notes.generate_summary', { content })
    if (typeof res === 'string') return res
    if (res && typeof res === 'object' && typeof (res as { summary?: unknown }).summary === 'string') {
      return (res as { summary: string }).summary
    }
    return ''
  } catch {
    return ''
  }
}
