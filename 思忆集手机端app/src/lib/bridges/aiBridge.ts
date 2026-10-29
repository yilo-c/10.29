/**
 * AI 桥 — 桌面 `ai.*` / `pattern.*` / `chat.*` 元能力的封装。
 *
 * 聊天主流式调用仍在 `DesktopAiClient`（HTTP SSE）以保持现有 ChatView 行为；
 * 这里补的是"工具/解析/会话管理"这类一次性能力，让 UI 不必再到处 `getWsClient()`。
 */
import { callRpc, callRpcStream } from '../rpcClient'

/* ── Intent / Pattern ── */
export interface IntentResult {
  intent?: string
  entities?: Record<string, unknown>
  confidence?: number
  raw?: string
  [k: string]: unknown
}

export async function parseIntent(text: string): Promise<IntentResult | null> {
  try { return await callRpc<IntentResult>('ai.parse_intent', { text }) } catch { return null }
}

export async function executePattern(patternId: string, params: Record<string, unknown> = {}): Promise<unknown> {
  try { return await callRpc('pattern.execute', { pattern_id: patternId, params }) } catch { return null }
}

/* ── Chat session helpers ── */
export interface ConversationListItem {
  session_id: string
  title?: string
  updated_at?: string | number
  message_count?: number
  [k: string]: unknown
}

export async function listConversations(limit = 50): Promise<ConversationListItem[]> {
  try {
    const res = await callRpc<unknown>('chat.list_conversations', { limit })
    return Array.isArray(res) ? (res as ConversationListItem[]) : []
  } catch { return [] }
}

export async function getConversation(sessionId: string): Promise<unknown> {
  try { return await callRpc('chat.get_conversation', { session_id: sessionId }) } catch { return null }
}

export async function getChatHistory(sessionId: string | null, limit = 50): Promise<unknown[]> {
  try {
    const res = sessionId
      ? await callRpc<unknown>('chat.history', { session_id: sessionId, limit })
      : await callRpc<unknown>('chat.history', { limit })
    return Array.isArray(res) ? res : []
  } catch { return [] }
}

export async function saveChatMessages(sessionId: string, messages: unknown[]): Promise<boolean> {
  try {
    await callRpc('chat.save_messages', { session_id: sessionId, messages })
    return true
  } catch { return false }
}

export async function compressChatContext(sessionId: string): Promise<boolean> {
  try {
    await callRpc('chat.compress_context', { session_id: sessionId })
    return true
  } catch { return false }
}

/** 流式 chat.send（WS 必备）；调用方负责消费 chunk。 */
export function streamChatSend(message: string, sessionId?: string | null) {
  const params: Record<string, unknown> = { message }
  if (sessionId) params.session_id = sessionId
  return callRpcStream('chat.send', params)
}

/* ── Knowledge system ── */

export interface IngestConversationResult {
  session_id?: number
  message_count?: number
  extracted?: number
  auto_write?: number
  stored?: number
  duplicates?: number
  merged?: number
  conflicts?: number
  needs_confirm?: number
  discarded?: number
  error?: string
}

/** 将已保存会话的对话内容提取为记忆并写入知识库。 */
export async function ingestConversation(sessionId: string | number): Promise<IngestConversationResult | null> {
  try {
    return await callRpc<IngestConversationResult>('knowledge.ingest_conversation', { session_id: sessionId })
  } catch { return null }
}

export interface OrganizeKnowledgeResult {
  report?: string
  summary?: string
  topics?: string[]
  gaps?: string[]
  suggestions?: string[]
  error?: string
  [k: string]: unknown
}

/** 主动整理个人知识体系，返回 AI 分析报告。 */
export async function organizeKnowledge(days = 30): Promise<OrganizeKnowledgeResult | null> {
  try {
    return await callRpc<OrganizeKnowledgeResult>('knowledge.organize', { days })
  } catch { return null }
}

