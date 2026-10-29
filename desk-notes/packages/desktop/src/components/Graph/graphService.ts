import type { KnowledgeGraphData, GraphNode, GraphEdge, FloatingNote, KnowledgeCard, TopicMOC } from '../../types'
import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'

// ── In-memory store (merged from file + extracted from notes) ────
let _cachedData: KnowledgeGraphData | null = null
let _backendLoaded = false

// ── Empty graph (no sample data — use backend knowledge.graph RPC) ──
const EMPTY_GRAPH: KnowledgeGraphData = {
  nodes: [],
  edges: [],
}

// ── Helpers ──────────────────────────────────────────────────────

/** Create a deterministic node ID from a label */
function nodeId(label: string): string {
  return 'n-' + label.replace(/[^a-zA-Z0-9一-鿿_-]/g, '_').toLowerCase()
}

/** Extract nodes and edges from notes (deterministic: sorted by node id) */
function extractFromNotes(notes: FloatingNote[]): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const nodeMap = new Map<string, GraphNode>()
  const edgeSet = new Set<string>()
  const edges: GraphEdge[] = []

  for (const note of notes) {
    if (!note.title) continue
    const id = nodeId(note.title)
    if (!nodeMap.has(id)) {
      nodeMap.set(id, { id, label: note.title, type: 'concept' })
    }

    // Tags → tag nodes + edges
    if (note.tags) {
      for (const tag of note.tags) {
        const tagId = nodeId('tag:' + tag)
        if (!nodeMap.has(tagId)) {
          nodeMap.set(tagId, { id: tagId, label: tag, type: 'tag' })
        }
        const ek = `${id}|${tagId}|tagged`
        if (!edgeSet.has(ek)) { edgeSet.add(ek); edges.push({ source: id, target: tagId, type: 'tagged' }) }
      }
    }

    // [[WikiLinks]] → edges
    const content = note.content || ''
    const wikiRe = /\[\[([^\]]+)\]\]/g
    let m: RegExpExecArray | null
    while ((m = wikiRe.exec(content)) !== null) {
      const targetTitle = m[1].trim()
      const targetId = nodeId(targetTitle)
      const targetNote = notes.find(n => n.title === targetTitle || (n.title || '').toLowerCase() === targetTitle.toLowerCase())
      if (targetNote) {
        const realId = nodeId(targetNote.title)
        if (!nodeMap.has(realId)) {
          nodeMap.set(realId, { id: realId, label: targetNote.title, type: 'concept' })
        }
        const ek = `${id}|${realId}|related`
        if (!edgeSet.has(ek)) { edgeSet.add(ek); edges.push({ source: id, target: realId, type: 'related' }) }
      } else {
        // WikiLink to a non-existent note — still add as an unlinked node
        if (!nodeMap.has(targetId)) {
          nodeMap.set(targetId, { id: targetId, label: targetTitle, type: 'concept' })
        }
        const ek = `${id}|${targetId}|related`
        if (!edgeSet.has(ek)) { edgeSet.add(ek); edges.push({ source: id, target: targetId, type: 'related' }) }
      }
    }
  }

  return { nodes: Array.from(nodeMap.values()), edges }
}

/** Merge two graph datasets (b overwrites a for same id) */
function mergeGraphs(a: KnowledgeGraphData, b: KnowledgeGraphData): KnowledgeGraphData {
  const nodeMap = new Map<string, GraphNode>()
  for (const n of a.nodes) nodeMap.set(n.id, n)
  for (const n of b.nodes) {
    const prev = nodeMap.get(n.id)
    // V1-5：同 id 合并时保留 a 侧已有的 properties（本地自动抽取节点无 properties，
    // 直接覆盖会把后端实体属性抹掉，成为第四个丢弃点）
    if (prev && prev.properties && !n.properties) {
      nodeMap.set(n.id, { ...n, properties: prev.properties })
    } else {
      nodeMap.set(n.id, n) // b overwrites
    }
  }

  const edgeSet = new Set<string>()
  const mergedEdges: GraphEdge[] = []
  const edgeKey = (e: GraphEdge) => `${e.source}|${e.target}|${e.type}`
  for (const e of [...a.edges, ...b.edges]) {
    const k = edgeKey(e)
    if (!edgeSet.has(k)) {
      edgeSet.add(k)
      mergedEdges.push(e)
    }
  }

  return {
    nodes: Array.from(nodeMap.values()),
    edges: mergedEdges,
  }
}

