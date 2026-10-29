import React, { memo, useMemo, useState, useCallback, useRef } from 'react'
import type { GraphNode, GraphEdge } from '../../types'
import { GLOBAL_POS_CACHE } from './graphService'

/* ─── Force layout ─── */

/* SVG 渲染侧性能兜底：超过该节点数则只渲染核心子图 */
const MAX_NODES = 300

interface SimNode {
  id: string
  x: number
  y: number
  vx: number
  vy: number
  degree: number
}

/**
 * 按 topic 给节点分组，并计算每个主题的锚点（环形分布）。
 * 无 topic 的节点归入 "未分类" 组。
 */
function computeClusterAnchors(
  nodes: GraphNode[],
  w: number,
  h: number,
): { anchors: Map<string, { x: number; y: number }>; groupOf: Map<string, string> } {
  const groups = new Map<string, GraphNode[]>()
  for (const n of nodes) {
    const g = n.topic && n.topic.trim() ? n.topic : '未分类'
    if (!groups.has(g)) groups.set(g, [])
    groups.get(g)!.push(n)
  }
  const groupKeys = Array.from(groups.keys())
  const anchors = new Map<string, { x: number; y: number }>()
  const radius = Math.min(w, h) * 0.38
  const cx = w / 2
  const cy = h / 2
  const count = Math.max(1, groupKeys.length)
  groupKeys.forEach((g, i) => {
    const angle = (i / count) * Math.PI * 2
    anchors.set(g, { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius })
  })
  const groupOf = new Map<string, string>()
  for (const [g, list] of groups) for (const n of list) groupOf.set(n.id, g)
  return { anchors, groupOf }
}

function runForceLayout(nodes: GraphNode[], edges: GraphEdge[], w: number, h: number, seedPositions?: Map<string, { x: number; y: number }>): Map<string, { x: number; y: number }> {
  const { anchors, groupOf } = computeClusterAnchors(nodes, w, h)
  const sn: SimNode[] = nodes.map(n => {
    const seed = seedPositions?.get(n.id)
    const anchor = anchors.get(groupOf.get(n.id) || '未分类') || { x: w / 2, y: h / 2 }
    return {
      id: n.id,
      x: seed?.x ?? anchor.x + (Math.random() - 0.5) * 120,
      y: seed?.y ?? anchor.y + (Math.random() - 0.5) * 120,
      vx: 0, vy: 0,
      degree: 0,
    }
  })
  const idMap = new Map(sn.map(n => [n.id, n]))

  // Compute degree
  for (const e of edges) {
    const src = idMap.get(e.source)
    if (src) src.degree++
    const tgt = idMap.get(e.target)
    if (tgt) tgt.degree++
  }

  const REP = 6000
  const ATT = 0.006
  const DAMP = 0.85
  const GRAV = 0.008
  // 力迭代次数随节点数自适应缩减，避免大数据量首帧卡死主线程
  const baseIters = seedPositions ? 40 : 120
  const ITERS = Math.max(30, Math.min(baseIters, baseIters - sn.length / 10))

  for (let iter = 0; iter < ITERS; iter++) {
    const alpha = 1 - iter / ITERS

    // Repulsion
    for (let a = 0; a < sn.length; a++) {
      for (let b = a + 1; b < sn.length; b++) {
        const dx = sn[a].x - sn[b].x
        const dy = sn[a].y - sn[b].y
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        const f = REP / (dist * dist) * alpha
        sn[a].vx += (dx / dist) * f
        sn[a].vy += (dy / dist) * f
        sn[b].vx -= (dx / dist) * f
        sn[b].vy -= (dy / dist) * f
      }
    }

    // Attraction along edges
    for (const e of edges) {
      const a = idMap.get(e.source)
      const b = idMap.get(e.target)
      if (!a || !b) continue
      const dx = b.x - a.x
      const dy = b.y - a.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const f = ATT * (dist - 80) * alpha
      a.vx += (dx / dist) * f
      a.vy += (dy / dist) * f
      b.vx -= (dx / dist) * f
      b.vy -= (dy / dist) * f
    }

    // Center gravity
    for (const n of sn) {
      n.vx -= n.x * GRAV * alpha
      n.vy -= n.y * GRAV * alpha
    }

    // Apply
    for (const n of sn) {
      n.vx *= DAMP
      n.vy *= DAMP
      n.x += n.vx
      n.y += n.vy
    }
  }

  // Center
  const cx = sn.reduce((s, n) => s + n.x, 0) / sn.length
  const cy = sn.reduce((s, n) => s + n.y, 0) / sn.length
  const result = new Map<string, { x: number; y: number }>()
  for (const n of sn) {
    result.set(n.id, { x: n.x - cx + w / 2, y: n.y - cy + h / 2 })
  }
  return result
}

