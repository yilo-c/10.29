import React, { useState, useEffect, useRef } from 'react'
import Icon from '../Common/Icon'

import { createPortal } from 'react-dom'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { useFocusTrap } from '../../hooks/useFocusTrap'
import { trackUsage } from '../../utils/trackUsage'

interface Props {
  isOpen: boolean
  onClose: () => void
}

const QuickTodoInput: React.FC<Props> = ({ isOpen, onClose }) => {
  const addTodo = useStore(s => s.addTodo)
  const activeCat = useStore(s => s.activeCat)
  const { t } = useTranslation()
  const [text, setText] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  useFocusTrap(dialogRef, isOpen)

  useEffect(() => {
    if (isOpen) {
      setText('')
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [isOpen])

  if (!isOpen) return null

  const submit = () => {
    const v = text.trim()
    if (v) {
      addTodo(v, activeCat === 'all' ? undefined : activeCat)
      trackUsage('capture.task')
    }
    onClose()
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[99999] flex items-start justify-center pt-[15vh]"
      onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={t('todo.addPlaceholder')}
        tabIndex={-1}
        className="border border-white/[0.08] rounded-xl shadow-2xl backdrop-blur-xl w-[480px] max-w-[90vw] overflow-hidden"
        style={{ background: 'var(--panel-bg-solid)' }}
      >
        <div className="flex items-center gap-2 px-3 py-2.5">
          <Icon icon="fa-solid fa-list-check text-sm text-text-muted" />
          <input
            ref={inputRef}
            value={text}
            onChange={e => setText(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') submit()
              if (e.key === 'Escape') onClose()
            }}
            placeholder={t('todo.addPlaceholder')}
            className="flex-1 bg-transparent text-sm text-text-primary placeholder:text-text-muted outline-none"
          />
          {activeCat !== 'all' && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-white/[0.06] text-text-secondary flex-shrink-0">
              {useStore.getState().categories.find(c => c.id === activeCat)?.label || activeCat}
            </span>
          )}
          <button
            onClick={submit}
            className="text-xs text-fluent-blue/70 hover:text-fluent-blue transition-colors flex-shrink-0"
          >
            {t('common.add')}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}

export default QuickTodoInput
