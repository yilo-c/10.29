import React, { useState, useEffect, useRef, useCallback } from 'react'
import Icon from './Icon'

import { motion, AnimatePresence } from 'framer-motion'
import { errorStore, ErrorEvent, ErrorMessage } from '../../store/errorStore'

const DISMISS_DELAY = 5000
const MAX_VISIBLE = 3

const LEVEL_STYLES: Record<string, { bg: string; border: string; icon: string; dot: string }> = {
  error:  { bg: 'rgba(239,68,68,0.1)',  border: 'rgba(239,68,68,0.2)',  icon: 'fa-circle-exclamation', dot: 'var(--danger)' },
  warn:   { bg: 'rgba(251,191,36,0.1)', border: 'rgba(251,191,36,0.2)', icon: 'fa-triangle-exclamation', dot: 'var(--warning)' },
  info:   { bg: 'rgba(96,165,250,0.1)', border: 'rgba(96,165,250,0.2)', icon: 'fa-circle-info', dot: 'var(--brand)' },
  success:{ bg: 'rgba(34,197,94,0.1)',  border: 'rgba(34,197,94,0.2)',  icon: 'fa-circle-check', dot: 'var(--success)' },
}

const ErrorToast: React.FC = () => {
  const [errors, setErrors] = useState<ErrorMessage[]>([])
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  useEffect(() => {
    const unsub = errorStore.subscribe((event: ErrorEvent) => {
      if (!mountedRef.current) return
      if (event.type === 'push') {
        setErrors(prev => {
          const next = [event.message, ...prev].slice(0, MAX_VISIBLE + 5)
          return next
        })
        const id = event.message.id
        if (timers.current.has(id)) clearTimeout(timers.current.get(id)!)
        timers.current.set(id, setTimeout(() => {
          if (mountedRef.current) {
            setErrors(prev => prev.filter(e => e.id !== id))
          }
          timers.current.delete(id)
        }, DISMISS_DELAY))
      } else if (event.type === 'dismiss') {
        setErrors(prev => prev.filter(e => e.id !== event.id))
        if (timers.current.has(event.id)) {
          clearTimeout(timers.current.get(event.id)!)
          timers.current.delete(event.id)
        }
      } else if (event.type === 'clear') {
        setErrors([])
        for (const t of timers.current.values()) clearTimeout(t)
        timers.current.clear()
      }
    })
    return () => {
      unsub()
      for (const t of timers.current.values()) clearTimeout(t)
      timers.current.clear()
    }
  }, [])

  const handleDismiss = useCallback((id: string) => {
    errorStore.dismiss(id)
  }, [])

  return (
    <div className="fixed top-4 right-4 z-[99999] flex flex-col gap-2 pointer-events-none" style={{ maxWidth: 360 }}>
      <AnimatePresence>
        {errors.slice(0, MAX_VISIBLE).map(e => {
          const style = LEVEL_STYLES[e.level] || LEVEL_STYLES.error
          return (
            <motion.div
              key={e.id}
              initial={{ opacity: 0, x: 40, scale: 0.95 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.95 }}
              transition={{ duration: 0.2 }}
              className="pointer-events-auto rounded-xl border shadow-2xl backdrop-blur-xl text-xs leading-relaxed overflow-hidden"
              style={{ background: style.bg, borderColor: style.border }}
              role="alert"
            >
              <div className="flex items-start gap-2 px-3 py-2">
                <span className="flex-shrink-0 mt-0.5" style={{ color: style.dot }}>
                  <Icon icon={`fa-solid ${style.icon}`} />
                </span>
                <span className="flex-1 min-w-0" style={{ color: 'var(--text-secondary)' }}>
                  {e.message}
                </span>
                <button
                  onClick={() => handleDismiss(e.id)}
                  className="flex-shrink-0 opacity-40 hover:opacity-100 transition-opacity"
                  style={{ color: 'var(--text-dim)' }}
                >
                  <Icon icon="fa-solid fa-xmark text-2xs" />
                </button>
              </div>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </div>
  )
}

export default ErrorToast
