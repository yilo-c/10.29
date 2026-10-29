import { useState, useEffect, useRef } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import { getWsClient } from '../../utils/ai'

/**
 * 相关记忆条目（匹配后端 memory.related_text RPC 返回格式）。
 * 后端实现：memory/search/_engine.py HybridMemorySearch.find_related()
 */
interface RelatedMemory {
  id: string
  title: string
  summary: string
  source_type: string  // note/task/inbox/decision/execution_run/pattern/memory
  source_label: string  // 中文标签
  score: number  // 相关性分数 0-1
  created_at: string
}

interface Props {
  text: string
}

// source_type → 图标映射
const SOURCE_ICONS: Record<string, string> = {
  note: 'fa-solid fa-note-sticky',
  task: 'fa-solid fa-check',
  inbox: 'fa-solid fa-inbox',
  decision: 'fa-solid fa-code-branch',
  execution_run: 'fa-solid fa-terminal',
  pattern: 'fa-solid fa-diagram-project',
  memory: 'fa-regular fa-clock',
  chat: 'fa-regular fa-comment',
}

// 相关性分数 → 颜色
function scoreColor(score: number): string {
  if (score >= 0.7) return 'var(--success)'  // 强相关：绿
  if (score >= 0.5) return '#eab308'  // 中相关：黄
  return '#6b7280'  // 弱相关：灰
}

export default function RelatedMemories({ text }: Props) {
  const { t } = useTranslation()
  const [memories, setMemories] = useState<RelatedMemory[]>([])
  const [loading, setLoading] = useState(false)
  const [collapsed, setCollapsed] = useState(true)
  const [error, setError] = useState('')
  const lastTextRef = useRef('')
  const seqRef = useRef(0)

  useEffect(() => {
    if (!text.trim() || text === lastTextRef.current) return
    lastTextRef.current = text
    const seq = ++seqRef.current
    setLoading(true)
    setError('')

    const client = getWsClient()
    if (client.status !== 'connected') {
      setLoading(false)
      return
    }

    // 使用 memory.related_text（专为记忆浮现设计，返回精简字段 + score）
    client
      .call<RelatedMemory[]>('memory.related_text', { text: text.slice(0, 500), limit: 6 })
      .then((results) => {
        if (seq !== seqRef.current) return
        const items = Array.isArray(results) ? results : []
        setMemories(items)
        setCollapsed(items.length === 0)
        setLoading(false)
      })
      .catch(() => {
        if (seq !== seqRef.current) return
        setLoading(false)
        setError('')
      })
  }, [text])

  if (loading) {
    return (
      <div className="px-3 py-1.5">
        <div className="flex items-center gap-2 text-2xs" style={{ color: 'var(--text-dim)' }}>
          <Icon icon="fa-solid fa-circle-notch text-[8px] animate-spin" />
          {t('relatedMemories.loading')}
        </div>
      </div>
    )
  }

  if (memories.length === 0 || error) return null

  return (
    <div className="border-t" style={{ borderColor: 'var(--separator)' }}>
      <button
        onClick={() => setCollapsed(v => !v)}
        className="flex items-center gap-1.5 w-full px-3 py-1 text-2xs transition-all hover:bg-white/[0.03]"
        style={{ color: 'var(--text-muted)' }}
      >
        <Icon icon={`fa-solid fa-chevron-down text-[7px] transition-transform ${collapsed ? '' : 'rotate-180'}`} />
        <Icon icon="fa-solid fa-link text-[8px]" />
        {t('relatedMemories.title', { count: memories.length })}
      </button>

      {!collapsed && (
        <div className="px-3 pb-2 space-y-1.5">
          {memories.map((mem, i) => {
            const srcIcon = SOURCE_ICONS[mem.source_type] || 'fa-regular fa-clock'
            const color = scoreColor(mem.score)
            const label = mem.source_label || ''

            return (
              <div
                key={mem.id || i}
                className="flex items-start gap-2 px-2 py-1.5 rounded-lg text-xs leading-relaxed transition-all hover:bg-white/[0.04] cursor-default"
                style={{ color: 'var(--text-secondary)' }}
              >
                <Icon icon={`${srcIcon} text-[8px] mt-0.5 flex-shrink-0`} style={{ color: 'var(--text-dim)' }} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-1.5">
                    {mem.title && (
                      <span className="font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                        {mem.title}
                      </span>
                    )}
                    {label && (
                      <span
                        className="flex-shrink-0 px-1 py-0.5 rounded text-[7px] font-medium uppercase tracking-wider"
                        style={{ background: `${color}18`, color }}
                      >
                        {label}
                      </span>
                    )}
                  </div>
                  <div className="line-clamp-2 text-2xs mt-0.5" style={{ color: 'var(--text-dim)' }}>
                    {mem.summary.slice(0, 150)}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
