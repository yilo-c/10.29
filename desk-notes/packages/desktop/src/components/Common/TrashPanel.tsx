import React, { useState, useEffect } from 'react'
import Icon from './Icon'

import { motion, AnimatePresence } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { getTrashItems, restoreFromTrash, permanentlyDelete, clearTrash, subscribe, TrashItem } from '../../utils/trash'
import { TodoItem, FloatingNote } from '../../types'
import { useTranslation } from '../../i18n'

interface Props {
  onClose: () => void
}

const TrashPanel: React.FC<Props> = ({ onClose }) => {
  const addTodo = useStore(s => s.addTodo)
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const categories = useStore(s => s.categories)
  const permanentlyDeleteTodo = useStore(s => s.permanentlyDeleteTodo)
  const permanentlyDeleteNote = useStore(s => s.permanentlyDeleteNote)
  const { t } = useTranslation()
  const [items, setItems] = useState<TrashItem[]>([])
  const [confirmClear, setConfirmClear] = useState(false)

  useEffect(() => {
    setItems(getTrashItems())
    const unsub = subscribe(() => setItems(getTrashItems()))
    return unsub
  }, [])

  const refresh = () => setItems(getTrashItems())

  const handleRestore = (item: TrashItem) => {
    const restored = restoreFromTrash(item.id)
    if (!restored) return

    if (restored.type === 'todo') {
      const todo = restored.data as TodoItem
      // If original category was deleted, set to undefined
      if (todo.category && !categories.find(c => c.id === todo.category)) {
        todo.category = undefined
      }
      addTodo(todo.text, todo.category)
    } else if (restored.type === 'note') {
      const note = restored.data as FloatingNote
      addFloatingNote({ ...note, archived: false, floated: false })
    }
    refresh()
  }

  const handlePermanentDelete = (item: TrashItem) => {
    if (item.type === 'todo') {
      permanentlyDeleteTodo(item.id)
    } else if (item.type === 'note') {
      permanentlyDeleteNote(item.id)
    }
    permanentlyDelete(item.id)
    refresh()
  }

  const handleClearAll = () => {
    clearTrash()
    setConfirmClear(false)
    refresh()
  }

  const formatTime = (ts: number) => {
    const d = new Date(ts)
    return d.toLocaleString(useStore.getState().locale === 'en' ? 'en-US' : 'zh-CN', {
      month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit',
    })
  }

  const getItemTitle = (item: TrashItem): string => {
    if (item.type === 'todo') {
      return (item.data as TodoItem).text
    }
    return (item.data as FloatingNote).title
  }


  // ESC 关闭（2026-10-16 E2E sweep 缺陷修复：modal 家族共性无 ESC）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      className="fixed inset-0 z-[10001] flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={t('trash.title')}
    >
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />

      {/* Panel */}
      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 10 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.95, y: 10 }}
        transition={{ duration: 0.15 }}
        className="relative w-[420px] max-w-[90vw] max-h-[70vh] flex flex-col min-h-0 rounded-2xl shadow-2xl overflow-hidden border"
        style={{
          background: 'var(--panel-bg-solid)',
          borderColor: 'var(--panel-border-accent)',
          backdropFilter: 'blur(24px)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--separator)' }}>
          <div className="flex items-center gap-2">
            <Icon icon="fa-regular fa-trash-can text-xs" style={{ color: 'var(--text-secondary)' }} />
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{t('trash.title')}</span>
            <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: 'var(--hover-bg)', color: 'var(--text-muted)' }}>
              {items.length}
            </span>
          </div>
          <button onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-lg transition-all text-xs"
            style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto p-2">
          {items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12" style={{ color: 'var(--text-dim)' }}>
              <Icon icon="fa-regular fa-trash-can text-lg mb-2" />
              <span className="text-xs">{t('trash.empty')}</span>
            </div>
          ) : (
            <AnimatePresence>
              {items.map(item => (
                <motion.div
                  key={item.id}
                  initial={{ opacity: 0, x: -8 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 8 }}
                  transition={{ duration: 0.15 }}
                  className="flex items-center gap-2 px-3 py-2 rounded-xl transition-colors group"
                  style={{ borderBottom: `1px solid var(--separator)` }}
                >
                  <i
                    className={`fa-regular text-xs ${item.type === 'todo' ? 'fa-list-check' : 'fa-note-sticky'}`}
                    style={{ color: 'var(--text-dim)' }}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs truncate" style={{ color: 'var(--text-primary)' }}>
                      {getItemTitle(item)}
                    </div>
                    <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                      {item.type === 'todo' ? t('trash.todo') : t('trash.note')} · {formatTime(item.deletedAt)}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => handleRestore(item)}
                      className="px-2 py-1 rounded-lg text-xs transition-all hover:bg-white/[0.06]"
                      style={{ color: 'var(--text-secondary)' }}
                      title={t('trash.restore')}>
                      <Icon icon="fa-solid fa-rotate-left" />
                    </button>
                    <button onClick={() => handlePermanentDelete(item)}
                      className="px-2 py-1 rounded-lg text-xs transition-all hover:bg-white/[0.06]"
                      style={{ color: 'var(--text-muted)' }}
                      title={t('trash.permanentDelete')}>
                      <Icon icon="fa-regular fa-trash-can" />
                    </button>
                  </div>
                </motion.div>
              ))}
            </AnimatePresence>
          )}
        </div>

        {/* Footer */}
        {items.length > 0 && (
          <div className="flex items-center justify-end px-4 py-2.5 border-t" style={{ borderColor: 'var(--separator)' }}>
            {confirmClear ? (
              <div className="flex items-center gap-2">
                <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('trash.confirmClear')}</span>
                <button onClick={handleClearAll}
                  className="px-2.5 py-1 rounded-lg text-xs bg-red-500/15 text-red-400 hover:bg-red-500/20 transition-colors">
                  {t('trash.confirmClearBtn')}
                </button>
                <button onClick={() => setConfirmClear(false)}
                  className="px-2.5 py-1 rounded-lg text-xs transition-colors"
                  style={{ color: 'var(--text-muted)' }}>
                  {t('common.cancel')}
                </button>
              </div>
            ) : (
              <button onClick={() => setConfirmClear(true)}
                className="px-2.5 py-1 rounded-lg text-xs transition-colors"
                style={{ color: 'var(--text-muted)' }}>
                {t('trash.clearAll')}
              </button>
            )}
          </div>
        )}
      </motion.div>
    </motion.div>
  )
}

export default TrashPanel