/** Save graph data via Electron IPC */
async function saveGraphData(data: KnowledgeGraphData): Promise<void> {
  const api = window.electronAPI
  if (api?.fileStore?.writeFile) {
    try {
      await api.fileStore.writeFile('knowledge_graph.json', JSON.stringify(data, null, 2))
    } catch (e) { /* best-effort */ swallowError('graphService:104', e) }
  }
}

async function loadPersistedGraph(): Promise<KnowledgeGraphData | null> {
  const api = window.electronAPI
  if (api?.fileStore) {
    try {
      const raw = await api.fileStore.readFile('knowledge_graph.json')
      if (raw) {
        const parsed = JSON.parse(raw) as KnowledgeGraphData
        if (parsed.nodes?.length && parsed.edges) return parsed
      }
    } catch (e) { /* ignore */ swallowError('graphService:117', e) }
  }
  return null
}

// ── Public API ───────────────────────────────────────────────────

export async function getGraphData(notes?: FloatingNote[]): Promise<KnowledgeGraphData> {
  // Delta merge: if cached, merge note extracts into existing data instead of rebuilding
  if (_cachedData) {
    if (notes?.length) {
      const extracted = extractFromNotes(notes)
      const merged = mergeGraphs(_cachedData, extracted)
      _cachedData = merged
    }
    return _cachedData
  }

  // 1. Try backend knowledge.graph first (once per session) — returns entities + relations
  // 仅在“成功拉到后端图”时才置位 _backendLoaded；若当时 WS 未连上/请求失败，
  // 保持 false，让下次 getGraphData（如用户点刷新按钮）能重试拉取最新图谱，
  // 避免旧逻辑在失败时也永久置位、导致本次会话再也看不到后端新增实体（如 AI 聊天抓取）。
  if (!_backendLoaded) {
    try {
      const client = getWsClient()
      if (client.status === 'connected') {
        const graph = await client.call<{ entities: BackendEntity[]; relations: BackendRelation[] }>('knowledge.graph', {})
        if (graph && Array.isArray(graph.entities) && graph.entities.length > 0) {
          const nodes: GraphNode[] = []
          const edgeSet = new Set<string>()
          const edges: GraphEdge[] = []
          const entityMap = new Map<string, GraphNode>()

          for (const ent of graph.entities) {
            const node = backendEntityToNode(ent)
            entityMap.set(node.id, node)
            nodes.push(node)
          }

          // Build edges from explicit relations
          for (const rel of graph.relations) {
            const sourceId = `bk-${rel.source_id}`
            const targetId = `bk-${rel.target_id}`
            // Only keep edges where both endpoints exist
            if (entityMap.has(sourceId) && entityMap.has(targetId)) {
              const ek = `${sourceId}|${targetId}|${rel.relation_type}`
              if (!edgeSet.has(ek)) {
                edgeSet.add(ek)
                edges.push({ source: sourceId, target: targetId, type: rel.relation_type })
              }
            }
          }

          const extracted = notes?.length ? extractFromNotes(notes) : { nodes: [], edges: [] }
          const merged = mergeGraphs({ nodes, edges }, extracted)
          _cachedData = merged
          _backendLoaded = true
          await saveGraphData(merged)
          return merged
        }
      }
    } catch (e) {
      console.warn('[graphService] backend knowledge.graph failed:', e)
    }
  }

  // 2. Load persisted from file
  const persisted = await loadPersistedGraph()

  // 3. Auto-extract from notes
  const extracted = notes?.length ? extractFromNotes(notes) : { nodes: [], edges: [] }

  // 4. Merge: persisted (user edits) > extracted (auto) > empty (fallback)
  if (persisted) {
    const merged = mergeGraphs(persisted, extracted)
    _cachedData = merged
    return merged
  }

  // 5. First time: merge empty + extracted
  const merged = mergeGraphs(EMPTY_GRAPH, extracted)

  // 6. Persist the merged result so user edits have a base
  await saveGraphData(merged)
  _cachedData = merged
  return merged
}