/* ─── Color map ─── */

const TYPE_COLORS: Record<string, string> = {
  '项目': '#4fc3f7', 'project': '#4fc3f7',
  '概念': '#81c784', 'concept': '#81c784',
  '人物': '#ff8a65', 'person': '#ff8a65',
  '地点': '#ce93d8', 'location': '#ce93d8',
  '事件': '#ffd54f', 'event': '#ffd54f',
  '组织': '#4db6ac', 'organization': '#4db6ac',
  '日期': '#90a4ae', 'date': '#90a4ae',
  '标签': '#a1887f', 'tag': '#a1887f',
  '其他': '#78909c', 'unknown': '#78909c',
}

function nodeColor(n: GraphNode): string {
  const key = (n as any).type_display || n.type || '其他'
  return TYPE_COLORS[key] || TYPE_COLORS['其他']
}

/* ─── Props ─── */

interface ForceGraphProps {
  nodes: GraphNode[]
  edges: GraphEdge[]
  width: number
  height: number
  onNodeClick?: (node: GraphNode) => void
  alwaysShowLabels?: boolean
  /**
   * 必须被渲染的节点 id（KG-CLICK，2026-09-26）。
   * 背景：超过 MAX_NODES 时按度数采样，而真实图谱 79% 是 0 度孤立点
   * ——被选中/被搜索命中的节点正好排在采样末尾，因此「根本不在画布上」，
   * 用户就点不到它。调用方把选中项传进来保证它可见。
   */
  pinnedIds?: string[]
}

/* ─── Component ─── */

function graphPropsEqual(prev: ForceGraphProps, next: ForceGraphProps) {
  // onNodeClick 一并比较：以前只比较 nodes/edges/尺寸，handler 换了 id 一样时
  // 不会重渲染，会拿着旧闭包回调（当前 handler 稳定，但不应依赖这个巧合）。
  return prev.width === next.width && prev.height === next.height
    && prev.alwaysShowLabels === next.alwaysShowLabels
    && prev.onNodeClick === next.onNodeClick
    && (prev.pinnedIds?.length ?? 0) === (next.pinnedIds?.length ?? 0)
    && (prev.pinnedIds ?? []).every((id, i) => id === next.pinnedIds?.[i])
    && prev.nodes.length === next.nodes.length
    && prev.nodes.every((n, i) => n.id === next.nodes[i]?.id)
    && prev.edges.length === next.edges.length
    && prev.edges.every((e, i) => e.source === next.edges[i]?.source && e.target === next.edges[i]?.target)
}

