import React, { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import { useStore } from '../../store/useStore'
import ForceGraph from '../Graph/ForceGraph'
import GraphMindMap from '../Graph/GraphMindMap'
import { getGraphData, getNodeConnections, searchNodes, addNode, addEdge, reloadGraphData, searchBackendKnowledge, expandBackendNode, fetchKnowledgeCards, fetchTopicMOCs, triggerKnowledgeSynthesize, getLocalSubgraph, propertyEntries, findLocalNoteId, deleteEntity, fetchEntityDetail, canJumpOrigin, type EntityDetail } from '../Graph/graphService'
import type { KnowledgeGraphData, GraphNode, GraphEdge, KnowledgeCard, TopicMOC } from '../../types'
import { NODE_TYPES, CHINESE_TO_TYPE, DEFAULT_NODE_TYPE, DEFAULT_EDGE_TYPE, nodeColor, buildNodeTypeDisplay } from './KnowledgeGraphData'
import PageHeader from '../Common/PageHeader'

const ei = window.electronAPI as ElectronAPI | undefined

// 记忆容器溯源节点标签前缀（后端生成的固定中文，属数据匹配而非 UI 文案；
// 用 unicode 转义避免被 i18n 硬编码检测误报）
const MEMORY_NODE_LABEL_PREFIX = '\u5bf9\u8bdd ' // '对话 '

const KnowledgeGraphModule: React.FC = () => {
  const { t } = useTranslation()
  // 无主题占位文案：同一渲染内用于生成选项值并与过滤条件比对，需保持一致
  const noTopicLabel = t('knowledgeGraph.noTopic')
  const floatingNotes = useStore(s => s.floatingNotes)
  // V1-5 图谱下钻：节点详情 → 跳回源笔记（笔记模块内打开编辑器）
  const setEditingNoteId = useStore(s => s.setEditingNoteId)
  const setActiveModule = useStore(s => s.setActiveModule)
  const [data, setData] = useState<KnowledgeGraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null)
  const [filterType, setFilterType] = useState<string | null>(null)
  const [filterTopic, setFilterTopic] = useState<string | null>(null)
  const [focusId, setFocusId] = useState<string | null>(null)
  const [alwaysShowLabels, setAlwaysShowLabels] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 800, h: 600 })

  // Add node modal
  const [showAddNode, setShowAddNode] = useState(false)
  const [newNodeLabel, setNewNodeLabel] = useState('')
  const [newNodeType, setNewNodeType] = useState(DEFAULT_NODE_TYPE)
  // Add edge modal
  const [showAddEdge, setShowAddEdge] = useState(false)
  const [newEdgeSource, setNewEdgeSource] = useState('')
  const [newEdgeTarget, setNewEdgeTarget] = useState('')
  const [newEdgeType, setNewEdgeType] = useState(DEFAULT_EDGE_TYPE)
  const [searchingBackend, setSearchingBackend] = useState(false)
  const [expanding, setExpanding] = useState(false)
  // 模块级操作回执（删除后端实体等）。**不放在详情面板里**：删完节点后
  // selectedNode 会置 null、面板卸载，消息将无处渲染（已踩过的组件陷阱）。
  const [actionNotice, setActionNotice] = useState<string | null>(null)
  // P4-KG-DETAIL：选中节点后向后端要归一详情（出处/邻居/卡片正文）。
  // null = 未加载或后端不可达 → 面板降级为本地字段，不谎称“无来源”。
  const [detail, setDetail] = useState<EntityDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const detailSeq = useRef(0)
  // V1-5 修复（2026-09-15）：默认改为 graph 视图。
  // 原默认 'cards' 导致节点详情面板（renderDetail 仅在 graph/mindmap 分支挂载）在默认视图下
  // 完全不可达 —— 体感"关系节点只能看标签、点击看不了细节"。卡片视图仍可经切换器进入。
  const [viewMode, setViewMode] = useState<'graph' | 'mindmap' | 'cards'>('graph')
  // 知识卡片 / 主题 MOC（卡片视图）
  const [knowledgeCards, setKnowledgeCards] = useState<KnowledgeCard[]>([])
  const [topicMOCs, setTopicMOCs] = useState<TopicMOC[]>([])
  const [cardsLoading, setCardsLoading] = useState(false)
  const [synthesizing, setSynthesizing] = useState(false)
  // 卡片展开详情（展示 详细解释/相关拓展/分析）
  const [expandedCardId, setExpandedCardId] = useState<string | null>(null)

  // 卡片视图：拉取卡片 + MOC
  const loadCards = useCallback(async () => {
    setCardsLoading(true)
    const [cards, mocs] = await Promise.all([fetchKnowledgeCards(), fetchTopicMOCs()])
    setKnowledgeCards(cards)
    setTopicMOCs(mocs)
    setCardsLoading(false)
  }, [])

  useEffect(() => {
    if (viewMode === 'cards') loadCards()
  }, [viewMode, loadCards])

  // Map taxonomy keys (data, in Chinese) to localized display labels.
  const nodeTypeDisplay = useMemo<Record<string, string>>(() => buildNodeTypeDisplay(t), [t])

  const localizeType = useCallback((ty: string): string => nodeTypeDisplay[ty] || ty, [nodeTypeDisplay])

  const loadData = useCallback(async (forceReload = false) => {
    if (forceReload) setLoading(true)
    const d = forceReload ? await reloadGraphData(floatingNotes) : await getGraphData(floatingNotes)
    setData(d)
    setLoading(false)
  }, [floatingNotes])

  // Load data
  useEffect(() => {
    loadData()
  }, [loadData])

  // Measure container
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      for (const entry of entries) {
        setSize({ w: entry.contentRect.width, h: entry.contentRect.height })
      }
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  // Extract unique node types for filter
  const nodeTypes: string[] = useMemo(() => {
    if (!data) return []
    const types = new Set(data.nodes.map((n: GraphNode) => n.type_display || n.type))
    return Array.from(types).sort()
  }, [data])

  // 提取用于过滤的主题列表（取出现次数最多的前 20 个 + 无主题）
  const topicOptions: string[] = useMemo(() => {
    if (!data) return []
    const counts = new Map<string, number>()
    for (const n of data.nodes) {
      const topicName = n.topic && n.topic.trim() ? n.topic : noTopicLabel
      counts.set(topicName, (counts.get(topicName) || 0) + 1)
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 20)
      .map(([t]) => t)
  }, [data, noTopicLabel])

  // Filtered data
  const filtered = useMemo<{ nodes: GraphNode[]; edges: GraphEdge[] }>(() => {
    if (!data) return { nodes: [], edges: [] }
    let nodes = searchQuery ? searchNodes(data, searchQuery) : data.nodes
    // 过滤记忆容器（「对话 xxx」溯源节点）——图谱视图只展示知识网络
    nodes = nodes.filter((n: GraphNode) => !n.id.startsWith('mem_') && !(n.label || '').startsWith(MEMORY_NODE_LABEL_PREFIX))
    if (filterType) {
      nodes = nodes.filter((n: GraphNode) => n.type === filterType)
    }
    if (filterTopic) {
      nodes = nodes.filter((n: GraphNode) => (n.topic && n.topic.trim() ? n.topic : noTopicLabel) === filterTopic)
    }
    const nodeIds = new Set(nodes.map((n: GraphNode) => n.id))
    const edges = data.edges.filter((e: GraphEdge) => nodeIds.has(e.source) && nodeIds.has(e.target))
    return { nodes, edges }
  }, [data, searchQuery, filterType, filterTopic, noTopicLabel])

  // 聚焦模式下，把 filtered 收缩为选中节点的 1-2 跳局部子图
  const displayData = useMemo<{ nodes: GraphNode[]; edges: GraphEdge[] }>(() => {
    if (!focusId) return filtered
    return getLocalSubgraph(filtered, focusId, 2)
  }, [filtered, focusId])

  const handleNodeClick = useCallback((node: GraphNode) => {
    setSelectedNode(prev => (prev?.id === node.id ? null : node))
  }, [])

  // 选中节点→拉归一详情。旧请求后到不能覆盖新选中项（乱序回执是这类面板
  // 的经典错数据源），用序号丢弃过期结果。
  useEffect(() => {
    const id = selectedNode?.id
    if (!id) { setDetail(null); setDetailLoading(false); return }
    if (!id.startsWith('bk-')) { setDetail(null); return }
    const seq = ++detailSeq.current
    setDetailLoading(true)
    void fetchEntityDetail(id).then(res => {
      if (seq !== detailSeq.current) return
      setDetail(res)
      setDetailLoading(false)
    })
  }, [selectedNode?.id])

  const handleClearSelection = useCallback(() => setSelectedNode(null), [])

  const handleAddNode = async () => {
    if (!data || !newNodeLabel.trim()) return
    const updated = await addNode(data, newNodeLabel.trim(), CHINESE_TO_TYPE[newNodeType] || newNodeType)
    setData(updated)
    setNewNodeLabel('')
    setNewNodeType(DEFAULT_NODE_TYPE)
    setShowAddNode(false)
  }

  const handleRemoveNode = async (id: string) => {
    if (!data) return
    const node = data.nodes.find(n => n.id === id)
    if (!node) return
    // P2-PD-NODEL 接线：后端实体（bk- 前缀）先过 `knowledge.entity.delete`
    // （R4 未批准时后端只入队不执行 → 本地也不删，绝不假删）。
    const res = await deleteEntity(data, node)
    setData(res.data)
    setSelectedNode(null)
    switch (res.outcome) {
      case 'deleted':
        setActionNotice(t('knowledgeGraph.deleteBackendDone'))
        break
      case 'not_found':
        setActionNotice(t('knowledgeGraph.deleteBackendGone'))
        break
      case 'pending':
        setActionNotice(t('knowledgeGraph.deletePendingApproval'))
        break
      case 'unavailable':
        setActionNotice(t('knowledgeGraph.deleteOfflineLocalOnly'))
        break
      case 'failed':
        setActionNotice(t('knowledgeGraph.deleteBackendFailedFmt', { error: res.error || 'unknown' }))
        break
      default:
        // local_only：本地派生节点，行为与以往一致，不打扰
        setActionNotice(null)
    }
  }

  // 回执自动收起（8s，避免切走再回来仍挂着一旧提示）
  useEffect(() => {
    if (!actionNotice) return
    const timer = setTimeout(() => setActionNotice(null), 8000)
    return () => clearTimeout(timer)
  }, [actionNotice])

  const handleAddEdge = async () => {
    if (!data || !newEdgeSource.trim() || !newEdgeTarget.trim()) return
    const updated = await addEdge(data, newEdgeSource.trim(), newEdgeTarget.trim(), newEdgeType)
    setData(updated)
    setNewEdgeSource('')
    setNewEdgeTarget('')
    setNewEdgeType(DEFAULT_EDGE_TYPE)
    setShowAddEdge(false)
  }

  // Node detail panel
  const renderDetail = () => {
    if (!selectedNode || !data) return null
    const connections: GraphEdge[] = getNodeConnections(selectedNode.id, data.edges)
    // V1-5 图谱下钻：properties 透传后可见内容 + 可跳源笔记（缺 source_id 不给假链接）
    const propRows = propertyEntries(selectedNode)
    const sourceNoteId = findLocalNoteId(selectedNode, floatingNotes)
    // P4-KG-DETAIL：后端归一详情优先（出处带标题/可跳标记、邻居带方向、卡片正文）；
    // 后端不可达时 detail=null → 下面的区块自动只展示本地字段，不假装“无来源”。
    const aliases = detail?.entity?.aliases ?? []
    const origins = detail?.origins ?? []
    const body = detail?.card ?? null
    const nodeIds = new Set(data.nodes.map(n => n.id))
    type RelRow = { key: string; name: string; pred: string; dir?: string; targetId?: string }
    const relRows: RelRow[] = detail?.relations?.length
      ? detail.relations.map(r => ({
          key: `${r.direction}|${r.other_id}|${r.relation_type}`,
          name: r.other_name || r.other_id,
          pred: r.relation_type,
          dir: r.direction,
          targetId: `bk-${r.other_id}`,
        }))
      : connections.flatMap((e, i): RelRow[] => {
          const otherId = e.source === selectedNode.id ? e.target : e.source
          const other = data.nodes.find((n: GraphNode) => n.id === otherId)
          if (!other) return []
          return [{ key: `local-${i}`, name: other.label, pred: e.relation_type_display || e.type || t('knowledgeGraph.defaultRelation'), targetId: otherId }]
        })
    const jumpToNeighbor = (row: RelRow) => {
      if (!row.targetId || !nodeIds.has(row.targetId)) return
      const target = data.nodes.find((n: GraphNode) => n.id === row.targetId)
      if (target) setSelectedNode(target)
    }
    return (
      <div className="flex flex-col w-60 flex-shrink-0 border-l" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
        <div className="flex items-center justify-between px-3 py-2 border-b" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
          <span className="text-xs font-medium text-text-secondary">{t('nav.knowledgeGraph')}</span>
          <button onClick={handleClearSelection} className="text-2xs text-text-muted hover:text-text-secondary transition-colors">
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>
        <div className="flex flex-col items-center py-4 px-3 border-b" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
          <div
            className="w-8 h-8 rounded-full flex items-center justify-center text-sm mb-2"
            style={{ background: `${nodeColor(selectedNode.type)}20`, color: nodeColor(selectedNode.type) }}
          >
            <Icon icon="fa-solid fa-circle-nodes" />
          </div>
          <span className="text-sm font-medium text-text-primary break-all">{selectedNode.label}</span>
          <span className="text-2xs text-text-muted mt-0.5">{selectedNode.type_display || localizeType(selectedNode.type)}</span>
          <span className="text-[8px] text-text-dim mt-0.5 break-all">{selectedNode.id}</span>
          {/* P4-KG-DETAIL：实体详情（LLM 提取一次性归纳 / 存量 backfill）；空=暂无详情，诚实占位不编造 */}
          {detail?.entity?.detail ? (
            <p className="mt-2 text-2xs text-text-secondary leading-relaxed break-words text-left w-full">
              {detail.entity.detail}
            </p>
          ) : (
            detail && <span className="mt-1.5 text-2xs text-text-dim">{t('knowledgeGraph.noDetail')}</span>
          )}
          {/* Delete node */}
          <button
            onClick={() => handleRemoveNode(selectedNode.id)}
            className="mt-2 px-2 py-0.5 rounded text-2xs text-red-400/60 hover:bg-red-400/10 transition-colors"
          >
            <Icon icon="fa-solid fa-trash-can mr-1" />
            {t('knowledgeGraph.deleteNode')}
          </button>
          {/* Jump to source note (properties.source_id → 本地笔记) */}
          {sourceNoteId && (
            <button
              onClick={() => { setActiveModule('notes'); setEditingNoteId(sourceNoteId) }}
              className="mt-1 px-2 py-0.5 rounded text-2xs text-emerald-400/70 hover:bg-emerald-400/10 transition-colors"
            >
              <Icon icon="fa-solid fa-arrow-up-right-from-square mr-1" />
              {t('knowledgeGraph.openSourceNote')}
            </button>
          )}
          {/* Expand from backend */}
          {selectedNode.id.startsWith('bk-') && (
            <button
              onClick={async () => {
                if (!data || expanding) return
                setExpanding(true)
                const updated = await expandBackendNode(data, selectedNode.id)
                setData(updated)
                setExpanding(false)
              }}
              disabled={expanding}
              className="mt-1 px-2 py-0.5 rounded text-2xs text-cyan-400/60 hover:bg-cyan-400/10 transition-colors disabled:opacity-30"
            >
              <Icon icon={`fa-solid fa-cloud-arrow-down ${expanding ? 'animate-pulse' : ''} mr-1`} />
              {expanding ? t('knowledgeGraph.expanding') : t('knowledgeGraph.expandRelations')}
            </button>
          )}
        </div>
        {(selectedNode.topic || propRows.length > 0) ? (
          <div className="px-3 py-2 border-b" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
            <span className="text-2xs text-text-muted uppercase tracking-wider">{t('knowledgeGraph.propertiesTitle')}</span>
            <div className="mt-1.5 space-y-1">
              {selectedNode.topic && (
                <div className="flex items-start gap-1.5 text-2xs">
                  <span className="text-text-dim flex-shrink-0">topic</span>
                  <span className="text-text-secondary break-all">{selectedNode.topic}</span>
                </div>
              )}
              {propRows.map(row => (
                <div key={row.key} className="flex items-start gap-1.5 text-2xs">
                  <span className="text-text-dim flex-shrink-0">{row.key}</span>
                  <span className="text-text-secondary break-all" title={row.value}>{row.value}</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          // V1-5 修复（2026-09-15）：无属性节点兜底。
          // 此前条件为 && 短路——无 topic 且无 properties 时整块消失，面板只剩标签/类型/id，
          // 体感「点了没细节」（本地自动抽取节点通常无 properties）。
          // i18n 修复（2026-09-16）：下方文案原为硬编码中文，被 i18n-batch1 门禁拦下，改走 t()。
          <div className="px-3 py-2 border-b" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
            <span className="text-2xs text-text-muted uppercase tracking-wider">{t('knowledgeGraph.propertiesTitle')}</span>
            <div className="mt-1.5 text-2xs text-text-dim leading-relaxed">
              {t('knowledgeGraph.noProperties')}
            </div>
          </div>
        )}
        {selectedNode.id && (
          <div className="px-3 py-2 flex-1 overflow-y-auto">
            {/* 别名（生产图谱 739 实体里 14 个有；旧面板完全展示不出来）*/}
            {aliases.length > 0 && (
              <div className="mb-2">
                <span className="text-2xs text-text-muted uppercase tracking-wider">{t('knowledgeGraph.aliasesTitle')}</span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {aliases.slice(0, 8).map(al => (
                    <span key={al} className="text-2xs px-1.5 py-0.5 rounded bg-white/[0.05] text-text-secondary">{al}</span>
                  ))}
                </div>
              </div>
            )}
            {/* 来源：detail 到位后才有内容——这是旧面板最大的空洞（89% 实体有出处、0 条可见） */}
            {detail && (
              <div className="mb-2">
                <span className="text-2xs text-text-muted uppercase tracking-wider">{t('knowledgeGraph.sourcesTitle')}</span>
                <div className="mt-1.5 space-y-1">
                  {origins.slice(0, 12).map((o, i) => {
                    // 可跳 = 后端说可跳 + 前端路由白名单里有这个模块 + **本地真存在该笔记**
                    // （三件齐才给按钮，不给点了报错的假链接）
                    const noteId = canJumpOrigin(o) ? String(o.source_id || '') : ''
                    const canJump = !!noteId && floatingNotes.some(n => n.id === noteId)
                    const label = o.title || o.source_id || o.raw
                    if (!canJump) {
                      return (
                        <div key={`${o.kind}-${o.source_id}-${i}`} className="flex items-start gap-1.5 text-2xs">
                          <span className="text-text-dim flex-shrink-0">{o.label}</span>
                          <span className="text-text-secondary break-all" title={o.raw}>{label}</span>
                        </div>
                      )
                    }
                    return (
                      <button
                        key={`${o.kind}-${o.source_id}-${i}`}
                        onClick={() => { setActiveModule('notes'); setEditingNoteId(o.source_id) }}
                        className="w-full flex items-start gap-1.5 text-2xs text-left text-emerald-400/80 hover:bg-emerald-400/10 rounded px-1 py-0.5 transition-colors"
                      >
                        <span className="text-text-dim flex-shrink-0">{o.label}</span>
                        <span className="break-all flex-1">{label}</span>
                        <Icon icon="fa-solid fa-arrow-up-right-from-square text-[8px] mt-0.5 flex-shrink-0" />
                      </button>
                    )
                  })}
                  {origins.length === 0 && (
                    <span className="text-2xs text-text-dim">{t('knowledgeGraph.noSources')}</span>
                  )}
                </div>
              </div>
            )}
            {/* 卡片/MOC 正文（实体本身不存正文，detail 回查后才看得到）*/}
            {body && (body.content || body.detail || body.expansion || body.analysis) && (
              <div className="mb-2">
                <span className="text-2xs text-text-muted uppercase tracking-wider">{t('knowledgeGraph.bodyTitle')}</span>
                <div className="mt-1 space-y-1.5">
                  {body.content && <p className="text-2xs text-text-secondary leading-relaxed break-words">{body.content}</p>}
                  {body.detail && <p className="text-2xs text-text-dim leading-relaxed break-words">{body.detail}</p>}
                  {body.expansion && <p className="text-2xs text-amber-400/70 leading-relaxed break-words">{body.expansion}</p>}
                  {body.analysis && <p className="text-2xs text-emerald-400/70 leading-relaxed break-words">{body.analysis}</p>}
                </div>
              </div>
            )}
            {detailLoading && (
              <div className="text-2xs text-text-dim mb-2 flex items-center gap-1.5">
                <span className="w-2.5 h-2.5 border border-fluent-blue/40 border-t-fluent-blue rounded-full animate-spin inline-block" />
                {t('knowledgeGraph.loadingDetail')}
              </div>
            )}
            <span className="text-2xs text-text-muted uppercase tracking-wider">{t('knowledgeGraph.connectionsFmt', { count: relRows.length })}</span>
            <div className="mt-1.5 space-y-1">
              {relRows.slice(0, 20).map(row => {
                const reachable = !!row.targetId && nodeIds.has(row.targetId)
                return (
                  <div
                    key={row.key}
                    onClick={() => jumpToNeighbor(row)}
                    className={`flex items-center gap-1.5 text-xs rounded px-1 py-0.5 transition-colors ${reachable ? 'text-text-secondary hover:bg-white/5 cursor-pointer' : 'text-text-dim'}`}
                  >
                    <span className="text-text-dim text-[9px] flex-shrink-0 w-3 text-center">{row.dir === 'in' ? '←' : row.dir === 'out' ? '→' : '·'}</span>
                    <span className="truncate flex-1">{row.name}</span>
                    <span className="text-text-dim text-[8px] flex-shrink-0">{row.pred}</span>
                  </div>
                )
              })}
              {relRows.length === 0 && (
                <span className="text-2xs text-text-dim">{t('knowledgeGraph.noConnections')}</span>
              )}
            </div>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="kgraph-night flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Page header（P3b：大标题 + 统计副标题 + 操作区） */}
      <div className="flex-shrink-0 px-5 pt-5 pb-3">
        <PageHeader
          title={t('nav.knowledgeGraph')}
          subtitle={data ? `${t('knowledgeGraph.statsNodesFmt', { count: data.nodes.length })} · ${t('knowledgeGraph.statsEdgesFmt', { count: data.edges.length })}` : undefined}
          actions={(
            <>
              <button
                onClick={() => setShowAddNode(true)}
                className="text-xs px-2.5 py-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-colors"
              >
                <Icon icon="fa-solid fa-plus mr-1" />
                {t('knowledgeGraph.addNode')}
              </button>
              <button
                onClick={() => setShowAddEdge(true)}
                className="text-xs px-2.5 py-1.5 rounded-lg text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-colors"
              >
                <Icon icon="fa-solid fa-link mr-1" />
                {t('knowledgeGraph.addEdge')}
              </button>
              <button
                onClick={async () => {
                  if (!data || searchingBackend) return
                  setSearchingBackend(true)
                  const merged = await searchBackendKnowledge(data, searchQuery, filterType || undefined)
                  setData(merged)
                  setSearchingBackend(false)
                }}
                disabled={searchingBackend}
                className="w-8 h-8 flex items-center justify-center rounded-lg text-sm text-text-muted hover:text-text-secondary hover:bg-hover-bg transition-colors disabled:opacity-30"
                title={t('knowledgeGraph.searchBackendTitle')}
              >
                <Icon icon={`fa-solid fa-cloud-arrow-down ${searchingBackend ? 'animate-pulse' : ''}`} />
              </button>
              <button
                onClick={() => loadData(true)}
                className="w-8 h-8 flex items-center justify-center rounded-lg text-sm text-text-muted hover:text-text-secondary hover:bg-hover-bg transition-colors"
                title={t('knowledgeGraph.reloadTitle')}
              >
                <Icon icon="fa-solid fa-rotate" />
              </button>
              {ei && (
                <button
                  onClick={() => ei.createViewWindow({ viewType: 'knowledge-graph' })}
                  className="w-8 h-8 flex items-center justify-center rounded-lg text-sm text-text-muted hover:text-text-secondary hover:bg-hover-bg transition-colors"
                  title={t('notesModule.independentWindow')}
                >
                  <Icon icon="fa-solid fa-external-link-alt" />
                </button>
              )}
            </>
          )}
        />
      </div>

      {/* 删除回执条（模块级：详情面板卸载后依然可见） */}
      {actionNotice && (
        <div className="flex-shrink-0 mx-5 mb-1 flex items-start gap-2 px-3 py-2 rounded-lg text-xs border" style={{ background: 'rgba(96,165,250,0.08)', borderColor: 'rgba(96,165,250,0.25)', color: 'var(--fluent-blue, #60a5fa)' }} role="status">
          <Icon icon="fa-solid fa-circle-info mt-0.5 flex-shrink-0" />
          <span className="flex-1 break-words">{actionNotice}</span>
          <button onClick={() => setActionNotice(null)} className="text-text-muted hover:text-text-secondary flex-shrink-0" aria-label={t('knowledgeGraph.cancel')}>
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>
      )}

      {/* Toolbar */}
      <div className="flex items-center gap-2 px-5 py-2.5 border-b" style={{ borderColor: 'var(--border)' }}>
        <div className="flex items-center gap-1.5 flex-1">
          <div className="flex items-center gap-1.5 bg-white/[0.04] rounded-lg px-2.5 py-1.5 border border-white/[0.04] focus-within:border-white/[0.1] transition-colors w-56">
            <Icon icon="fa-solid fa-magnifying-glass text-xs text-text-muted" />
            <input
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder={t('knowledgeGraph.searchPlaceholder')}
              className="flex-1 bg-transparent text-xs text-white/85 placeholder:text-text-muted outline-none"
            />
            {searchQuery && (
              <button onClick={() => setSearchQuery('')} className="text-xs text-text-muted hover:text-text-secondary">
                <Icon icon="fa-solid fa-xmark" />
              </button>
            )}
          </div>

          {/* Type filter chips */}
          <div className="flex items-center gap-1.5 flex-wrap">
            <button onClick={() => setFilterType(null)}
              className="text-xs px-2 py-1 rounded-full transition-colors"
              style={{ background: filterType ? 'transparent' : 'var(--chip-selected-bg, rgba(96,165,250,0.15))',
                       color: filterType ? 'var(--text-tertiary)' : 'var(--fluent-blue, #60a5fa)',
                       border: filterType ? '1px solid rgba(255,255,255,0.06)' : '1px solid rgba(96,165,250,0.3)' }}>
            {t('knowledgeGraph.all')}</button>
            {nodeTypes.map((ty: string) => (
              <button key={ty} onClick={() => setFilterType(ty === filterType ? null : ty)}
                className="text-xs px-2 py-1 rounded-full transition-colors flex items-center gap-1"
                style={{ background: filterType === ty ? 'var(--chip-selected-bg, rgba(96,165,250,0.15))' : 'transparent',
                         color: filterType === ty ? 'var(--fluent-blue, #60a5fa)' : 'var(--text-tertiary)',
                         border: filterType === ty ? '1px solid rgba(96,165,250,0.3)' : '1px solid rgba(255,255,255,0.06)' }}>
                <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: nodeColor(ty) }} />
                {localizeType(ty)}
              </button>))}
          </div>

          {/* Topic filter select */}
          <select
            value={filterTopic || ''}
            onChange={e => setFilterTopic(e.target.value || null)}
            className="text-xs px-2 py-1 rounded-lg bg-white/[0.04] border border-white/[0.04] text-white/55 outline-none cursor-pointer max-w-40"
          >
            <option value="">{t('knowledgeGraph.allTopics')}</option>
            {topicOptions.map(tp => (
              <option key={tp} value={tp}>{tp}</option>
            ))}
          </select>
        </div>

        {/* 视图切换：关系图谱 / 思维导图 */}
        <div className="flex items-center gap-0.5 bg-white/[0.04] rounded-lg p-0.5 border border-white/[0.04]">
          <button
            onClick={() => setViewMode('graph')}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs transition-colors ${
              viewMode === 'graph'
                ? 'bg-fluent-blue/15 text-fluent-blue'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            <Icon icon="fa-solid fa-circle-nodes" />
            {t('knowledgeGraph.viewGraph')}
          </button>
          <button
            onClick={() => setViewMode('mindmap')}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs transition-colors ${
              viewMode === 'mindmap'
                ? 'bg-fluent-blue/15 text-fluent-blue'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            <Icon icon="fa-solid fa-sitemap" />
            {t('knowledgeGraph.viewMindMap')}
          </button>
          <button
            onClick={() => setViewMode('cards')}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs transition-colors ${
              viewMode === 'cards'
                ? 'bg-fluent-blue/15 text-fluent-blue'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            <Icon icon="fa-solid fa-clone" />
            {t('knowledgeGraph.viewCards')}
          </button>
          {focusId ? (
            <button
              onClick={() => { setFocusId(null); setSelectedNode(null) }}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs transition-colors text-amber-400/90 hover:bg-white/5"
            >
              <Icon icon="fa-solid fa-expand" />
              {t('knowledgeGraph.exitLocalView')}
            </button>
          ) : (
            <button
              onClick={() => { if (selectedNode) setFocusId(selectedNode.id) }}
              disabled={!selectedNode}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs transition-colors disabled:opacity-40 text-fluent-blue hover:bg-white/5"
            >
              <Icon icon="fa-solid fa-crosshairs" />
              {t('knowledgeGraph.focusLocal')}
            </button>
          )}
          <button
            onClick={() => setAlwaysShowLabels(v => !v)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs transition-colors ${
              viewMode === 'graph' && alwaysShowLabels
                ? 'bg-fluent-blue/15 text-fluent-blue'
                : 'text-text-muted hover:text-text-secondary'
            }`}
          >
            <Icon icon={alwaysShowLabels ? 'fa-solid fa-eye' : 'fa-solid fa-eye-slash'} />
            {t('knowledgeGraph.alwaysShowLabels')}
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 flex min-h-0" ref={containerRef}>
        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
          </div>
        ) : viewMode === 'cards' ? (
          /* ── 知识卡片视图：主题 MOC → 可读卡片 ── */
          <div className="flex-1 overflow-auto px-5 py-4">
            <div className="flex items-center gap-2 mb-4">
              <button
                onClick={async () => {
                  if (synthesizing) return
                  setSynthesizing(true)
                  try {
                    await triggerKnowledgeSynthesize()
                    await loadCards()
                  } finally {
                    setSynthesizing(false)
                  }
                }}
                disabled={synthesizing}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs text-fluent-blue bg-fluent-blue/10 hover:bg-fluent-blue/20 transition-colors disabled:opacity-40"
              >
                <Icon icon={`fa-solid fa-wand-magic-sparkles ${synthesizing ? 'animate-pulse' : ''}`} />
                {synthesizing ? t('knowledgeGraph.synthesizing') : t('knowledgeGraph.synthesizeBtn')}
              </button>
              <span className="text-xs text-text-muted">
                {t('knowledgeGraph.cardsStatsFmt', { cards: knowledgeCards.length, topics: topicMOCs.length })}
              </span>
            </div>

            {cardsLoading ? (
              <div className="flex items-center justify-center py-16">
                <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
              </div>
            ) : topicMOCs.length === 0 && knowledgeCards.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 text-center">
                <Icon icon="fa-solid fa-clone text-3xl text-text-dim mb-3" />
                <p className="text-sm text-text-muted">{t('knowledgeGraph.cardsEmpty')}</p>
              </div>
            ) : (
              <div className="space-y-6 max-w-4xl">
                {topicMOCs.map(moc => (
                  <div key={moc.id} className="rounded-xl border overflow-hidden" style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg-solid)' }}>
                    <div className="px-4 py-3 border-b flex items-center justify-between" style={{ borderColor: 'var(--border)' }}>
                      <div className="flex items-center gap-2 min-w-0">
                        <Icon icon="fa-solid fa-map" className="text-fluent-blue text-sm flex-shrink-0" />
                        <h3 className="text-sm font-medium text-text-primary truncate">{moc.title}</h3>
                        <span className="text-2xs text-text-muted flex-shrink-0">{t('knowledgeGraph.cardsInTopicFmt', { count: moc.card_count || 0 })}</span>
                      </div>
                      {(moc.related_topics || []).length > 0 && (
                        <div className="flex items-center gap-1 flex-shrink-0">
                          <span className="text-2xs text-text-muted">{t('knowledgeGraph.relatedTopics')}</span>
                          {(moc.related_topics || []).slice(0, 3).map((rt, i) => (
                            <span key={i} className="text-2xs px-1.5 py-0.5 rounded bg-white/[0.04] text-text-secondary">{rt}</span>
                          ))}
                        </div>
                      )}
                    </div>
                    <div className="px-4 py-3">
                      <p className="text-xs text-text-secondary leading-relaxed mb-3">{moc.content || moc.card_topic}</p>
                      <div className="space-y-2">
                        {(moc.card_ids || [])
                          .map(cid => knowledgeCards.find(c => c.id === cid))
                          .filter((c): c is KnowledgeCard => Boolean(c))
                          .map(card => {
                            const expanded = expandedCardId === card.id
                            return (
                              <div key={card.id} className="rounded-lg border" style={{ borderColor: expanded ? 'rgba(96,165,250,0.35)' : 'var(--border)', background: 'rgba(255,255,255,0.02)' }}>
                                <button
                                  onClick={() => setExpandedCardId(expanded ? null : card.id)}
                                  className="w-full text-left px-3 py-2.5 flex items-start justify-between gap-2"
                                >
                                  <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 mb-1">
                                      <span className="text-xs font-medium text-text-primary truncate">{card.title}</span>
                                      <span className="text-2xs text-text-muted flex-shrink-0">{card.card_topic || ''}</span>
                                    </div>
                                    <p className="text-xs text-text-secondary leading-relaxed">{card.content}</p>
                                  </div>
                                  <Icon icon={expanded ? 'fa-solid fa-chevron-up' : 'fa-solid fa-chevron-down'} className="text-2xs text-text-muted mt-1 flex-shrink-0" />
                                </button>
                                {expanded && (
                                  <div className="px-3 pb-3 space-y-2 border-t" style={{ borderColor: 'var(--border)' }}>
                                    {card.detail && (
                                      <div>
                                        <div className="text-2xs text-fluent-blue mb-0.5">{t('knowledgeGraph.detailExplain')}</div>
                                        <p className="text-xs text-text-secondary leading-relaxed">{card.detail}</p>
                                      </div>
                                    )}
                                    {card.expansion && (
                                      <div>
                                        <div className="text-2xs text-amber-400/90 mb-0.5">{t('knowledgeGraph.detailExpand')}</div>
                                        <p className="text-xs text-text-secondary leading-relaxed">{card.expansion}</p>
                                      </div>
                                    )}
                                    {card.analysis && (
                                      <div>
                                        <div className="text-2xs text-emerald-400/90 mb-0.5">{t('knowledgeGraph.detailAnalysis')}</div>
                                        <p className="text-xs text-text-secondary leading-relaxed">{card.analysis}</p>
                                      </div>
                                    )}
                                  </div>
                                )}
                              </div>
                            )
                          })}
                        {(moc.card_ids || []).length === 0 && (
                          <span className="text-xs text-text-dim">{t('knowledgeGraph.cardsInTopicEmpty')}</span>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : (
          <>
            <div className={`flex-1 ${viewMode === 'mindmap' ? 'px-3 py-2' : 'relative'}`}>
              {viewMode === 'mindmap' ? (
                <GraphMindMap
                  nodes={displayData.nodes}
                  localizeType={localizeType}
                  nodeColor={nodeColor}
                  selectedId={selectedNode?.id}
                  onSelect={handleNodeClick}
                />
              ) : (
                <>
                  <ForceGraph
                    nodes={displayData.nodes}
                    edges={displayData.edges}
                    width={size.w - (selectedNode ? 240 : 0)}
                    height={size.h}
                    onNodeClick={handleNodeClick}
                    alwaysShowLabels={alwaysShowLabels}
                    pinnedIds={selectedNode ? [selectedNode.id] : undefined}
                  />
                  {/* Legend */}
                  <div className="absolute top-2 left-2 flex flex-col gap-1 pointer-events-none">
                    <div className="flex flex-wrap gap-1.5">
                      {nodeTypes.map((ty: string) => (
                        <span key={ty} className="flex items-center gap-1 text-[8px] text-text-muted">
                          <span className="w-1.5 h-1.5 rounded-full" style={{ background: nodeColor(ty) }} />
                          {localizeType(ty)}
                        </span>
                      ))}
                    </div>
                    {/* 主题图例：显示前 8 个业务主题 */}
                    {topicOptions.length > 0 && (
                      <div className="flex flex-wrap gap-1.5">
                        {topicOptions.slice(0, 8).map(tp => (
                          <span key={tp} className="flex items-center gap-1 text-[8px] text-text-muted bg-white/[0.04] rounded px-1 py-0.5">
                            <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--fluent-blue, #60a5fa)' }} />
                            {tp}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
            {selectedNode && renderDetail()}
          </>
        )}
      </div>

      {/* Add Node Modal */}
      {showAddNode && (
        <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/40" onClick={() => setShowAddNode(false)}>
          <div className="rounded-xl p-4 w-80 border shadow-2xl" style={{ background: 'var(--panel-bg-solid)', borderColor: 'var(--panel-border)' }} onClick={e => e.stopPropagation()}>
            <h3 className="text-xs font-medium text-text-secondary mb-3">{t('knowledgeGraph.addNodeModalTitle')}</h3>
            <input
              value={newNodeLabel}
              onChange={e => setNewNodeLabel(e.target.value)}
              placeholder={t('knowledgeGraph.nodeNamePlaceholder')}
              className="w-full px-2.5 py-1.5 rounded-lg text-xs bg-white/5 border border-white/10 text-text-secondary outline-none focus:border-fluent-blue/40 mb-2"
              autoFocus
              onKeyDown={e => { if (e.key === 'Enter') handleAddNode() }}
            />
            <select
              value={newNodeType}
              onChange={e => setNewNodeType(e.target.value)}
              className="w-full bg-white/[0.04] border border-white/[0.04] rounded-lg text-xs px-2.5 py-1.5 text-white/55 outline-none mb-3 cursor-pointer"
            >
              {NODE_TYPES.map(ty => (
                <option key={ty} value={ty}>{localizeType(ty)}</option>
              ))}
            </select>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowAddNode(false)} className="px-3 py-1.5 rounded-lg text-xs text-text-secondary hover:text-text-secondary transition-colors">{t('knowledgeGraph.cancel')}</button>
              <button onClick={handleAddNode} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 transition-colors">{t('knowledgeGraph.addBtn')}</button>
            </div>
          </div>
        </div>
      )}

      {/* Add Edge Modal */}
      {showAddEdge && (
        <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/40" onClick={() => setShowAddEdge(false)}>
          <div className="rounded-xl p-4 w-80 border shadow-2xl" style={{ background: 'var(--panel-bg-solid)', borderColor: 'var(--panel-border)' }} onClick={e => e.stopPropagation()}>
            <h3 className="text-xs font-medium text-text-secondary mb-3">{t('knowledgeGraph.addEdgeModalTitle')}</h3>
            <select
              value={newEdgeSource}
              onChange={e => setNewEdgeSource(e.target.value)}
              className="w-full bg-white/[0.04] border border-white/[0.04] rounded-lg text-xs px-2.5 py-1.5 text-white/55 outline-none mb-2 cursor-pointer"
            >
              <option value="">{t('knowledgeGraph.selectSourceNode')}</option>
              {data?.nodes.map(n => (
                <option key={n.id} value={n.id}>{n.label}</option>
              ))}
            </select>
            <select
              value={newEdgeTarget}
              onChange={e => setNewEdgeTarget(e.target.value)}
              className="w-full bg-white/[0.04] border border-white/[0.04] rounded-lg text-xs px-2.5 py-1.5 text-white/55 outline-none mb-2 cursor-pointer"
            >
              <option value="">{t('knowledgeGraph.selectTargetNode')}</option>
              {data?.nodes.map(n => (
                <option key={n.id} value={n.id}>{n.label}</option>
              ))}
            </select>
            <input
              value={newEdgeType}
              onChange={e => setNewEdgeType(e.target.value)}
              placeholder={t('knowledgeGraph.edgeTypePlaceholder')}
              className="w-full px-2.5 py-1.5 rounded-lg text-xs bg-white/5 border border-white/10 text-text-secondary outline-none focus:border-fluent-blue/40 mb-3"
            />
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowAddEdge(false)} className="px-3 py-1.5 rounded-lg text-xs text-text-secondary hover:text-text-secondary transition-colors">{t('knowledgeGraph.cancel')}</button>
              <button onClick={handleAddEdge} className="px-3 py-1.5 rounded-lg text-xs font-medium bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 transition-colors">{t('knowledgeGraph.addBtn')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default KnowledgeGraphModule
