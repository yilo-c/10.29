/* ── 手机端知识视图：实体搜索 + 关系列表 + 关联笔记 ── */

import { useState, useCallback } from 'react'
import { searchKnowledge, knowledgeRelations } from '../../../lib/bridges'

interface GraphEntity {
  id: string
  name: string
  type: string
  type_display?: string
}

interface GraphRelation {
  source_id: string
  target_id: string
  relation_type: string
  relation_type_display?: string
  source_entity?: string
  target_entity?: string
}

const TYPE_LABELS: Record<string, string> = {
  person: '人物', project: '项目', concept: '概念',
  location: '地点', event: '事件', organization: '组织',
  date: '日期', tag: '标签', unknown: '其他',
}

const REL_LABELS: Record<string, string> = {
  mentioned_in: '提及于', related_to: '关联', part_of: '属于',
  located_at: '位于', participated_in: '参与',
  before: '之前', after: '之后', same_as: '等同于',
}

const TYPE_ICONS: Record<string, string> = {
  person: '👤', project: '📁', concept: '💡',
  location: '📍', event: '📅', organization: '🏢',
  date: '🕐', tag: '🏷', unknown: '📌',
}

export default function KnowledgeView({ onClose }: { onClose: () => void }) {
  const [query, setQuery] = useState('')
  const [entities, setEntities] = useState<GraphEntity[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [relations, setRelations] = useState<GraphRelation[]>([])

  const search = useCallback(async (q: string) => {
    if (!q.trim()) { setEntities([]); return }
    setLoading(true); setError('')
    try {
      const result = await searchKnowledge(q)
      setEntities(Array.isArray(result) ? (result as unknown as GraphEntity[]) : [])
    } catch {
      setError('后端未连接')
    }
    setLoading(false)
  }, [])

  const loadRelations = useCallback(async (entityId: string) => {
    try {
      const result = await knowledgeRelations(entityId)
      setRelations(Array.isArray(result) ? (result as GraphRelation[]) : [])
    } catch { setRelations([]) }
  }, [])

  const handleSelect = (entity: GraphEntity) => {
    setSelectedId(entity.id === selectedId ? null : entity.id)
    if (entity.id !== selectedId) loadRelations(entity.id)
    else setRelations([])
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: 'var(--bg-primary)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '16px 20px 0', flexShrink: 0 }}>
        <div style={{ fontSize: 'var(--text-large-title)', fontWeight: 700 }}>知识图谱</div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 'var(--text-body)', padding: '4px 8px' }}>关闭</button>
      </div>

      <div style={{ padding: '12px 20px', flexShrink: 0 }}>
        <input
          value={query}
          onChange={e => { setQuery(e.target.value); search(e.target.value) }}
          placeholder="搜索实体（人物、项目、概念...）"
          style={{
            width: '100%', padding: '10px 14px', borderRadius: 'var(--radius-md)',
            border: '0.5px solid var(--border-default)', background: 'var(--bg-secondary)',
            color: 'var(--text-primary)', fontSize: 'var(--text-footnote)', outline: 'none',
            boxSizing: 'border-box',
          }}
        />
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: '0 20px' }}>
        {loading && <div style={{ padding: 20, textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 'var(--text-caption-2)' }}>搜索中...</div>}
        {error && <div style={{ padding: 20, textAlign: 'center', color: '#ef4444', fontSize: 'var(--text-caption-2)' }}>{error}</div>}
        {!loading && !error && entities.length === 0 && query && (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-disabled)', fontSize: 'var(--text-footnote)' }}>未找到实体</div>
        )}
        {!query && (
          <div style={{ padding: 40, textAlign: 'center', color: 'var(--text-disabled)', fontSize: 'var(--text-footnote)' }}>
            输入关键词搜索知识图谱中的实体
          </div>
        )}

        {entities.map(e => (
          <div key={e.id}>
            <div
              onClick={() => handleSelect(e)}
              style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '12px 0', borderBottom: '0.5px solid var(--border-subtle)',
                cursor: 'pointer', background: selectedId === e.id ? 'var(--accent-surface)' : 'transparent',
                borderRadius: selectedId === e.id ? 'var(--radius-sm)' : 0,
                paddingLeft: selectedId === e.id ? 8 : 0,
              }}
            >
              <span style={{ fontSize: 18 }}>{TYPE_ICONS[e.type] || TYPE_ICONS.unknown}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 'var(--text-body)', fontWeight: 500, color: 'var(--text-primary)' }}>{e.name}</div>
                <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
                  {e.type_display || TYPE_LABELS[e.type] || e.type}
                </div>
              </div>
            </div>

            {selectedId === e.id && (
              <div style={{ padding: '8px 0 12px 28px' }}>
                <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-secondary)', fontWeight: 600, marginBottom: 6 }}>
                  关联关系 ({relations.length})
                </div>
                {relations.length === 0 && (
                  <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)', padding: '4px 0' }}>暂无关联</div>
                )}
                {relations.map((r, i) => {
                  const isSource = r.source_id === e.id
                  const otherName = isSource ? r.target_entity : r.source_entity
                  const dir = isSource ? '→' : '←'
                  const relLabel = r.relation_type_display || REL_LABELS[r.relation_type] || r.relation_type || '关联'
                  return (
                    <div key={i} style={{
                      display: 'flex', alignItems: 'center', gap: 6,
                      padding: '4px 0', fontSize: 'var(--text-caption-2)',
                      color: 'var(--text-secondary)',
                    }}>
                      <span style={{ color: 'var(--text-disabled)', fontSize: 10 }}>{dir}</span>
                      <span>{otherName || '?'}</span>
                      <span style={{ color: 'var(--text-tertiary)', fontSize: 10 }}>({relLabel})</span>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
