import React, { useState, useEffect } from 'react'
import Icon from './Icon'

import { motion } from 'framer-motion'
import { undoManager } from '@desk-notes/shared/store/undoManager'
import { useTranslation } from '../../i18n'
import { useStore } from '../../store/useStore'

interface Props {
  onClose: () => void
}

const LABEL_ICONS: Record<string, string> = {
  editNote: 'fa-pen',
  deleteNote: 'fa-trash-can',
  deleteTodo: 'fa-list-check',
  markDone: 'fa-check-circle',
  markUndone: 'fa-rotate-left',
  clearDone: 'fa-broom',
  deleteSubtask: 'fa-list',
}

function getIcon(label: string): string {
  for (const [key, icon] of Object.entries(LABEL_ICONS)) {
    if (label.includes(key)) return icon
  }
  return 'fa-clock'
}

function formatTime(ts: number, locale: string): string | null {
  const d = new Date(ts)
  const now = Date.now()
  const diff = now - ts

  if (diff < 60000) return null // will be grouped as "just now"
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h`
  return d.toLocaleDateString(locale === 'en' ? 'en-US' : 'zh-CN', {
    month: '2-digit', day: '2-digit',
  })
}

function isSameDay(ts: number): boolean {
  const d = new Date(ts)
  const now = new Date()
  return d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth()
    && d.getDate() === now.getDate()
}

interface HistoryEntry {
  id: string
  label: string
  timestamp: number
  canUndo: boolean
}

const HistoryTimelinePanel: React.FC<Props> = ({ onClose }) => {
  const { t } = useTranslation()
  const locale = useStore(s => s.locale)
  const [history, setHistory] = useState<HistoryEntry[]>([])

  useEffect(() => {
    setHistory([...undoManager.getHistory()])
    const unsub = undoManager.subscribe(() => {
      setHistory([...undoManager.getHistory()])
    })
    return unsub
  }, [])

  const now = Date.now()
  const justNow = history.filter(e => now - e.timestamp < 60000 && isSameDay(e.timestamp))
  const today = history.filter(e => now - e.timestamp >= 60000 && isSameDay(e.timestamp))
  const earlier = history.filter(e => !isSameDay(e.timestamp))

  const renderEntry = (entry: HistoryEntry) => (
    <div
      key={entry.id}
      className="flex items-center gap-2.5 px-3 py-2.5 border-b last:border-b-0 transition-colors"
      style={{ borderColor: 'var(--separator)' }}
    >
      <Icon icon={`fa-regular ${getIcon(entry.label)} text-xs`} style={{ color: 'var(--text-dim)' }} />
      <div className="flex-1 min-w-0">
        <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>
          {entry.label}
        </div>
        <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>
          {formatTime(entry.timestamp, locale)}
        </div>
      </div>
      <button
        onClick={() => entry.canUndo && undoManager.undo()}
        disabled={!entry.canUndo}
        title={!entry.canUndo ? t('history.cannotUndo') : t('history.undo')}
        className="px-2 py-1 rounded-lg text-xs transition-all flex-shrink-0"
        style={{
          color: entry.canUndo ? 'rgb(96,165,250)' : 'var(--text-dim)',
          background: entry.canUndo ? 'rgba(96,165,250,0.12)' : 'transparent',
          opacity: entry.canUndo ? 1 : 0.3,
        }}
      >
        <Icon icon="fa-solid fa-rotate-left mr-1" />
        {t('history.undo')}
      </button>
    </div>
  )

  const renderGroup = (title: string, entries: HistoryEntry[]) => {
    if (entries.length === 0) return null
    return (
      <div>
        <div className="px-3 py-1.5 text-2xs font-medium" style={{ color: 'var(--text-muted)' }}>
          {title}
        </div>
        {entries.map(renderEntry)}
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[10001] flex items-center justify-center"
    >
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 10 }}
        transition={{ duration: 0.15 }}
        className="relative w-[380px] max-w-[90vw] max-h-[70vh] flex flex-col min-h-0 rounded-2xl shadow-2xl overflow-hidden border"
        style={{
          background: 'var(--panel-bg-solid)',
          borderColor: 'var(--panel-border-accent)',
          backdropFilter: 'blur(24px)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--separator)' }}>
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-clock-rotate-left text-xs" style={{ color: 'var(--text-secondary)' }} />
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{t('history.title')}</span>
            <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}>
              {history.length}
            </span>
          </div>
          <button onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-lg transition-all text-xs"
            style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {history.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12" style={{ color: 'var(--text-dim)' }}>
              <Icon icon="fa-regular fa-clock text-lg mb-2" />
              <span className="text-xs">{t('history.empty')}</span>
            </div>
          ) : (
            <>
              {renderGroup(t('history.groupJustNow'), justNow)}
              {renderGroup(t('history.groupToday'), today)}
              {renderGroup(t('history.groupEarlier'), earlier)}
            </>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-center px-4 py-2 border-t" style={{ borderColor: 'var(--separator)' }}>
          <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>
            <Icon icon="fa-solid fa-info-circle mr-1" />
            {t('history.footerNote')}
          </span>
        </div>
      </motion.div>
    </motion.div>
  )
}

export default HistoryTimelinePanel
