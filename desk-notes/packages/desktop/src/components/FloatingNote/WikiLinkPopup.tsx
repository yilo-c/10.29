import React, { useState, useMemo, useRef, useEffect } from 'react'
import { useStore } from '../../store/useStore'
import { buildFuseIndex } from '../../utils/wikiLinks'
import { useTranslation } from '../../i18n'

interface WikiLinkPopupProps {
  containerEl: HTMLElement | null
  currentNoteId: string
  onClose: () => void
}

const WikiLinkPopup: React.FC<WikiLinkPopupProps> = ({ containerEl, currentNoteId, onClose }) => {
  const { t } = useTranslation()
  const floatingNotes = useStore(s => s.floatingNotes)
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const popupRef = useRef<HTMLDivElement>(null)

  const candidates = useMemo(() => {
    const notes = floatingNotes.filter(n => n.id !== currentNoteId && !n.archived)
    if (!query.trim()) return notes.map(n => ({ item: n, score: 0 }))
    const fuse = buildFuseIndex(notes.map(n => ({ id: n.id, title: n.title })))
    return fuse.search(query)
  }, [floatingNotes, currentNoteId, query])

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('mousedown', handler, true)
    return () => document.removeEventListener('mousedown', handler, true)
  }, [onClose])

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  const insertLink = (title: string) => {
    const linkText = `[[${title}]]`
    const el = containerEl
    if (el && (el as HTMLElement).isContentEditable) {
      el.focus()
      document.execCommand('insertText', false, linkText)
    }
    onClose()
  }

  return (
    <div
      ref={popupRef}
      className="absolute top-full left-0 mt-1 z-50 w-56 border border-white/[0.08] rounded-lg shadow-xl overflow-hidden"
      style={{ background: 'var(--panel-bg-solid)' }}
    >
      <div className="p-1.5 border-b border-white/[0.06]">
        <input
          ref={inputRef}
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={t('common.search')}
          className="w-full bg-white/[0.06] text-white text-xs rounded px-2 py-1.5 outline-none placeholder-white/30"
          onKeyDown={e => {
            if (e.key === 'Escape') onClose()
          }}
        />
      </div>
      <div className="max-h-48 overflow-y-auto">
        {candidates.length === 0 ? (
          <div className="px-3 py-4 text-center text-xs text-text-muted">{t('common.noData')}</div>
        ) : (
          candidates.map(({ item }) => (
            <button
              key={item.id}
              onClick={() => insertLink(item.title)}
              className="w-full text-left px-3 py-1.5 text-xs text-text-secondary hover:text-white hover:bg-white/[0.06] transition-colors truncate"
            >
              {item.title || t('note.newNote')}
            </button>
          ))
        )}
      </div>
    </div>
  )
}

export default WikiLinkPopup
