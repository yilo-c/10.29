/**
 * 学习/资讯/报告 桥 — 桌面 `url_learning.*` / `info.*` / `report.*` / `data.import_*`。
 *
 * 这一组接口都是"长任务"，UI 应当展示 loading + 失败重试。
 */
import { callRpc } from '../rpcClient'

/* ── URL Learning ── */
export async function learnFromUrl(url: string): Promise<unknown> {
  try { return await callRpc('url_learning.learn', { url }) } catch { return null }
}

/* ── Info radar ── */
export interface InfoBriefItem {
  title?: string
  url?: string
  summary?: string
  source?: string
  score?: number
  [k: string]: unknown
}

export async function infoBrief(topics?: string[]): Promise<InfoBriefItem[]> {
  try {
    const res = await callRpc<unknown>('info.brief', { topics: topics ?? [] })
    if (Array.isArray(res)) return res as InfoBriefItem[]
    if (res && typeof res === 'object' && Array.isArray((res as { items?: unknown[] }).items)) {
      return (res as { items: InfoBriefItem[] }).items
    }
    return []
  } catch {
    return []
  }
}

export async function infoAutoConfig(args: { enabled?: boolean; interval?: number; topics?: string[] }): Promise<boolean> {
  try { await callRpc('info.auto_config', args as Record<string, unknown>); return true } catch { return false }
}

export async function infoFetchProxy(url: string): Promise<string> {
  try {
    const res = await callRpc<unknown>('info.fetch_proxy', { url })
    if (typeof res === 'string') return res
    if (res && typeof res === 'object' && typeof (res as { content?: unknown }).content === 'string') {
      return (res as { content: string }).content
    }
    return ''
  } catch { return '' }
}

export async function infoPersonalize(preferences: Record<string, unknown>): Promise<boolean> {
  try { await callRpc('info.personalize', { preferences }); return true } catch { return false }
}

/* ── Reports ── */
export async function generateReport(topic: string, format = 'markdown'): Promise<string> {
  try {
    const res = await callRpc<unknown>('report.generate', { topic, format })
    if (typeof res === 'string') return res
    if (res && typeof res === 'object' && typeof (res as { content?: unknown }).content === 'string') {
      return (res as { content: string }).content
    }
    return ''
  } catch { return '' }
}

/* ── Data Import ── */
export async function importChat(format: string, content: string): Promise<{ imported?: number } | null> {
  try { return await callRpc<{ imported?: number }>('data.import_chat', { format, content }) } catch { return null }
}

export async function importChatFolder(path: string): Promise<{ imported?: number } | null> {
  try { return await callRpc<{ imported?: number }>('data.import_chat_folder', { path }) } catch { return null }
}

/* ── Knowledge graph ── */
export interface KnowledgeEntity {
  id?: string | number
  name?: string
  type?: string
  attributes?: Record<string, unknown>
  [k: string]: unknown
}

export async function searchKnowledge(query: string, entityType?: string): Promise<KnowledgeEntity[]> {
  try {
    const res = await callRpc<unknown>('knowledge.search', { query, entity_type: entityType })
    return Array.isArray(res) ? (res as KnowledgeEntity[]) : []
  } catch { return [] }
}

export async function knowledgeRelations(entityId: string | number): Promise<unknown> {
  try { return await callRpc('knowledge.relations', { entity_id: entityId }) } catch { return null }
}

/**
 * 统一知识查询。
 *
 * MOB-KG（2026-09-21）契约修正：后端 handler 形参是 `text`（
 * `knowledge_unified_query(text, limit)`），旧码传 `{ query }` → 被
 * `**kwargs` 吞掉、text 恒为空串，等于拿空查询去跑全源检索。
 * 与同仓 `aiBridge.unifiedKnowledgeQuery`（传 text，形状正确）对齐。
 */
export async function knowledgeUnifiedQuery(query: string, limit = 15): Promise<unknown> {
  try { return await callRpc('knowledge.unified_query', { text: query, limit }) } catch { return null }
}

/* ── OCR ── */
export async function ocrExtractText(imagePath: string): Promise<string> {
  try {
    const res = await callRpc<unknown>('ocr.extract_text', { image_path: imagePath })
    if (typeof res === 'string') return res
    if (res && typeof res === 'object' && typeof (res as { text?: unknown }).text === 'string') {
      return (res as { text: string }).text
    }
    return ''
  } catch { return '' }
}

export async function ocrExtractBase64(imageBase64: string): Promise<string> {
  try {
    const res = await callRpc<unknown>('ocr.extract_base64', { image_base64: imageBase64 })
    if (typeof res === 'string') return res
    if (res && typeof res === 'object' && typeof (res as { text?: unknown }).text === 'string') {
      return (res as { text: string }).text
    }
    return ''
  } catch { return '' }
}