export function invalidateCache(): void {
  _cachedData = null
  _backendLoaded = false
  // Does NOT clear GLOBAL_POS_CACHE — positions survive to prevent flickering
}

/** Force a full reload from disk, discarding in-memory delta cache */
export async function reloadGraphData(notes?: FloatingNote[]): Promise<KnowledgeGraphData> {
  _cachedData = null
  _backendLoaded = false
  return getGraphData(notes)
}

// ── Global position cache (survives invalidateCache, persists across component mounts) ──
export const GLOBAL_POS_CACHE = new Map<string, { x: number; y: number }>()

export function clearPositionCache(): void {
  GLOBAL_POS_CACHE.clear()
}

// ── Knowledge synthesis (atomic cards + topic MOCs) ───────────

/** 拉取原子知识卡片（后端 knowledge.cards） */
export async function fetchKnowledgeCards(days: number = 0): Promise<KnowledgeCard[]> {
  const client = getWsClient()
  if (client.status !== 'connected') return []
  try {
    const cards = await client.call<KnowledgeCard[]>('knowledge.cards', { days })
    return Array.isArray(cards) ? cards : []
  } catch (e) {
    console.warn('[graphService] knowledge.cards failed:', e)
    return []
  }
}

/** 拉取主题 MOC（后端 knowledge.mocs） */
export async function fetchTopicMOCs(): Promise<TopicMOC[]> {
  const client = getWsClient()
  if (client.status !== 'connected') return []
  try {
    const mocs = await client.call<TopicMOC[]>('knowledge.mocs', {})
    return Array.isArray(mocs) ? mocs : []
  } catch (e) {
    console.warn('[graphService] knowledge.mocs failed:', e)
    return []
  }
}

/** 触发后端知识归纳（卡片→主题 MOC + 回写图谱主题） */
export async function triggerKnowledgeSynthesize(): Promise<{ success: boolean; topics?: number; cards?: number; error?: string }> {
  const client = getWsClient()
  if (client.status !== 'connected') return { success: false, error: 'not_connected' }
  try {
    return await client.call<{ success: boolean; topics?: number; cards?: number; error?: string }>('knowledge.synthesize', {})
  } catch (e) {
    console.warn('[graphService] knowledge.synthesize failed:', e)
    return { success: false, error: String(e) }
  }
}

// ── 知识库 MD 内容层（阶段三：时间树/主题树/MD 编辑器） ──────────

export interface MdFileMeta {
  title?: string
  type?: string
  topic?: string
  date?: string
  [k: string]: unknown
}

/** 拉取知识库文件树（knowledge.md_list） */
export async function fetchMdList(): Promise<{ files: string[]; root: string }> {
  const client = getWsClient()
  if (client.status !== 'connected') return { files: [], root: '' }
  try {
    const res = await client.call<{ success: boolean; files?: string[]; root?: string; error?: string }>('knowledge.md_list', {})
    if (res?.success && Array.isArray(res.files)) return { files: res.files, root: res.root || '' }
    return { files: [], root: '' }
  } catch (e) {
    console.warn('[graphService] knowledge.md_list failed:', e)
    return { files: [], root: '' }
  }
}

