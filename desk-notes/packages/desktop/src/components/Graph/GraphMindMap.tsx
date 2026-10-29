import React, { useMemo, useState, useCallback } from 'react'
import Icon from '../Common/Icon'
import { useTranslation } from '../../i18n'
import type { GraphNode } from '../../types'

/**
 * 知识图谱 · 思维导图视图
 *
 * 把扁平的关系图谱按「类型 → 主题 → 实体」三级归类成可折叠的树，
 * 实现类似思维导图的归类整理，避免力导向图杂乱无章的"毛线球"。
 *
 * 展开策略：
 *   - 类型分支（一级）默认展开
 *   - 主题分支（二级）默认折叠（数量多，避免刷屏）
 *   - 实体为叶子节点，点击选中查看详情
 */

interface Props {
  nodes: GraphNode[]
  localizeType: (type: string) => string
  nodeColor: (type: string) => string
  selectedId?: string | null
  onSelect?: (node: GraphNode) => void
}

const TYPE_ICONS: Record<string, string> = {
  project: 'fa-solid fa-briefcase',
  concept: 'fa-solid fa-lightbulb',
  person: 'fa-solid fa-user',
  location: 'fa-solid fa-location-dot',
  event: 'fa-solid fa-bolt',
  organization: 'fa-solid fa-building',
  date: 'fa-solid fa-calendar-days',
  tag: 'fa-solid fa-tag',
}

