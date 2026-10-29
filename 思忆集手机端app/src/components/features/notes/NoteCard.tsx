import { useState, useEffect } from 'react'
import { SwipeableRow, IconMore } from '../../ui'
import type { FloatingNote } from '@desk-notes/shared'

function formatRelativeTime(ts?: number): string {
  if (!ts) return ''
  const diff = Date.now() - ts
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  if (diff < 172800000) return '昨天'
  const d = new Date(ts)
  const now = new Date()
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()}`
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}

interface NoteCardProps {
  note: FloatingNote
  onEdit: (note: FloatingNote) => void
  onAction: () => void
  onDelete: () => void
}

export default function NoteCard({ note, onEdit, onAction, onDelete }: NoteCardProps) {
  return (
    <div style={{ marginBottom: 12 }}>
      <SwipeableRow onDelete={onDelete}>
        <div
          onClick={() => onEdit(note)}
          style={{
            background: 'var(--bg-secondary)',
            borderRadius: 'var(--radius-lg)',
            padding: 16, cursor: 'pointer',
            transition: 'background 0.15s ease',
            position: 'relative', overflow: 'hidden',
          }}
        >
          {note.color && (
            <div style={{
              position: 'absolute', left: 0, top: 0, bottom: 0, width: 3,
              background: note.color, borderRadius: '1.5px',
            }} />
          )}
          <div style={{ fontSize: 'var(--text-body)', fontWeight: 600, marginBottom: 4, color: 'var(--text-primary)' }}>
            {note.pinned && <span style={{ fontSize: 12, color: 'var(--accent)', marginRight: 4 }}>📌</span>}
            {note.title || '无标题'}
          </div>
          {note.tags && note.tags.length > 0 && (
            <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 6 }}>
              {note.tags.map(tag => (
                <span key={tag} style={{ fontSize: 10, color: 'var(--accent)', background: 'var(--accent-surface)', padding: '1px 6px', borderRadius: 'var(--radius-full)', fontWeight: 500 }}>
                  {tag}
                </span>
              ))}
            </div>
          )}
          <div style={{
            fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)',
            lineHeight: 1.5, overflow: 'hidden', textOverflow: 'ellipsis',
            display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
          }}>
            {note.editMode === 'markdown'
              ? (note.content || '').replace(/[#*`_>-]+/g, '').trim() || '无内容'
              : (note.content || '').split('\n').slice(0, 2).join(' ') || '无内容'}
          </div>
          <AttachmentSummary content={note.content || ''} />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 10 }}>
            <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)' }}>
              {formatRelativeTime(note.updatedAt || note.createdAt)}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); onAction() }}
              style={{ background: 'none', border: 'none', color: 'var(--text-disabled)', cursor: 'pointer', display: 'flex', padding: '4px 8px' }}
            >
              <IconMore size={16} />
            </button>
          </div>
        </div>
      </SwipeableRow>
    </div>
  )
}


/** 附件摘要：从 Markdown 中提取 siyi-file:// 数量并显示。 */
function AttachmentSummary({ content }: { content: string }) {
  const [summary, setSummary] = useState<string | null>(null)

  useEffect(() => {
    if (!content || !content.includes('siyi-file://')) {
      setSummary(null)
      return
    }
    import('@desk-notes/shared').then((mod) => {
      const text = mod.formatAttachmentSummary(content)
      setSummary(text || null)
    })
  }, [content])

  if (!summary) return null

  return (
    <div style={{
      fontSize: 'var(--text-caption-2)', color: 'var(--accent)',
      marginTop: 4,
    }}>
      {summary}
    </div>
  )
}