/** 读取单个 MD 文件（knowledge.md_get） */
export async function fetchMdGet(rel: string): Promise<{ content: string; meta: MdFileMeta } | null> {
  const client = getWsClient()
  if (client.status !== 'connected') return null
  try {
    const res = await client.call<{ success: boolean; content?: string; meta?: MdFileMeta; error?: string }>('knowledge.md_get', { rel })
    if (res?.success && typeof res.content === 'string') return { content: res.content, meta: res.meta || {} }
    return null
  } catch (e) {
    console.warn('[graphService] knowledge.md_get failed:', e)
    return null
  }
}

/** 保存 MD 文件并触发反向回灌（knowledge.md_save） */
export async function saveMdFile(rel: string, content: string): Promise<{ success: boolean; sync?: { success?: boolean; skipped?: boolean; action?: string; error?: string }; error?: string }> {
  const client = getWsClient()
  if (client.status !== 'connected') return { success: false, error: 'not_connected' }
  try {
    return await client.call<{ success: boolean; sync?: { success?: boolean; skipped?: boolean; action?: string; error?: string }; error?: string }>('knowledge.md_save', { rel, content })
  } catch (e) {
    console.warn('[graphService] knowledge.md_save failed:', e)
    return { success: false, error: String(e) }
  }
}

/** 手动触发 MD 反向同步（knowledge.md_sync） */
export async function syncMdFile(rel: string): Promise<{ success: boolean; error?: string }> {
  const client = getWsClient()
  if (client.status !== 'connected') return { success: false, error: 'not_connected' }
  try {
    return await client.call<{ success: boolean; error?: string }>('knowledge.md_sync', { rel })
  } catch (e) {
    console.warn('[graphService] knowledge.md_sync failed:', e)
    return { success: false, error: String(e) }
  }
}

export async function addNode(data: KnowledgeGraphData, label: string, type: string): Promise<KnowledgeGraphData> {
  const id = nodeId(label)
  const updated: KnowledgeGraphData = {
    ...data,
    nodes: data.nodes.some(n => n.id === id) ? data.nodes : [...data.nodes, { id, label, type }],
  }
  _cachedData = updated
  await saveGraphData(updated)
  return updated
}

export async function removeNode(data: KnowledgeGraphData, nodeId: string): Promise<KnowledgeGraphData> {
  const updated: KnowledgeGraphData = {
    ...data,
    nodes: data.nodes.filter(n => n.id !== nodeId),
    edges: data.edges.filter(e => e.source !== nodeId && e.target !== nodeId),
  }
  _cachedData = updated
  await saveGraphData(updated)
  return updated
}

export async function addEdge(data: KnowledgeGraphData, source: string, target: string, type: string): Promise<KnowledgeGraphData> {
  const updated: KnowledgeGraphData = {
    ...data,
    edges: [...data.edges, { source, target, type }],
  }
  _cachedData = updated
  await saveGraphData(updated)
  return updated
}

export async function removeEdge(data: KnowledgeGraphData, source: string, target: string): Promise<KnowledgeGraphData> {
  const updated: KnowledgeGraphData = {
    ...data,
    edges: data.edges.filter(e => !(e.source === source && e.target === target)),
  }
  _cachedData = updated
  await saveGraphData(updated)
  return updated
}

export function getNodeCount(data: KnowledgeGraphData): number {
  return data.nodes.length
}

export function getEdgeCount(data: KnowledgeGraphData): number {
  return data.edges.length
}

export function getConnectedNodes(nodeId: string, edges: KnowledgeGraphData['edges']): string[] {
  const connected = new Set<string>()
  for (const e of edges) {
    if (e.source === nodeId) connected.add(e.target)
    if (e.target === nodeId) connected.add(e.source)
  }
  return Array.from(connected)
}

export function getNodeConnections(nodeId: string, edges: KnowledgeGraphData['edges']): GraphEdge[] {
  return edges.filter((e: GraphEdge) => e.source === nodeId || e.target === nodeId)
}