const GraphMindMap: React.FC<Props> = ({ nodes, localizeType, nodeColor, selectedId, onSelect }) => {
  const { t } = useTranslation()
  // 类型分支展开集合：null 表示"全部展开"（默认）；Set 表示仅这些类型展开
  const [typeExpanded, setTypeExpanded] = useState<Set<string> | null>(null)
  // 主题分支展开集合：默认全部折叠
  const [topicExpanded, setTopicExpanded] = useState<Set<string>>(() => new Set())

  // ── 构建三级树：type → topic → entities ──────────────────
  const tree = useMemo(() => {
    const types = new Map<string, Map<string, GraphNode[]>>()
    for (const n of nodes) {
      const type = n.type || 'unknown'
      const topic = (n.topic && n.topic.trim()) ? n.topic : ''
      if (!types.has(type)) types.set(type, new Map())
      const topics = types.get(type)!
      if (!topics.has(topic)) topics.set(topic, [])
      topics.get(topic)!.push(n)
    }
    // 类型排序：数量多的在前
    const sortedTypes = Array.from(types.entries()).sort((a, b) => {
      const ca = Array.from(a[1].values()).reduce((s, arr) => s + arr.length, 0)
      const cb = Array.from(b[1].values()).reduce((s, arr) => s + arr.length, 0)
      return cb - ca
    })
    return sortedTypes.map(([type, topics]) => ({
      type,
      typeCount: Array.from(topics.values()).reduce((s, arr) => s + arr.length, 0),
      topics: Array.from(topics.entries())
        .sort((a, b) => b[1].length - a[1].length)
        .map(([topic, ents]) => ({
          topic,
          ents: [...ents].sort((a, b) => a.label.localeCompare(b.label, 'zh')),
        })),
    }))
  }, [nodes])

  const typeKeyOf = (type: string) => `type:${type}`
  const topicKeyOf = (type: string, topic: string) => `type:${type}:topic:${topic}`

  const allTypeKeys = useMemo(() => tree.map(t => typeKeyOf(t.type)), [tree])

  const isTypeOpen = useCallback(
    (type: string) => {
      if (typeExpanded === null) return true
      return typeExpanded.has(typeKeyOf(type))
    },
    [typeExpanded],
  )

  const toggleType = useCallback((type: string) => {
    const key = typeKeyOf(type)
    setTypeExpanded(prev => {
      if (prev === null) {
        // 首次收起：从"全部展开"切换为"除当前外全部展开"
        const next = new Set(allTypeKeys)
        next.delete(key)
        return next
      }
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [allTypeKeys])

  const toggleTopic = useCallback((type: string, topic: string) => {
    const key = topicKeyOf(type, topic)
    setTopicExpanded(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const expandAll = useCallback(() => {
    setTypeExpanded(null)
    const all = new Set<string>()
    for (const t of tree) {
      for (const tp of t.topics) all.add(topicKeyOf(t.type, tp.topic))
    }
    setTopicExpanded(all)
  }, [tree])

  const collapseAll = useCallback(() => {
    setTypeExpanded(new Set())
    setTopicExpanded(new Set())
  }, [])

  if (nodes.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-xs text-text-muted">
        <Icon icon="fa-solid fa-tree-city mr-2" />{t('knowledgeGraph.mindMapEmpty')}
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 工具栏：全部展开 / 全部收起 */}
      <div className="flex items-center gap-2 px-1 pb-2 flex-shrink-0">
        <span className="text-2xs text-text-muted uppercase tracking-wider flex-1">
          {t('knowledgeGraph.mindMapEntitiesFmt', { count: nodes.length })}
        </span>
        <button
          onClick={expandAll}
          className="text-2xs px-2 py-0.5 rounded text-text-muted hover:text-text-secondary hover:bg-white/5 transition-colors"
        >
          <Icon icon="fa-solid fa-expand mr-1" />{t('knowledgeGraph.mindMapExpandAll')}
        </button>
        <button
          onClick={collapseAll}
          className="text-2xs px-2 py-0.5 rounded text-text-muted hover:text-text-secondary hover:bg-white/5 transition-colors"
        >
          <Icon icon="fa-solid fa-compress mr-1" />{t('knowledgeGraph.mindMapCollapseAll')}
        </button>
      </div>

      <div className="flex-1 overflow-auto pr-1 -mr-1">
        {tree.map(group => {
          const typeOpen = isTypeOpen(group.type)
          return (
            <div key={group.type} className="mb-1">
              {/* 类型分支 */}
              <button
                onClick={() => toggleType(group.type)}
                className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-white/5 transition-colors"
              >
                <Icon
                  icon={`fa-solid ${typeOpen ? 'fa-chevron-down' : 'fa-chevron-right'} text-[9px] text-text-muted`}
                />
                <span className="w-4 h-4 rounded flex items-center justify-center text-[9px] flex-shrink-0"
                  style={{ background: `${nodeColor(group.type)}22`, color: nodeColor(group.type) }}>
                  <Icon icon={TYPE_ICONS[group.type] || 'fa-solid fa-circle-dot'} />
                </span>
                <span className="text-xs font-medium text-text-primary flex-1 text-left">{localizeType(group.type)}</span>
                <span className="text-2xs text-text-muted">{group.typeCount}</span>
              </button>

              {typeOpen && (
                <div className="ml-[15px] pl-3 border-l" style={{ borderColor: 'rgba(255,255,255,0.07)' }}>
                  {group.topics.map(tp => {
                    const tOpen = topicExpanded.has(topicKeyOf(group.type, tp.topic))
                    return (
                      <div key={tp.topic}>
                        {/* 主题分支 */}
                        <button
                          onClick={() => toggleTopic(group.type, tp.topic)}
                          className="w-full flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-white/5 transition-colors"
                        >
                          <Icon
                            icon={`fa-solid ${tOpen ? 'fa-chevron-down' : 'fa-chevron-right'} text-[8px] text-text-muted`}
                          />
                          <Icon icon="fa-solid fa-folder text-2xs text-text-muted" />
                          <span className="text-xs text-text-secondary flex-1 text-left truncate">
                            {tp.topic || t('knowledgeGraph.mindMapUnclassified')}
                          </span>
                          <span className="text-[9px] text-text-dim">{tp.ents.length}</span>
                        </button>
                        {tOpen && (
                          <div className="ml-[15px] pl-3 border-l" style={{ borderColor: 'rgba(255,255,255,0.05)' }}>
                            {tp.ents.map(n => (
                              <button
                                key={n.id}
                                onClick={() => onSelect?.(n)}
                                className={`w-full flex items-center gap-2 px-2 py-[3px] rounded-md hover:bg-white/5 transition-colors ${
                                  selectedId === n.id ? 'bg-fluent-blue/10' : ''
                                }`}
                              >
                                <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: nodeColor(n.type) }} />
                                <span className="text-xs text-text-secondary truncate flex-1 text-left">{n.label}</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default GraphMindMap