/** 知识图谱实体（= 后端 graph_store 的实体形状）。 */
export interface KnowledgeGraphEntity {
  id: string
  name?: string
  type?: string
  aliases?: string[]
  properties?: Record<string, unknown>
  [k: string]: unknown
}

/**
 * 知识图谱实体检索（后端 `knowledge.search`）。
 *
 * MOB-KG（2026-09-21）契约修正：旧实现叫 `knowledge.graph` 并传
 * `{ query, entity_type }`，而后端 `knowledge.graph` **不接收任何参数**、
 * 恒返回全图（handler 签名 `knowledge_graph(**kwargs)`）——于是“按名称/类型
 * 查询”静默退化为全量拉取。带参检索的真入口是 `knowledge.search`。
 */
export async function searchKnowledgeGraph(query = '', entityType?: string): Promise<KnowledgeGraphEntity[] | null> {
  try {
    return await callRpc<KnowledgeGraphEntity[]>('knowledge.search', { query, entity_type: entityType ?? '' })
  } catch { return null }
}

/** 全图（后端 `knowledge.graph`：无参，返回 entities + relations）。 */
export interface KnowledgeGraphFull {
  entities?: KnowledgeGraphEntity[]
  relations?: Array<{ source_id: string; target_id: string; relation_type?: string; confidence?: number }>
  error?: string
  [k: string]: unknown
}

/** 拉取完整知识图谱（不传参数：后端不支持过滤）。 */
export async function fetchKnowledgeGraph(): Promise<KnowledgeGraphFull | null> {
  try {
    return await callRpc<KnowledgeGraphFull>('knowledge.graph', {})
  } catch { return null }
}

/**
 * `knowledge.traverse` 回执：后端返回 `{entities, relations}`，
 * 不是前端习惯的 `{nodes, edges}`（旧类型定义与后端不符）。
 */
export interface KnowledgeTraverseResult {
  entities?: KnowledgeGraphEntity[]
  relations?: Array<{ source_id: string; target_id: string; relation_type?: string }>
  error?: string
  [k: string]: unknown
}

/**
 * 从指定实体出发 BFS 遍历知识图谱。
 *
 * MOB-KG（2026-09-21）契约修正：参数名为 `start_id` / `max_hops`，
 * 旧码传 `entity_id` / `depth` → 后端拿到空 start_id，恒返回空图。
 */
export async function traverseKnowledge(startId: string, maxHops = 2): Promise<KnowledgeTraverseResult | null> {
  try {
    return await callRpc<KnowledgeTraverseResult>('knowledge.traverse', { start_id: startId, max_hops: maxHops })
  } catch { return null }
}

/** 统一知识查询（跨记忆/知识图/笔记/词库/资讯）。 */
export async function unifiedKnowledgeQuery(query: string, limit = 15): Promise<Record<string, unknown> | null> {
  try {
    return await callRpc<Record<string, unknown>>('knowledge.unified_query', { text: query, limit })
  } catch { return null }
}

/* ── Chat import ── */

export interface ChatImportResult {
  success?: boolean
  total?: number
  imported?: number
  skipped?: number
  sessions_saved?: number
  memories_stored?: number
  categories?: Record<string, number>
  error?: string
  [k: string]: unknown
}

/** 导入一个聊天记录文件到知识系统（支持 ChatGPT/Claude/Kimi/豆包/Markdown）。
 *
 * filePath 可以是桌面端绝对路径，也可以是手机端通过 transfer.upload 上传后返回的文件名。
 */
export async function importChatFile(
  filePath: string,
  format: 'auto' | 'chatgpt' | 'claude' | 'kimi' | 'markdown' | 'doubao' = 'auto',
  maxConv = 50,
): Promise<ChatImportResult | null> {
  try {
    return await callRpc<ChatImportResult>('data.import_chat', {
      file_path: filePath,
      format: format === 'auto' ? '' : format,
      max_conv: maxConv,
    })
  } catch { return null }
}