export function searchNodes(data: KnowledgeGraphData, query: string): GraphNode[] {
  const q = query.toLowerCase().trim()
  if (!q) return data.nodes
  return data.nodes.filter((n: GraphNode) => n.label.toLowerCase().includes(q) || n.type.toLowerCase().includes(q))
}

// ── Backend knowledge graph types ──────────────────────────

interface BackendEntity {
  id: string
  name: string
  type: string
  aliases?: string[]
  properties?: Record<string, unknown>
}

interface BackendRelation {
  source_id: string
  target_id: string
  relation_type: string
  confidence?: number
  source_entity?: string
  target_entity?: string
}

interface BackendGraph {
  entities: BackendEntity[]
  relations: BackendRelation[]
}

/** 归一化后端 properties：非对象（null / 字符串 / 缺字段）一律视作无 */
export function normalizeProperties(props: unknown): Record<string, unknown> | undefined {
  if (!props || typeof props !== 'object' || Array.isArray(props)) return undefined
  const entries = Object.entries(props as Record<string, unknown>).filter(([, v]) => v !== null && v !== undefined)
  return entries.length ? Object.fromEntries(entries) : undefined
}

/** 从后端实体读取来源主题（properties.topic），无则返回 undefined */
function readTopic(ent: BackendEntity): string | undefined {
  const props = ent.properties as Record<string, unknown> | undefined
  const topic = props?.topic
  return typeof topic === 'string' && topic.trim() ? topic.trim() : undefined
}

/** 从后端实体读取业务类型（properties.kind：knowledge_card / topic_moc） */
function readKind(ent: BackendEntity): string | undefined {
  const props = ent.properties as Record<string, unknown> | undefined
  const kind = props?.kind
  return typeof kind === 'string' && kind.trim() ? kind.trim() : undefined
}

/**
 * V1-5 图谱下钻单点：后端实体 → 前端 GraphNode。
 * 三处转换点（getGraphData / searchBackendKnowledge / expandBackendNode）统一走这里，
 * 保证 properties 不再被丢弃（历史上 properties 在 :155 / :467 / :551 三处被吃掉）。
 */
export function backendEntityToNode(ent: BackendEntity): GraphNode {
  return {
    id: `bk-${ent.id}`,
    label: ent.name || 'Unknown',
    type: ent.type || 'concept',
    topic: readTopic(ent),
    kind: readKind(ent),
    properties: normalizeProperties(ent.properties),
  }
}

/** 详情面板展示用的属性条目（过滤内部键，值统一转字符串） */
export function propertyEntries(node: GraphNode | null): Array<{ key: string; value: string }> {
  const props = node?.properties
  if (!props) return []
  const HIDDEN = new Set(['topic', 'kind'])
  return Object.entries(props)
    .filter(([k, v]) => !HIDDEN.has(k) && v !== null && v !== undefined && typeof v !== 'object')
    .map(([k, v]) => ({ key: k, value: String(v) }))
    .slice(0, 12)
}

/**
 * 解析节点可跳转的来源笔记 ID。
 * 后端约定：properties.source_id 形如 "note:<id>"（graph_store.remove_by_source 同源规则），
 * 亦兼容直接给 note_id 的实体。缺 source_id 时返回 null（不强造，登记 M1 后续）。
 */
export function resolveSourceNoteId(node: GraphNode | null): string | null {
  const props = node?.properties
  if (!props) return null
  const raw = props.source_id ?? props.note_id
  if (typeof raw !== 'string' && typeof raw !== 'number') return null
  const s = String(raw).trim()
  if (!s) return null
  return s.startsWith('note:') ? s.slice('note:'.length) : s
}

/** 该节点是否能跳到本地真实存在的笔记（本地无此笔记时不给假链接） */
export function findLocalNoteId(node: GraphNode | null, notes: Array<{ id: string }>): string | null {
  const id = resolveSourceNoteId(node)
  if (!id) return null
  return notes.some(n => n.id === id) ? id : null
}

