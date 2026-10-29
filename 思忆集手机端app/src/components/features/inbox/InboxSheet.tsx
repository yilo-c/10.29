import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from '@desk-notes/shared'
import { Sheet, IconX, IconNote, IconTodo } from '../../ui'
import { getInboxItems, removeInboxItem, type InboxItem } from '../../../lib/inboxManager'
import EmptyState from '../todo/EmptyState'

interface InboxSheetProps {
  open: boolean
  onClose: () => void
  onCreateNote: (text: string) => void
  onCreateTask: (text: string) => void
}

export default function InboxSheet({ open, onClose, onCreateNote, onCreateTask }: InboxSheetProps) {
  const { t } = useTranslation()
  const [items, setItems] = useState<InboxItem[]>([])

  useEffect(() => {
    if (open) setItems(getInboxItems().filter(i => !i.processed))
  }, [open])

  const handleProcess = useCallback((item: InboxItem, asType: 'note' | 'task') => {
    if (asType === 'note') onCreateNote(item.text)
    else onCreateTask(item.text)
    removeInboxItem(item.id)
    setItems(prev => prev.filter(i => i.id !== item.id))
  }, [onCreateNote, onCreateTask])

  const handleDismiss = useCallback((id: string) => {
    removeInboxItem(id)
    setItems(prev => prev.filter(i => i.id !== id))
  }, [])

  const handleDismissAll = useCallback(() => {
    for (const item of items) removeInboxItem(item.id)
    setItems([])
  }, [items])

  const sourceLabel: Record<string, string> = {
    share: t('mobileInbox.sourceShare'),
    quick_capture: t('mobileInbox.sourceQuickCapture'),
    sync: t('mobileInbox.sourceSync'),
  }

  return (
    <Sheet open={open} onClose={onClose} title={t('mobileInbox.title')}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minHeight: 200 }}>
        {items.length > 0 && (
          <button
            onClick={handleDismissAll}
            style={{
              alignSelf: 'flex-end',
              background: 'none',
              border: 'none',
              color: 'var(--text-tertiary)',
              fontSize: 'var(--text-caption-1)',
              cursor: 'pointer',
              padding: '4px 8px',
              marginBottom: 4,
            }}
          >
            {t('mobileInbox.dismissAll')}
          </button>
        )}

        {items.length === 0 ? (
          <EmptyState
            icon={<IconNote size={40} />}
            title={t('mobileInbox.empty')}
            description={t('mobileInbox.emptyDesc')}
          />
        ) : (
          items.map(item => (
            <div key={item.id} style={{
              display: 'flex', alignItems: 'flex-start', gap: 10,
              padding: '12px 4px',
              borderBottom: '0.5px solid var(--border-subtle)',
            }}>
              <span style={{ color: item.type === 'note' ? 'var(--accent)' : 'var(--tone-warm)', flexShrink: 0, marginTop: 2 }}>
                {item.type === 'note' ? <IconNote size={16} /> : <IconTodo size={16} />}
              </span>

              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 'var(--text-footnote)',
                  color: 'var(--text-primary)',
                  lineHeight: 1.5,
                  wordBreak: 'break-word',
                  marginBottom: 4,
                }}>
                  {item.text}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{
                    fontSize: 'var(--text-caption-2)',
                    color: 'var(--text-tertiary)',
                    background: 'var(--bg-tertiary)',
                    padding: '1px 6px',
                    borderRadius: 'var(--radius-full)',
                  }}>
                    {sourceLabel[item.source] || item.source}
                  </span>
                  <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)' }}>
                    {formatTime(item.createdAt)}
                  </span>
                </div>

                <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                  <button
                    onClick={() => handleProcess(item, 'note')}
                    style={{
                      padding: '4px 12px',
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      background: 'var(--accent-surface)',
                      color: 'var(--accent)',
                      fontSize: 'var(--text-caption-1)',
                      fontWeight: 500,
                      cursor: 'pointer',
                    }}
                  >
                    {t('mobileInbox.asNote')}
                  </button>
                  <button
                    onClick={() => handleProcess(item, 'task')}
                    style={{
                      padding: '4px 12px',
                      borderRadius: 'var(--radius-sm)',
                      border: 'none',
                      background: 'var(--bg-tertiary)',
                      color: 'var(--text-primary)',
                      fontSize: 'var(--text-caption-1)',
                      fontWeight: 500,
                      cursor: 'pointer',
                    }}
                  >
                    {t('mobileInbox.asTask')}
                  </button>
                </div>
              </div>

              <button
                onClick={() => handleDismiss(item.id)}
                aria-label={t('mobileInbox.remove')}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-disabled)',
                  cursor: 'pointer',
                  padding: 4,
                  flexShrink: 0,
                }}
              >
                <IconX size={14} />
              </button>
            </div>
          ))
        )}

        <div style={{ height: 12 }} />
      </div>
    </Sheet>
  )
}

function formatTime(ts: number): string {
  const diff = Date.now() - ts
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`
  const d = new Date(ts)
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
}