const ForceGraph: React.FC<ForceGraphProps> = memo(({ nodes, edges, width, height, onNodeClick, alwaysShowLabels = false, pinnedIds }) => {
  // Position cache: persist between renders to prevent flickering on data updates
  const posCacheRef = useRef<Map<string, { x: number; y: number }> | null>(null)

  // 渲染侧性能保护：节点数超过 MAX_NODES 时，按度数降序采样核心节点，
  // 只保留两端都在采样集合内的边；未超限则原样返回（保持引用稳定，不触发下游重算）
  const { sampledNodes, sampledEdges, truncatedFrom } = useMemo(() => {
    if (nodes.length <= MAX_NODES) {
      return { sampledNodes: nodes, sampledEdges: edges, truncatedFrom: 0 }
    }
    const degree = new Map<string, number>()
    for (const n of nodes) degree.set(n.id, 0)
    for (const e of edges) {
      if (degree.has(e.source)) degree.set(e.source, degree.get(e.source)! + 1)
      if (degree.has(e.target)) degree.set(e.target, degree.get(e.target)! + 1)
    }
    const picked = new Set(
      [...nodes].sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0))
        .slice(0, MAX_NODES)
        .map(n => n.id),
    )
    // 钉住节点（选中/搜索结果）必须入选：挤掉采样集末尾的度数最低者，保持总量不涨
    for (const id of pinnedIds ?? []) {
      if (picked.has(id) || !degree.has(id)) continue
      let weakest: string | null = null
      let weakestDeg = Number.POSITIVE_INFINITY
      for (const cand of picked) {
        const d = degree.get(cand) ?? 0
        if (d < weakestDeg) { weakestDeg = d; weakest = cand }
      }
      if (weakest !== null && weakestDeg === 0) picked.delete(weakest)
      picked.add(id)
    }
    return {
      sampledNodes: nodes.filter(n => picked.has(n.id)),
      sampledEdges: edges.filter(e => picked.has(e.source) && picked.has(e.target)),
      truncatedFrom: nodes.length,
    }
  }, [nodes, edges, pinnedIds])

  const positions = useMemo(() => {
    // Try posCacheRef first (component-level)
    if (posCacheRef.current) {
      const cached = posCacheRef.current
      const allCached = sampledNodes.every(n => cached.has(n.id))
      if (allCached) {
        // Sync to global cache
        for (const [k, v] of cached) GLOBAL_POS_CACHE.set(k, v)
        return cached
      }
    }

    // Try GLOBAL_POS_CACHE (survives remounts)
    if (GLOBAL_POS_CACHE.size > 0) {
      const allCached = sampledNodes.every(n => GLOBAL_POS_CACHE.has(n.id))
      if (allCached) {
        posCacheRef.current = GLOBAL_POS_CACHE
        return GLOBAL_POS_CACHE
      }
    }

    // Partial cache: seed existing positions, only compute layout for stabilization
    const seedPositions = new Map<string, { x: number; y: number }>()
    if (posCacheRef.current) {
      for (const n of sampledNodes) {
        const p = posCacheRef.current.get(n.id)
        if (p) seedPositions.set(n.id, p)
      }
    }
    if (seedPositions.size === 0 && GLOBAL_POS_CACHE.size > 0) {
      for (const n of sampledNodes) {
        const p = GLOBAL_POS_CACHE.get(n.id)
        if (p) seedPositions.set(n.id, p)
      }
    }

    const newPositions = runForceLayout(sampledNodes, sampledEdges, width, height, seedPositions.size > 0 ? seedPositions : undefined)
    posCacheRef.current = newPositions
    // Sync to global cache
    for (const [k, v] of newPositions) GLOBAL_POS_CACHE.set(k, v)
    return newPositions
  }, [sampledNodes, sampledEdges, width, height])
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [zoom, setZoom] = useState(1)
  const [isPanning, setPanning] = useState(false)
  // KG-CLICK（2026-09-26）拖拽/点击分调。旧实现按下左键就平移，与节点 click
  // 共用同一手势：在 8px 圆点上按下微抖 1px 就已经在拖画布，体感「节点点不动」。
  const dragRef = useRef({ active: false, dragging: false, sx: 0, sy: 0, px: 0, py: 0 })
  const svgRef = useRef<SVGSVGElement>(null)

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const ratio = e.deltaY > 0 ? 0.9 : 1.1
    const newZoom = Math.max(0.2, Math.min(6, zoom * ratio))
    // 以鼠标位置为锚点缩放，避免缩放后焦点漂移
    const px = e.clientX - rect.left
    const py = e.clientY - rect.top
    const wx = (px - pan.x) / zoom
    const wy = (py - pan.y) / zoom
    setPan({ x: px - wx * newZoom, y: py - wy * newZoom })
    setZoom(newZoom)
  }, [pan.x, pan.y, zoom])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 1 && e.button !== 0) return
    dragRef.current = { active: true, dragging: false, sx: e.clientX, sy: e.clientY, px: pan.x, py: pan.y }
    setPanning(true)
  }, [pan])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragRef.current.active) return
    const dx = e.clientX - dragRef.current.sx
    const dy = e.clientY - dragRef.current.sy
    // 3px 阈值：没越过阈值前当作“准备点击”，不平移——否则微抖就丢一次选中
    if (!dragRef.current.dragging && Math.hypot(dx, dy) < 3) return
    dragRef.current.dragging = true
    setPan({ x: dragRef.current.px + dx, y: dragRef.current.py + dy })
  }, [])

  const handleEnd = useCallback(() => {
    dragRef.current.active = false
    setPanning(false)
  }, [])

  // 节点点击统一入口：拖完一下再抬手不应误选/误取消节点
  const handleNodeTap = useCallback((n: GraphNode) => {
    if (dragRef.current.dragging) return
    onNodeClick?.(n)
  }, [onNodeClick])

  // Edge rendering
  const edgeElements = useMemo(() => {
    if (!positions) return null
    return sampledEdges.map((e, i) => {
      const s = positions.get(e.source)
      const t = positions.get(e.target)
      if (!s || !t) return null
      return (
        <line
          key={`e-${i}`}
          x1={s.x} y1={s.y} x2={t.x} y2={t.y}
          stroke="rgba(160,190,255,0.14)"
          strokeWidth={1}
        />
      )
    })
  }, [sampledEdges, positions])

  // Node rendering
  const nodeElements = useMemo(() => {
    if (!positions) return null
    return sampledNodes.map(n => {
      const pos = positions.get(n.id)
      if (!pos) return null
      const isHovered = hoveredId === n.id
      const kind = (n as any).kind
      const isMoc = kind === 'topic_moc'
      const isCard = kind === 'knowledge_card'
      const r = isMoc ? 10 : isCard ? 7 : 4 + (n.type === 'project' || (n as any).type_display === '项目' ? 3 : 0)
      const showLabel = alwaysShowLabels || isHovered || zoom >= 0.75
      return (
        <g
          key={n.id}
          transform={`translate(${pos.x},${pos.y})`}
          style={{ cursor: 'pointer' }}
          onMouseEnter={() => setHoveredId(n.id)}
          onMouseLeave={() => setHoveredId(null)}
          onClick={() => handleNodeTap(n)}
        >
          {/* KG-CLICK 命中区：真实可点面从 r≤7 的圆点撑到 ≥r+6（透明但有 paint，
              所以 visiblePainted 下可接事件），否则 739 节点图里要瞄 8px 才能选中 */}
          <circle r={Math.max(r + 6, 11)} fill="rgba(0,0,0,0)" stroke="none" />
          {/* Glow on hover */}
          {isHovered && (
            <circle r={r + 4} fill="none" stroke={nodeColor(n)} strokeWidth={1} opacity={0.3} />
          )}
          <circle
            r={r}
            fill={nodeColor(n)}
            opacity={isMoc || isCard ? 1 : 0.85}
            stroke={isMoc ? 'rgba(255,255,255,0.55)' : isCard ? 'rgba(255,255,255,0.35)' : 'rgba(255,255,255,0.15)'}
            strokeWidth={isMoc ? 1.5 : isCard ? 1 : 0.5}
            style={{ filter: `drop-shadow(0 0 6px ${nodeColor(n)}${isMoc ? 'cc' : 'aa'})` }}
          />
          {/* Label：KG-CLICK 后不再 pointer-events:none —— 点标签也能开详情（竞品一致） */}
          {showLabel && (
            <text
              x={0} y={-r - 6}
              textAnchor="middle"
              fill="rgba(255,255,255,0.9)"
              fontSize={isMoc ? 12 : 10}
              fontWeight={isMoc ? 600 : 400}
              style={{ textShadow: '0 1px 2px rgba(0,0,0,0.6)' }}
            >
              {n.label}
            </text>
          )}
        </g>
      )
    })
  }, [sampledNodes, positions, hoveredId, zoom, alwaysShowLabels, handleNodeTap])

  return (
    <svg
      ref={svgRef}
      width={width}
      height={height}
      className="select-none"
      style={{ cursor: isPanning ? 'grabbing' : 'grab', background: 'transparent' }}
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleEnd}
      onMouseLeave={handleEnd}
    >
      <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
        {edgeElements}
        {nodeElements}
      </g>
      {truncatedFrom > 0 && (
        <g style={{ pointerEvents: 'none' }}>
          <rect
            x={width / 2 - 150}
            y={8}
            width={300}
            height={24}
            rx={6}
            fill="rgba(15,20,32,0.72)"
            stroke="rgba(255,180,90,0.4)"
            strokeWidth={1}
          />
          <text
            x={width / 2}
            y={24}
            textAnchor="middle"
            fill="rgba(255,210,150,0.95)"
            fontSize={12}
          >
            {`图谱过大，已展示核心 ${MAX_NODES} 个节点（共 ${truncatedFrom} 个）`}
          </text>
        </g>
      )}
    </svg>
  )
}, graphPropsEqual)

export default ForceGraph
