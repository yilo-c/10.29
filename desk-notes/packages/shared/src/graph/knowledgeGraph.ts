/**
 * 知识图谱构建器
 * 
 * 功能：
 * - 从笔记/标签/任务构建关系图
 * - 支持多种关系类型
 * - 图数据分析（中心性、聚类等）
 * - 为 D3.js/Cytoscape.js 提供数据
 */

// 图节点类型
export type GraphNodeType = 'note' | 'tag' | 'task' | 'category' | 'entity'

// 图边类型
export type GraphEdgeType = 
  | 'has_tag'       // 笔记有标签
  | 'related_to'    // 笔记相关
  | 'part_of'       // 属于分类
  | 'depends_on'    // 任务依赖
  | 'mentions'      // 提及其他实体
  | 'similar_to'    // 相似内容

// 图节点
export interface GraphNode {
  id: string
  type: GraphNodeType
  label: string
  properties: Record<string, unknown>
  // 计算属性
  centrality?: number      // 中心性得分
  cluster?: number         // 聚类 ID
  x?: number              // 布局 X
  y?: number              // 布局 Y
}

// 图边
export interface GraphEdge {
  id: string
  source: string
  target: string
  type: GraphEdgeType
  weight: number           // 边权重
  properties: Record<string, unknown>
}

// 图数据
export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  metadata: {
    nodeCount: number
    edgeCount: number
    density: number        // 图密度
    avgDegree: number      // 平均度
    createdAt: number
  }
}

// 输入数据类型
export interface NoteNode {
  id: string
  title: string
  content: string
  tags: string[]
  folder?: string
  relatedIds?: string[]
  createdAt: number
  updatedAt: number
}

export interface TaskNode {
  id: string
  title: string
  category?: string
  dependsOn?: string[]
  completed: boolean
}

/**
 * 知识图谱构建器
 */
export class KnowledgeGraphBuilder {
  private nodes: Map<string, GraphNode> = new Map()
  private edges: Map<string, GraphEdge> = new Map()
  private edgeIdCounter = 0

  /**
   * 清空图
   */
  clear(): void {
    this.nodes.clear()
    this.edges.clear()
    this.edgeIdCounter = 0
  }

  /**
   * 添加节点
   */
  addNode(node: GraphNode): void {
    this.nodes.set(node.id, node)
  }

  /**
   * 添加边
   */
  addEdge(edge: Omit<GraphEdge, 'id'>): void {
    const id = `edge_${this.edgeIdCounter++}`
    this.edges.set(id, { ...edge, id })
  }

  /**
   * 从笔记构建图谱
   */
  buildFromNotes(notes: NoteNode[]): GraphData {
    this.clear()

    // 添加笔记节点
    for (const note of notes) {
      this.addNode({
        id: `note:${note.id}`,
        type: 'note',
        label: note.title,
        properties: {
          contentLength: note.content.length,
          tagCount: note.tags.length,
          createdAt: note.createdAt,
          updatedAt: note.updatedAt,
        },
      })

      // 添加标签节点和边
      for (const tag of note.tags) {
        const tagId = `tag:${tag}`
        
        if (!this.nodes.has(tagId)) {
          this.addNode({
            id: tagId,
            type: 'tag',
            label: tag,
            properties: { noteCount: 0 },
          })
        }

        // 更新标签的笔记计数
        const tagNode = this.nodes.get(tagId)!
        ;(tagNode.properties.noteCount as number)++

        this.addEdge({
          source: `note:${note.id}`,
          target: tagId,
          type: 'has_tag',
          weight: 1,
          properties: {},
        })
      }

      // 添加分类节点和边
      if (note.folder) {
        const folderId = `category:${note.folder}`
        
        if (!this.nodes.has(folderId)) {
          this.addNode({
            id: folderId,
            type: 'category',
            label: note.folder,
            properties: { noteCount: 0 },
          })
        }

        const folderNode = this.nodes.get(folderId)!
        ;(folderNode.properties.noteCount as number)++

        this.addEdge({
          source: `note:${note.id}`,
          target: folderId,
          type: 'part_of',
          weight: 1,
          properties: {},
        })
      }

      // 添加相关笔记边
      for (const relatedId of note.relatedIds || []) {
        this.addEdge({
          source: `note:${note.id}`,
          target: `note:${relatedId}`,
          type: 'related_to',
          weight: 0.8,
          properties: {},
        })
      }
    }

    // 基于内容相似度添加边
    this.addSimilarityEdges(notes)

    return this.build()
  }

