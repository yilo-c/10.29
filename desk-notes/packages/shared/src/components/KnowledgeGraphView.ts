/**
 * 知识图谱可视化组件
 * 
 * 功能：
 * - SVG 渲染力导向图
 * - 节点拖拽交互
 * - 缩放和平移
 * - 节点搜索高亮
 * - 点击节点查看详情
 * 
 * 使用示例：
 * ```tsx
 * <KnowledgeGraphView
 *   notes={notes}
 *   onNodeClick={(node) => console.log(node)}
 *   width={800}
 *   height={600}
 * />
 * ```
 */

import { 
  buildKnowledgeGraph, 
  computeForceLayout, 
  filterGraph,
  type GraphData, 
  type GraphNode, 
  type GraphEdge,
  type NoteNode 
} from '../graph/knowledgeGraph'

// 节点颜色配置
const NODE_COLORS: Record<string, string> = {
  note: '#3b82f6',    // 蓝色
  tag: '#10b981',     // 绿色
  task: '#f59e0b',    // 橙色
  category: '#8b5cf6', // 紫色
  entity: '#ec4899',  // 粉色
}

// 边颜色配置
const EDGE_COLORS: Record<string, string> = {
  has_tag: '#10b981',
  related_to: '#3b82f6',
  part_of: '#8b5cf6',
  depends_on: '#f59e0b',
  mentions: '#ec4899',
  similar_to: '#6b7280',
}

// 节点大小配置
const NODE_SIZES: Record<string, number> = {
  note: 20,
  tag: 15,
  task: 18,
  category: 25,
  entity: 12,
}

/**
 * 知识图谱视图配置
 */
export interface KnowledgeGraphViewConfig {
  width: number
  height: number
  showLabels?: boolean
  showEdgeLabels?: boolean
  minNodeSize?: number
  maxNodeSize?: number
  linkDistance?: number
  chargeStrength?: number
}

/**
 * 知识图谱视图状态
 */
export interface KnowledgeGraphViewState {
  transform: { x: number; y: number; k: number }
  selectedNodeId: string | null
  hoveredNodeId: string | null
  searchQuery: string
  highlightedNodeIds: Set<string>
  draggedNodeId: string | null
}

/**
 * 创建默认配置
 */
export function createDefaultConfig(): KnowledgeGraphViewConfig {
  return {
    width: 800,
    height: 600,
    showLabels: true,
    showEdgeLabels: false,
    minNodeSize: 8,
    maxNodeSize: 40,
    linkDistance: 100,
    chargeStrength: -200,
  }
}

/**
 * 创建默认状态
 */
export function createDefaultState(): KnowledgeGraphViewState {
  return {
    transform: { x: 0, y: 0, k: 1 },
    selectedNodeId: null,
    hoveredNodeId: null,
    searchQuery: '',
    highlightedNodeIds: new Set(),
    draggedNodeId: null,
  }
}

/**
 * 构建并布局图谱
 */
export function buildAndLayoutGraph(
  notes: NoteNode[],
  config: KnowledgeGraphViewConfig
): GraphData {
  // 构建图谱
  let graph = buildKnowledgeGraph(notes)
  
  // 过滤掉中心性过低的节点（可选）
  if (graph.nodes.length > 50) {
    graph = filterGraph(graph, { minCentrality: 0.1 })
  }
  
  // 应用力导向布局
  graph = computeForceLayout(graph, {
    width: config.width,
    height: config.height,
    iterations: 100,
    nodeDistance: config.linkDistance,
  })
  
  return graph
}

/**
 * 获取节点颜色
 */
export function getNodeColor(nodeType: string): string {
  return NODE_COLORS[nodeType] || '#6b7280'
}

/**
 * 获取边颜色
 */
export function getEdgeColor(edgeType: string): string {
  return EDGE_COLORS[edgeType] || '#6b7280'
}

/**
 * 获取节点大小
 */
