import React, { useEffect, useState } from 'react'
import Icon from './Icon'

import { motion, AnimatePresence } from 'framer-motion'
import { useTranslation } from '../../i18n'
import { sanitizeHtml } from '../../utils/sanitize'

const ei = window.electronAPI

interface UsageDocPanelProps {
  onClose: () => void
}

const UsageDocPanel: React.FC<UsageDocPanelProps> = ({ onClose }) => {
  const { t } = useTranslation()
  const [content, setContent] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    ;(async () => {
      if (ei?.readUsageDoc) {
        const md = await ei.readUsageDoc()
        setContent(md)
      }
      setLoading(false)
    })()
  }, [])

  // ESC 关闭（2026-10-16 E2E sweep 缺陷修复：modal 家族共性无 ESC）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [])

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[10002] flex items-center justify-center"
        role="dialog"
        aria-modal="true"
        aria-label={t('usageDoc')}
      >
        <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={onClose} />
        <motion.div
          initial={{ scale: 0.95, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          exit={{ scale: 0.95, opacity: 0 }}
          transition={{ duration: 0.15 }}
          className="relative w-[500px] max-w-[85vw] max-h-[80vh] rounded-xl border shadow-2xl flex flex-col overflow-hidden"
          style={{ background: 'var(--panel-bg-solid)', borderColor: 'var(--panel-border-accent)' }}
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/[0.06]">
            <h2 className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
              {t('toolbar.usageDoc')}
            </h2>
            <button
              onClick={onClose}
              className="w-6 h-6 flex items-center justify-center rounded-lg hover:bg-white/[0.06] transition-colors"
            >
              <Icon icon="fa-solid fa-xmark text-xs" style={{ color: 'var(--text-secondary)' }} />
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-4">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Icon icon="fa-solid fa-spinner fa-spin text-sm text-text-muted" />
              </div>
            ) : content ? (
              <div
                className="prose prose-invert prose-xs max-w-none"
                style={{ color: 'var(--text-primary)' }}
                dangerouslySetInnerHTML={{
                  __html: sanitizeHtml(content
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/^### (.*$)/gm, '<h3 class="text-sm font-semibold mt-4 mb-2">$1</h3>')
                    .replace(/^## (.*$)/gm, '<h2 class="text-base font-semibold mt-5 mb-2">$1</h2>')
                    .replace(/^# (.*$)/gm, '<h1 class="text-lg font-bold mt-5 mb-3">$1</h1>')
                    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
                    .replace(/^- (.*$)/gm, '<li class="ml-4 list-disc text-xs leading-relaxed">$1</li>')
                    .replace(/\n\n/g, '<br/>'),
                )}}
              />
            ) : (
              <div className="flex flex-col items-center justify-center py-12 text-text-dim">
                <Icon icon="fa-solid fa-file-lines text-lg mb-2" />
                <span className="text-xs">{t('common.noData')}</span>
              </div>
            )}
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  )
}

export default UsageDocPanel