  /**
   * 从任务构建图谱
   */
  buildFromTasks(tasks: TaskNode[]): GraphData {
    this.clear()

    for (const task of tasks) {
      this.addNode({
        id: `task:${task.id}`,
        type: 'task',
        label: task.title,
        properties: {
          completed: task.completed,
          category: task.category,
        },
      })

      // 添加分类
      if (task.category) {
        const categoryId = `category:${task.category}`
        if (!this.nodes.has(categoryId)) {
          this.addNode({
            id: categoryId,
            type: 'category',
            label: task.category,
            properties: {},
          })
        }

        this.addEdge({
          source: `task:${task.id}`,
          target: categoryId,
          type: 'part_of',
          weight: 1,
          properties: {},
        })
      }

      // 添加依赖关系
      for (const depId of task.dependsOn || []) {
        this.addEdge({
          source: `task:${task.id}`,
          target: `task:${depId}`,
          type: 'depends_on',
          weight: 1,
          properties: {},
        })
      }
    }

    return this.build()
  }

  /**
   * 合并多个图谱
   */
  mergeGraphs(...graphs: GraphData[]): GraphData {
    this.clear()

    for (const graph of graphs) {
      for (const node of graph.nodes) {
        this.addNode(node)
      }
      for (const edge of graph.edges) {
        this.addEdge({
          source: edge.source,
          target: edge.target,
          type: edge.type,
          weight: edge.weight,
          properties: edge.properties,
        })
      }
    }

    return this.build()
  }

  /**
   * 基于内容相似度添加边
   */
  private addSimilarityEdges(notes: NoteNode[], threshold: number = 0.3): void {
    // 简单的 Jaccard 相似度
    const computeSimilarity = (a: NoteNode, b: NoteNode): number => {
      const wordsA = new Set(a.content.toLowerCase().split(/\s+/))
      const wordsB = new Set(b.content.toLowerCase().split(/\s+/))
      
      const intersection = new Set([...wordsA].filter(x => wordsB.has(x)))
      const union = new Set([...wordsA, ...wordsB])
      
      return intersection.size / union.size
    }

    for (let i = 0; i < notes.length; i++) {
      for (let j = i + 1; j < notes.length; j++) {
        const similarity = computeSimilarity(notes[i], notes[j])
        
        if (similarity >= threshold) {
          // 检查是否已存在边
          const exists = Array.from(this.edges.values()).some(
            e => (e.source === `note:${notes[i].id}` && e.target === `note:${notes[j].id}`) ||
                 (e.source === `note:${notes[j].id}` && e.target === `note:${notes[i].id}`)
          )
          
          if (!exists) {
            this.addEdge({
              source: `note:${notes[i].id}`,
              target: `note:${notes[j].id}`,
              type: 'similar_to',
              weight: similarity,
              properties: { similarity },
            })
          }
        }
      }
    }
  }

  /**
   * 计算节点中心性（度中心性）
   */
  computeCentrality(): Map<string, number> {
    const centrality = new Map<string, number>()
    
    // 计算每个节点的度
    for (const node of this.nodes.keys()) {
      centrality.set(node, 0)
    }
    
    for (const edge of this.edges.values()) {
      centrality.set(edge.source, (centrality.get(edge.source) || 0) + 1)
      centrality.set(edge.target, (centrality.get(edge.target) || 0) + 1)
    }
    
    // 归一化
    const maxDegree = Math.max(...centrality.values(), 1)
    for (const [node, value] of centrality) {
      centrality.set(node, value / maxDegree)
    }
    
    return centrality
  }

  /**
   * 简单聚类（基于连通分量）
   */
  computeClusters(): Map<string, number> {
    const clusters = new Map<string, number>()
    const visited = new Set<string>()
    let clusterId = 0

    const bfs = (startNode: string): void => {
      const queue = [startNode]
      visited.add(startNode)
      clusters.set(startNode, clusterId)

      while (queue.length > 0) {
        const node = queue.shift()!
        
        // 找到所有相邻节点
        for (const edge of this.edges.values()) {
          let neighbor: string | null = null
          
          if (edge.source === node) {
            neighbor = edge.target
          } else if (edge.target === node) {
            neighbor = edge.source
          }
          
          if (neighbor && !visited.has(neighbor)) {
            visited.add(neighbor)
            clusters.set(neighbor, clusterId)
            queue.push(neighbor)
          }
        }
      }
    }

    for (const node of this.nodes.keys()) {
      if (!visited.has(node)) {
        bfs(node)
        clusterId++
      }
    }

    return clusters
  }

  /**
   * 构建最终图数据
   */
  build(): GraphData {
    const nodes = Array.from(this.nodes.values())
    const edges = Array.from(this.edges.values())
    
    // 计算中心性
    const centrality = this.computeCentrality()
    for (const node of nodes) {
      node.centrality = centrality.get(node.id) || 0
    }
    
    // 计算聚类
    const clusters = this.computeClusters()
    for (const node of nodes) {
      node.cluster = clusters.get(node.id) || 0
    }
    
    // 计算统计信息
    const nodeCount = nodes.length
    const edgeCount = edges.length
    const maxEdges = nodeCount * (nodeCount - 1) / 2
    const density = maxEdges > 0 ? edgeCount / maxEdges : 0
    const avgDegree = nodeCount > 0 ? (2 * edgeCount) / nodeCount : 0

    return {
      nodes,
      edges,
      metadata: {
        nodeCount,
        edgeCount,
        density,
        avgDegree,
        createdAt: Date.now(),
      },
    }
  }
}