export function getNodeSize(node: GraphNode, config: KnowledgeGraphViewConfig): number {
  const baseSize = NODE_SIZES[node.type] || 15
  const centrality = node.centrality || 0
  
  // 根据中心性调整大小
  const size = baseSize + centrality * 20
  
  return Math.max(config.minNodeSize || 8, Math.min(config.maxNodeSize || 40, size))
}

/**
 * 搜索节点
 */
export function searchNodes(graph: GraphData, query: string): Set<string> {
  if (!query.trim()) {
    return new Set()
  }
  
  const normalizedQuery = query.toLowerCase().trim()
  const highlightedIds = new Set<string>()
  
  for (const node of graph.nodes) {
    if (node.label.toLowerCase().includes(normalizedQuery)) {
      highlightedIds.add(node.id)
    }
  }
  
  return highlightedIds
}

/**
 * 获取节点的邻居
 */
export function getNodeNeighbors(graph: GraphData, nodeId: string): {
  nodes: GraphNode[]
  edges: GraphEdge[]
} {
  const neighborEdges = graph.edges.filter(
    e => e.source === nodeId || e.target === nodeId
  )
  
  const neighborIds = new Set<string>()
  neighborIds.add(nodeId)
  
  for (const edge of neighborEdges) {
    neighborIds.add(edge.source)
    neighborIds.add(edge.target)
  }
  
  const nodes = graph.nodes.filter(n => neighborIds.has(n.id))
  
  return { nodes, edges: neighborEdges }
}

/**
 * 生成 SVG 路径（边）
 */
export function generateEdgePath(
  source: GraphNode,
  target: GraphNode
): string {
  const sx = source.x || 0
  const sy = source.y || 0
  const tx = target.x || 0
  const ty = target.y || 0
  
  return `M ${sx} ${sy} L ${tx} ${ty}`
}

/**
 * 计算边的中点（用于标签）
 */
export function getEdgeMidpoint(
  source: GraphNode,
  target: GraphNode
): { x: number; y: number } {
  return {
    x: ((source.x || 0) + (target.x || 0)) / 2,
    y: ((source.y || 0) + (target.y || 0)) / 2,
  }
}

/**
 * 导出为 SVG 字符串
 */