/** Search the backend knowledge graph and merge results into local data */
export async function searchBackendKnowledge(
  localData: KnowledgeGraphData,
  _query: string,
  _entityType?: string,
): Promise<KnowledgeGraphData> {
  const client = getWsClient()
  if (client.status !== 'connected') return localData

  try {
    // Fetch full graph data from backend
    const graph = await client.call<BackendGraph>('knowledge.graph', {})
    if (!graph || !Array.isArray(graph.entities) || graph.entities.length === 0) return localData

    // Build merged graph
    const nodeMap = new Map<string, GraphNode>()
    for (const n of localData.nodes) nodeMap.set(n.id, n)

    const edgeSet = new Set<string>()
    const edges: GraphEdge[] = [...localData.edges]
    for (const e of edges) edgeSet.add(`${e.source}|${e.target}|${e.type}`)

    for (const ent of graph.entities) {
      const id = `bk-${ent.id}`
      if (!nodeMap.has(id)) {
        nodeMap.set(id, backendEntityToNode(ent))
      }
    }

    for (const rel of graph.relations) {
      const sourceId = `bk-${rel.source_id}`
      const targetId = `bk-${rel.target_id}`
      if (nodeMap.has(sourceId) && nodeMap.has(targetId)) {
        const ek = `${sourceId}|${targetId}|${rel.relation_type}`
        if (!edgeSet.has(ek)) {
          edgeSet.add(ek)
          edges.push({ source: sourceId, target: targetId, type: rel.relation_type })
        }
      }
    }

    return {
      nodes: Array.from(nodeMap.values()),
      edges,
    }
  } catch {
    return localData
  }
}

/**
 * 计算节点的 1-2 跳邻居子图（Local Graph）。
 * focusId 为 null 时返回原始图。
 */
export function getLocalSubgraph(
  data: KnowledgeGraphData,
  focusId: string | null,
  hops: number = 2,
): KnowledgeGraphData {
  if (!focusId) return data
  const reachable = new Set<string>([focusId])
  let frontier = new Set<string>([focusId])
  for (let h = 0; h < hops; h++) {
    const next = new Set<string>()
    for (const id of frontier) {
      for (const e of data.edges) {
        if (e.source === id && !reachable.has(e.target)) { reachable.add(e.target); next.add(e.target) }
        if (e.target === id && !reachable.has(e.source)) { reachable.add(e.source); next.add(e.source) }
      }
    }
    frontier = next
    if (frontier.size === 0) break
  }
  const nodes = data.nodes.filter(n => reachable.has(n.id))
  const edges = data.edges.filter(e => reachable.has(e.source) && reachable.has(e.target))
  return { nodes, edges }
}

/** Expand a backend node: traverse from the entity to discover more connections */
export async function expandBackendNode(
  localData: KnowledgeGraphData,
  backendEntityId: string,
  maxHops: number = 1,
): Promise<KnowledgeGraphData> {
  const client = getWsClient()
  if (client.status !== 'connected') return localData

  // Strip the "bk-" prefix to get the real backend entity ID
  const realId = backendEntityId.startsWith('bk-') ? backendEntityId.slice(3) : backendEntityId

  try {
    const result = await client.call<BackendGraph>('knowledge.traverse', {
      start_id: realId,
      max_hops: maxHops,
    })
    if (!result || !Array.isArray(result.entities) || result.entities.length === 0) return localData

    // Merge discovered entities and relations into local data
    const nodeMap = new Map<string, GraphNode>()
    for (const n of localData.nodes) nodeMap.set(n.id, n)

    const edgeSet = new Set<string>()
    const edges: GraphEdge[] = [...localData.edges]
    for (const e of edges) edgeSet.add(`${e.source}|${e.target}|${e.type}`)

    let addedCount = 0
    for (const ent of result.entities) {
      const id = `bk-${ent.id}`
      // 已有节点同样要补写 properties：traverse 常返回带完整属性的焦点实体本身，
      // 若只在 !has(id) 分支写入，点开焦点节点仍是空详情。
      const prev = nodeMap.get(id)
      if (!prev) {
        nodeMap.set(id, backendEntityToNode(ent))
        addedCount++
      } else if (!prev.properties && ent.properties) {
        nodeMap.set(id, { ...prev, properties: normalizeProperties(ent.properties), topic: prev.topic ?? readTopic(ent), kind: prev.kind ?? readKind(ent) })
      }
    }

    for (const rel of result.relations) {
      const sourceId = `bk-${rel.source_id}`
      const targetId = `bk-${rel.target_id}`
      if (nodeMap.has(sourceId) && nodeMap.has(targetId)) {
        const ek = `${sourceId}|${targetId}|${rel.relation_type}`
        if (!edgeSet.has(ek)) {
          edgeSet.add(ek)
          edges.push({ source: sourceId, target: targetId, type: rel.relation_type })
        }
      }
    }

    if (addedCount === 0) return localData // nothing new

    const merged: KnowledgeGraphData = { nodes: Array.from(nodeMap.values()), edges }
    _cachedData = merged
    await saveGraphData(merged)
    return merged
  } catch {
    return localData
  }
}

