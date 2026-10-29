import React, { useState, useMemo, useEffect } from 'react'
import Icon from '../Common/Icon'

import { stripHtml } from '../../utils/helpers'
import { getHistory, addSnapshot, computeDiff, type VersionEntry } from '../../utils/noteHistory'
import type { FloatingNote } from '../../types'
import { useTranslation } from '../../i18n'

interface Props {
  note: FloatingNote
  onClose: () => void
  onRevert: (version: VersionEntry) => void
}

const VersionHistoryPanel: React.FC<Props> = ({ note, onClose, onRevert }) => {
  const { t } = useTranslation()
  const [versions, setVersions] = useState<VersionEntry[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setVersions(getHistory(note.id))
  }, [note.id])

  const selected = selectedId ? versions.find(v => v.id === selectedId) ?? null : null

  const diff = useMemo(() => {
    if (!selected) return []
    const oldText = stripHtml(selected.content || '')
    const newText = stripHtml(note.content || '')
    return computeDiff(oldText, newText)
  }, [selected, note.content])

  const handleSaveVersion = () => {
    setSaving(true)
    addSnapshot(note.id, note.title, note.content || '', note.todos)
    setVersions(getHistory(note.id))
    setTimeout(() => setSaving(false), 600)
  }

  return (
    <div
      className="fixed inset-0 z-[99999] flex items-center justify-center"
      onMouseDown={onClose}
      style={{ background: 'rgba(0,0,0,0.45)' }}
    >
      <div
        className="w-full max-w-[520px] max-h-[80vh] border rounded-xl shadow-2xl overflow-hidden backdrop-blur-xl flex flex-col"
        onMouseDown={e => e.stopPropagation()}
        style={{
          background: 'var(--panel-bg-solid, rgba(22,22,32,0.95))',
          borderColor: 'var(--panel-border, rgba(255,255,255,0.08))',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--panel-border, rgba(255,255,255,0.06))' }}>
          <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
            {t('noteHistory.title')} — {note.title}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={handleSaveVersion}
              disabled={saving}
              className="text-2xs px-2 py-1 rounded flex items-center gap-1 transition-all disabled:opacity-40"
              style={{ background: 'var(--hover-bg, rgba(255,255,255,0.06))', color: 'var(--text-secondary)' }}
            >
              <Icon icon={`fa-solid ${saving ? 'fa-check' : 'fa-floppy-disk'}`} />
              {saving ? t('common.saved') : t('noteHistory.saveVersion')}
            </button>
            <button onClick={onClose}
              className="w-5 h-5 flex items-center justify-center rounded text-white/55 hover:text-text-secondary hover:bg-white/[0.06] transition-all text-2xs">
              <Icon icon="fa-solid fa-xmark" />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex flex-1 min-h-0">
          {/* Version list */}
          <div className="w-1/2 overflow-y-auto border-r" style={{ borderColor: 'var(--panel-border, rgba(255,255,255,0.04))' }}>
            {versions.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 gap-2">
                <Icon icon="fa-regular fa-clock text-lg" style={{ color: 'var(--text-muted)' }} />
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('noteHistory.noVersions')}</span>
              </div>
            ) : (
              [...versions].reverse().map(v => {
                const isSelected = v.id === selectedId
                const preview = stripHtml(v.content || '').slice(0, 60)
                const time = new Date(v.timestamp).toLocaleString('zh-CN', {
                  month: '2-digit', day: '2-digit',
                  hour: '2-digit', minute: '2-digit',
                })
                return (
                  <button
                    key={v.id}
                    onClick={() => setSelectedId(v.id)}
                    className="w-full text-left px-3 py-2 border-b transition-colors"
                    style={{
                      borderColor: 'var(--panel-border, rgba(255,255,255,0.04))',
                      background: isSelected ? 'var(--hover-bg, rgba(255,255,255,0.06))' : undefined,
                    }}
                  >
                    <div className="text-2xs" style={{ color: 'var(--text-dim, rgba(255,255,255,0.3))' }}>
                      {time}
                    </div>
                    <div className="text-xs mt-0.5 truncate" style={{ color: 'var(--text-secondary)' }}>
                      {v.title} — {preview || '...'}
                    </div>
                  </button>
                )
              })
            )}
          </div>

          {/* Diff view */}
          <div className="w-1/2 overflow-y-auto">
            {!selected ? (
              <div className="flex flex-col items-center justify-center h-full py-8 gap-2">
                <Icon icon="fa-regular fa-file-lines text-lg" style={{ color: 'var(--text-muted)' }} />
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {t('noteHistory.selectVersion')}
                </span>
              </div>
            ) : diff.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full py-8 gap-2">
                <Icon icon="fa-regular fa-circle-check text-lg text-green-400/60" />
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  {t('noteHistory.noChanges')}
                </span>
              </div>
            ) : (
              <div className="px-3 py-2">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>
                    {t('noteHistory.diff')}
                  </span>
                  <button
                    onClick={() => onRevert(selected)}
                    className="text-2xs px-2 py-1 rounded flex items-center gap-1 transition-all"
                    style={{ background: 'rgba(96,165,250,0.12)', color: 'var(--brand)' }}
                  >
                    <Icon icon="fa-solid fa-rotate-left" />
                    {t('noteHistory.revert')}
                  </button>
                </div>
                <div className="font-mono text-xs leading-relaxed space-y-0.5">
                  {diff.map((line, i) => (
                    <div
                      key={i}
                      className="whitespace-pre-wrap break-all px-1 py-0.5 rounded"
                      style={{
                        background: line.type === 'added'
                          ? 'rgba(52,211,153,0.08)'
                          : line.type === 'removed'
                          ? 'rgba(248,113,113,0.08)'
                          : undefined,
                        color: line.type === 'added'
                          ? 'var(--success)'
                          : line.type === 'removed'
                          ? 'var(--danger)'
                          : 'var(--text-secondary)',
                      }}
                    >
                      <span className="mr-1 select-none">
                        {line.type === 'added' ? '+' : line.type === 'removed' ? '−' : ' '}
                      </span>
                      {line.text || ' '}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default VersionHistoryPanel