export function exportToSVG(
  graph: GraphData,
  config: KnowledgeGraphViewConfig
): string {
  const { width, height } = config
  
  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`
  
  // 添加样式
  svg += `
    <style>
      .node { cursor: pointer; }
      .node:hover { opacity: 0.8; }
      .edge { fill: none; stroke-width: 1.5; }
      .label { font-family: sans-serif; font-size: 12px; text-anchor: middle; }
    </style>
  `
  
  // 绘制边
  svg += '<g class="edges">'
  for (const edge of graph.edges) {
    const source = graph.nodes.find(n => n.id === edge.source)
    const target = graph.nodes.find(n => n.id === edge.target)
    
    if (source && target) {
      const color = getEdgeColor(edge.type)
      const path = generateEdgePath(source, target)
      svg += `<path class="edge" d="${path}" stroke="${color}" />`
    }
  }
  svg += '</g>'
  
  // 绘制节点
  svg += '<g class="nodes">'
  for (const node of graph.nodes) {
    const x = node.x || 0
    const y = node.y || 0
    const size = getNodeSize(node, config)
    const color = getNodeColor(node.type)
    
    svg += `<circle class="node" cx="${x}" cy="${y}" r="${size}" fill="${color}" />`
    
    if (config.showLabels) {
      svg += `<text class="label" x="${x}" y="${y + size + 15}">${node.label}</text>`
    }
  }
  svg += '</g>'
  
  svg += '</svg>'
  
  return svg
}

/**
 * 图谱统计信息
 */
export interface GraphStats {
  nodeCount: number
  edgeCount: number
  density: number
  avgDegree: number
  nodeTypeCounts: Record<string, number>
  edgeTypeCounts: Record<string, number>
  topNodes: GraphNode[]
}

/**
 * 计算图谱统计信息
 */
export function computeGraphStats(graph: GraphData): GraphStats {
  const nodeTypeCounts: Record<string, number> = {}
  const edgeTypeCounts: Record<string, number> = {}
  
  for (const node of graph.nodes) {
    nodeTypeCounts[node.type] = (nodeTypeCounts[node.type] || 0) + 1
  }
  
  for (const edge of graph.edges) {
    edgeTypeCounts[edge.type] = (edgeTypeCounts[edge.type] || 0) + 1
  }
  
  // 获取中心性最高的节点
  const topNodes = [...graph.nodes]
    .sort((a, b) => (b.centrality || 0) - (a.centrality || 0))
    .slice(0, 10)
  
  return {
    nodeCount: graph.metadata.nodeCount,
    edgeCount: graph.metadata.edgeCount,
    density: graph.metadata.density,
    avgDegree: graph.metadata.avgDegree,
    nodeTypeCounts,
    edgeTypeCounts,
    topNodes,
  }
}

/**
 * 格式化统计信息为可读字符串
 */
export function formatStats(stats: GraphStats): string {
  const lines = [
    `节点数: ${stats.nodeCount}`,
    `边数: ${stats.edgeCount}`,
    `图密度: ${stats.density.toFixed(3)}`,
    `平均度: ${stats.avgDegree.toFixed(2)}`,
    '',
    '节点类型:',
  ]
  
  for (const [type, count] of Object.entries(stats.nodeTypeCounts)) {
    lines.push(`  ${type}: ${count}`)
  }
  
  lines.push('')
  lines.push('边类型:')
  
  for (const [type, count] of Object.entries(stats.edgeTypeCounts)) {
    lines.push(`  ${type}: ${count}`)
  }
  
  lines.push('')
  lines.push('重要节点:')
  
  for (const node of stats.topNodes.slice(0, 5)) {
    lines.push(`  ${node.label} (${node.type}, 中心性: ${(node.centrality || 0).toFixed(2)})`)
  }
  
  return lines.join('\n')
}

// React 组件渲染辅助函数（需要配合 React 使用）

/**
 * 渲染节点属性
 */
export function getNodeAttributes(
  node: GraphNode,
  config: KnowledgeGraphViewConfig,
  state: KnowledgeGraphViewState
): {
  cx: number
  cy: number
  r: number
  fill: string
  opacity: number
  stroke?: string
  strokeWidth?: number
} {
  const size = getNodeSize(node, config)
  const color = getNodeColor(node.type)
  
  let opacity = 1
  let stroke: string | undefined
  let strokeWidth: number | undefined
  
  // 搜索高亮
  if (state.highlightedNodeIds.size > 0) {
    if (state.highlightedNodeIds.has(node.id)) {
      stroke = '#fbbf24'
      strokeWidth = 3
    } else {
      opacity = 0.3
    }
  }
  
  // 选中状态
  if (state.selectedNodeId === node.id) {
    stroke = '#3b82f6'
    strokeWidth = 4
  }
  
  // 悬停状态
  if (state.hoveredNodeId === node.id) {
    opacity = 0.8
  }
  
  return {
    cx: node.x || 0,
    cy: node.y || 0,
    r: size,
    fill: color,
    opacity,
    stroke,
    strokeWidth,
  }
}

/**
 * 渲染边属性
 */
export function getEdgeAttributes(
  edge: GraphEdge,
  graph: GraphData,
  state: KnowledgeGraphViewState
): {
  d: string
  stroke: string
  strokeWidth: number
  opacity: number
} {
  const source = graph.nodes.find(n => n.id === edge.source)
  const target = graph.nodes.find(n => n.id === edge.target)
  
  const color = getEdgeColor(edge.type)
  let opacity = 0.6
  let strokeWidth = 1.5
  
  // 如果选中了节点，高亮相关边
  if (state.selectedNodeId) {
    if (edge.source === state.selectedNodeId || edge.target === state.selectedNodeId) {
      opacity = 1
      strokeWidth = 2.5
    } else {
      opacity = 0.2
    }
  }
  
  return {
    d: source && target ? generateEdgePath(source, target) : '',
    stroke: color,
    strokeWidth,
    opacity,
  }
}