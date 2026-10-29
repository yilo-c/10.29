import React, { useState, useEffect, useRef, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { undoManager, UndoEvent } from '../../store/undoManager'
import { useTranslation } from '../../i18n'

const TOAST_DURATION = 4000

const UndoToast: React.FC = () => {
  const { t } = useTranslation()
  const [toast, setToast] = useState<{ label: string } | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout>>()
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    const unsub = undoManager.subscribe((event: UndoEvent) => {
      if (event.type === 'push') {
        setToast({ label: event.label })
        if (timerRef.current) clearTimeout(timerRef.current)
        timerRef.current = setTimeout(() => {
          if (mountedRef.current) setToast(null)
        }, TOAST_DURATION)
      } else {
        if (timerRef.current) clearTimeout(timerRef.current)
        if (mountedRef.current) setToast(null)
      }
    })
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      unsub()
    }
  }, [])

  const handleUndo = useCallback(() => {
    undoManager.undo()
  }, [])

  return (
    <AnimatePresence>
      {toast && (
        <motion.div
          initial={{ opacity: 0, y: 16, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 12, scale: 0.95 }}
          transition={{ duration: 0.2 }}
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[99999]"
        >
          <div
            className="flex items-center gap-3 px-4 py-2.5 rounded-xl shadow-2xl backdrop-blur-xl border text-xs"
            style={{
              background: 'var(--tooltip-bg)',
              borderColor: 'var(--panel-border-accent)',
            }}
          >
            <span style={{ color: 'var(--text-secondary)' }}>{toast.label}</span>
            <button
              onClick={handleUndo}
              className="font-medium transition-colors hover:opacity-80"
              style={{ color: 'var(--accent, var(--brand))' }}
            >
              {t('common.undo')}
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}

export default UndoToast