// ── 后端实体删除（P2-PD-NODEL 接线，2026-09-21）───────────────────
//
// 历史缺口：后端 2026-09-18 就有了 `knowledge.entity.delete`（store 早有
// remove_entity），但全仓**无任何调用方** —— UI 上提错的实体只能靠删来源
// 笔记或重建全图收拾。本函数把它接进图谱详情面板的「删除节点」。
//
// 诚实性约束（照抄同域既有教训）：
// * 该方法属 R4 破坏性操作，未审批时后端返回 `{pending:true}` **且不执行** →
//   本地也**不得**先删，否则刷新后节点复活，等于骗用户（组件陷阱：删完
//   再提示会因详情面板卸载而无处渲染，提示一律上抛到模块级）；
// * RPC 异常/失败 → 本地保持原样，不把「没删成」说成已删；
// * 仅本地派生节点（笔记/标签/WikiLink 抽取）后端无对应实体 → 走本地移除。

export type DeleteEntityOutcome =
  | 'deleted'      // 后端已删除且落盘
  | 'pending'      // R4 审批闸门：已入队，未执行
  | 'not_found'    // 后端无此实体（陈旧节点）→ 本地一并清掉
  | 'failed'       // 后端拒绝/RPC 异常/落盘失败
  | 'unavailable'  // 后端未连接 → 降级为仅本地移除
  | 'local_only'   // 本地派生节点，本就没有后端实体

export interface DeleteEntityResult {
  data: KnowledgeGraphData
  outcome: DeleteEntityOutcome
  error?: string
}

interface DeleteEntityResponse {
  success?: boolean
  deleted?: boolean
  error?: string
  pending?: boolean
  requires_approval?: boolean
  message?: string
}

// ── 节点详情归一层（P4-KG-DETAIL 前端消费，2026-09-26）─────
//
// 为什么不在前端拼：图谱 6+ 条写入通道往 properties 各写各的键（ent_*/card_*/
// moc_*/external_content_*），旧面板只认 `source_id`——生产图谱 739 实体里
// 它 **0 个**，而 658 个有 `sources` 但因为是数组被 propertyEntries 丢弃，
// 所以「点开节点没有完整信息可显示」。现在后端
// `knowledge.entity.detail`（knowledge/node_profile.py）一次返回归一结果。

export interface EntityOrigin {
  raw: string
  kind: string
  source_id: string
  label: string
  target_module: string
  jumpable: boolean
  title: string
  resolved: boolean
  inferred?: boolean
}

export interface EntityRelation {
  relation_type: string
  direction: 'in' | 'out'
  other_id: string
  other_name: string
  other_type: string
  confidence?: number
  description?: string
}