/**
 * 图布局算法（力导向布局简化版）
 */
export function computeForceLayout(
  graph: GraphData,
  options: {
    width: number
    height: number
    iterations?: number
    nodeDistance?: number
  }
): GraphData {
  const { width, height, iterations = 100, nodeDistance = 100 } = options
  const nodes = graph.nodes.map(n => ({ ...n }))
  const edges = graph.edges

  // 初始化位置
  for (const node of nodes) {
    node.x = Math.random() * width
    node.y = Math.random() * height
  }

  // 力导向迭代
  for (let iter = 0; iter < iterations; iter++) {
    const forces = new Map<string, { fx: number; fy: number }>()
    
    for (const node of nodes) {
      forces.set(node.id, { fx: 0, fy: 0 })
    }

    // 斥力（节点之间）
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i]
        const b = nodes[j]
        
        const dx = (b.x || 0) - (a.x || 0)
        const dy = (b.y || 0) - (a.y || 0)
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        
        const force = (nodeDistance * nodeDistance) / dist
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        
        forces.get(a.id)!.fx -= fx
        forces.get(a.id)!.fy -= fy
        forces.get(b.id)!.fx += fx
        forces.get(b.id)!.fy += fy
      }
    }

    // 引力（边连接的节点）
    for (const edge of edges) {
      const source = nodes.find(n => n.id === edge.source)
      const target = nodes.find(n => n.id === edge.target)
      
      if (source && target) {
        const dx = (target.x || 0) - (source.x || 0)
        const dy = (target.y || 0) - (source.y || 0)
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        
        const force = dist / nodeDistance
        const fx = dx * force * edge.weight
        const fy = dy * force * edge.weight
        
        forces.get(source.id)!.fx += fx
        forces.get(source.id)!.fy += fy
        forces.get(target.id)!.fx -= fx
        forces.get(target.id)!.fy -= fy
      }
    }

    // 应用力
    const cooling = 1 - iter / iterations
    for (const node of nodes) {
      const f = forces.get(node.id)!
      node.x = (node.x || 0) + f.fx * cooling
      node.y = (node.y || 0) + f.fy * cooling
      
      // 边界约束
      node.x = Math.max(50, Math.min(width - 50, node.x))
      node.y = Math.max(50, Math.min(height - 50, node.y))
    }
  }

  return { ...graph, nodes }
}

/**
 * 获取节点的重要邻居
 */
export function getImportantNeighbors(
  graph: GraphData,
  nodeId: string,
  limit: number = 5
): GraphNode[] {
  const neighborEdges = graph.edges.filter(
    e => e.source === nodeId || e.target === nodeId
  )
  
  const neighborIds = neighborEdges.map(e => 
    e.source === nodeId ? e.target : e.source
  )
  
  const neighbors = graph.nodes
    .filter(n => neighborIds.includes(n.id))
    .sort((a, b) => (b.centrality || 0) - (a.centrality || 0))
  
  return neighbors.slice(0, limit)
}

/**
 * 过滤图谱
 */
export function filterGraph(
  graph: GraphData,
  options: {
    minCentrality?: number
    nodeTypes?: GraphNodeType[]
    edgeTypes?: GraphEdgeType[]
    clusterIds?: number[]
  }
): GraphData {
  let nodes = graph.nodes
  let edges = graph.edges

  if (options.minCentrality !== undefined) {
    nodes = nodes.filter(n => (n.centrality || 0) >= options.minCentrality!)
  }

  if (options.nodeTypes) {
    nodes = nodes.filter(n => options.nodeTypes!.includes(n.type))
  }

  if (options.clusterIds) {
    nodes = nodes.filter(n => options.clusterIds!.includes(n.cluster || 0))
  }

  const nodeIds = new Set(nodes.map(n => n.id))
  
  if (options.edgeTypes) {
    edges = edges.filter(e => options.edgeTypes!.includes(e.type))
  }

  edges = edges.filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))

  return {
    nodes,
    edges,
    metadata: {
      ...graph.metadata,
      nodeCount: nodes.length,
      edgeCount: edges.length,
    },
  }
}

// 便捷函数
export function buildKnowledgeGraph(notes: NoteNode[]): GraphData {
  const builder = new KnowledgeGraphBuilder()
  return builder.buildFromNotes(notes)
}

export function buildTaskGraph(tasks: TaskNode[]): GraphData {
  const builder = new KnowledgeGraphBuilder()
  return builder.buildFromTasks(tasks)
}