export interface EntityCardBody {
  entity_type?: string
  title?: string
  content?: string
  detail?: string
  expansion?: string
  analysis?: string
}

export interface EntityDetail {
  found: boolean
  entity?: {
    id: string
    name: string
    type: string
    type_display: string
    aliases: string[]
    topic: string
    kind: string
    title: string
    /** P4-KG-DETAIL（2026-10-15）：「这个实体是什么」——LLM 提取时一次性归纳，
     *  存量经 detail_backfill 回填；空串 = 暂无详情（面板显示占位，不编造）。 */
    detail: string
    created_at: string
    updated_at: string
    source_id: string
  }
  degree?: number
  origins?: EntityOrigin[]
  unresolved_origin_count?: number
  relations?: EntityRelation[]
  relations_by_predicate?: Record<string, number>
  card?: EntityCardBody | null
  has_body_text?: boolean
  error?: string
}

/**
 * 当前前端真正能打开的目标模块。**后端 jumpable 为真不代表这里可跳**——
 * 后端只知道“出处属于哪个域”，路由能不能接上取决于桌面是否有该模块入口与
 * 打开单条的能力。接不了就不给按钮，不给假链接（与 resolveSourceNoteId 同一纪律）。
 */
export const JUMPABLE_TARGET_MODULES = new Set(['notes'])

export function canJumpOrigin(origin: EntityOrigin | null | undefined): boolean {
  if (!origin?.jumpable) return false
  const module = String(origin.target_module || '')
  const id = String(origin.source_id || '').trim()
  return Boolean(id) && JUMPABLE_TARGET_MODULES.has(module)
}

/** 拉取单实体归一详情（后端不可达/无此实体时返回 null，由面板降级展示本地字段）。 */
export async function fetchEntityDetail(entityId: string): Promise<EntityDetail | null> {
  const client = getWsClient()
  if (client.status !== 'connected') return null
  const realId = entityId.startsWith('bk-') ? entityId.slice(3) : entityId
  if (!realId) return null
  try {
    const res = await client.call<EntityDetail>('knowledge.entity.detail', { entity_id: realId })
    return res && res.found ? res : null
  } catch (e) {
    console.warn('[graphService] knowledge.entity.detail failed:', e)
    return null
  }
}

/** 后端实体删除回执 → 结果对象（不直接改图，交由 removeNode 落本地） */
export async function deleteEntity(data: KnowledgeGraphData, node: GraphNode): Promise<DeleteEntityResult> {
  if (!node?.id) return { data, outcome: 'failed', error: 'node required' }

  // 非后端节点：本地视图移除即为全部语义
  if (!node.id.startsWith('bk-')) {
    return { data: await removeNode(data, node.id), outcome: 'local_only' }
  }

  const client = getWsClient()
  if (client.status !== 'connected') {
    return { data: await removeNode(data, node.id), outcome: 'unavailable' }
  }

  const realId = node.id.slice('bk-'.length)
  let resp: DeleteEntityResponse | null = null
  try {
    resp = await client.call<DeleteEntityResponse>('knowledge.entity.delete', { entity_id: realId })
  } catch (e) {
    return { data, outcome: 'failed', error: String(e) }
  }

  if (!resp) return { data, outcome: 'failed', error: 'empty response' }

  // R4：已提交审批但**尚未执行** → 图保持原样
  if (resp.pending || resp.requires_approval) {
    return { data, outcome: 'pending', error: resp.message }
  }

  if (resp.deleted) {
    const next = await removeNode(data, node.id)
    // success=false + deleted=true：内存删了但 save() 失败（重启会复活），如实上报
    return resp.success
      ? { data: next, outcome: 'deleted' }
      : { data: next, outcome: 'failed', error: resp.error || 'save failed' }
  }

  if (resp.error === 'not found') {
    return { data: await removeNode(data, node.id), outcome: 'not_found' }
  }

  return { data, outcome: 'failed', error: resp.error }
}
