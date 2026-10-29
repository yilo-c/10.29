import React, { useState, useMemo, useCallback, useRef, useEffect } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { buildBacklinkIndex, parseWikiLinks } from '../../utils/wikiLinks'
import type { FloatingNote } from '../../types'

/* ─── helpers ─── */

interface NoteStub {
  id: string
  title: string
  content: string
}

function toStub(n: FloatingNote): NoteStub {
  return { id: n.id, title: n.title, content: n.content || '' }
}

function getContextSnippets(content: string, linkTitle: string): string[] {
  const re = new RegExp(`.{0,40}\\[\\[${escapeRegex(linkTitle)}(?:\\|[^\\]]+)?\\]\\].{0,40}`, 'g')
  const snippets: string[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(content)) !== null) {
    snippets.push(m[0].replace(/[[\]]/g, '').trim())
  }
  return snippets
}

function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/* ─── BacklinksModule ─── */

const BacklinksModule: React.FC = () => {
  const { t } = useTranslation()
  const floatingNotes = useStore(s => s.floatingNotes)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  // Focus search on mount
  useEffect(() => { inputRef.current?.focus() }, [])

  // Build backlink index
  const stubs = useMemo(() => floatingNotes.map(toStub), [floatingNotes])
  const backlinkIndex = useMemo(() => buildBacklinkIndex(stubs), [stubs])

  // All notes with backlink counts
  const notesWithLinks = useMemo(() => {
    const result = floatingNotes.map(n => {
      const st = toStub(n)
      const outgoing = parseWikiLinks(st.content).length
      const incoming = backlinkIndex.get(n.id)?.length || 0
      return { note: n, outgoing, incoming }
    })
    const q = searchQuery.trim().toLowerCase()
    if (q) {
      return result.filter(r => (r.note.title || '').toLowerCase().includes(q))
    }
    return result.sort((a, b) => b.incoming - a.incoming || b.outgoing - a.outgoing)
  }, [floatingNotes, backlinkIndex, searchQuery])

  // Selected note details
  const selectedNote = useMemo(
    () => floatingNotes.find(n => n.id === selectedId) || null,
    [floatingNotes, selectedId],
  )

  // Source notes that link to selected note
  const sourceNotes = useMemo(() => {
    if (!selectedId) return []
    const sourceIds = backlinkIndex.get(selectedId) || []
    return sourceIds
      .map(id => floatingNotes.find(n => n.id === id))
      .filter((n): n is FloatingNote => !!n)
  }, [selectedId, backlinkIndex, floatingNotes])

  // Outgoing links from selected note
  const outgoingLinks = useMemo(() => {
    if (!selectedNote) return []
    const content = selectedNote.content || ''
    const parsed = parseWikiLinks(content)
    return parsed.map(p => {
      const target = floatingNotes.find(n => n.title === p.title || (n.title || '').toLowerCase() === p.title.toLowerCase())
      return { ...p, targetId: target?.id || null }
    })
  }, [selectedNote, floatingNotes])

  const handleSelectNote = useCallback((id: string) => {
    setSelectedId(prev => prev === id ? null : id)
  }, [])

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Search bar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
        <div className="flex items-center gap-1.5 bg-white/[0.04] rounded-lg px-2.5 py-1.5 border border-white/[0.04] focus-within:border-white/[0.1] transition-colors w-56">
          <Icon icon="fa-solid fa-magnifying-glass text-2xs text-text-muted" />
          <input
            ref={inputRef}
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder={t('note.searchPlaceholder')}
            className="flex-1 bg-transparent text-xs text-white/85 placeholder:text-text-muted outline-none"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')} className="text-2xs text-text-muted hover:text-text-secondary">
              <Icon icon="fa-solid fa-xmark" />
            </button>
          )}
        </div>

        <span className="text-2xs text-text-muted tabular-nums">
          {t('backlinks.summary', { count: notesWithLinks.length, total: floatingNotes.reduce((s, n) => s + ((backlinkIndex.get(n.id)?.length) || 0), 0) })}
        </span>
      </div>

      {/* Content: note list + detail */}
      <div className="flex-1 flex min-h-0">
        {/* Note list sidebar */}
        <div className="w-56 flex-shrink-0 overflow-y-auto border-r" style={{ borderColor: 'rgba(255,255,255,0.06)' }}>
          {notesWithLinks.map(({ note, incoming, outgoing }) => {
            const isSelected = note.id === selectedId
            return (
              <button
                key={note.id}
                onClick={() => handleSelectNote(note.id)}
                className={`w-full text-left px-3 py-2 transition-colors hover:bg-white/[0.03] ${
                  isSelected ? 'bg-white/[0.05]' : ''
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className={`flex-1 text-xs truncate ${isSelected ? 'text-text-primary' : 'text-text-secondary'}`}>
                    {note.title || t('note.newNote')}
                  </span>
                  <span className={`text-2xs tabular-nums ${incoming > 0 ? 'text-fluent-blue/60' : 'text-text-dim'}`}>
                    {incoming}
                  </span>
                </div>
                {incoming > 0 && (
                  <div className="text-[8px] text-text-dim mt-0.5">
                    {outgoing > 0 && t('backlinks.outgoingShort', { count: outgoing })}
                  </div>
                )}
              </button>
            )
          })}
          {notesWithLinks.length === 0 && (
            <div className="flex items-center justify-center py-8 text-2xs text-text-dim">
              {t('backlinks.noMatchingNotes')}
            </div>
          )}
        </div>

        {/* Detail panel */}
        <div className="flex-1 overflow-y-auto">
          {!selectedNote ? (
            <div className="flex items-center justify-center py-16 text-text-dim">
              <div className="flex flex-col items-center gap-2">
                <Icon icon="fa-solid fa-link text-lg" />
                <span className="text-xs">{t('backlinks.selectToView')}</span>
              </div>
            </div>
          ) : (
            <div className="px-4 py-3 max-w-2xl">
              {/* Note header */}
              <div className="mb-4">
                <h2 className="text-sm font-medium text-text-primary">{selectedNote.title || t('note.newNote')}</h2>
                <div className="flex items-center gap-3 mt-1 text-2xs text-text-muted">
                  <span>{t('backlinks.incomingCount', { count: sourceNotes.length })}</span>
                  <span>{t('backlinks.outgoingCount', { count: outgoingLinks.length })}</span>
                </div>
              </div>

              {/* Outgoing links */}
              {outgoingLinks.length > 0 && (
                <div className="mb-4">
                  <span className="text-2xs text-text-muted uppercase tracking-wider block mb-1.5">{t('backlinks.labelOutgoing')}</span>
                  <div className="flex flex-wrap gap-1">
                    {outgoingLinks.map((link, i) => (
                      <button
                        key={i}
                        onClick={() => link.targetId && handleSelectNote(link.targetId)}
                        className={`text-xs px-2 py-0.5 rounded-full transition-colors ${
                          link.targetId
                            ? 'text-purple-400/80 bg-purple-400/8 hover:bg-purple-400/16'
                            : 'text-red-400/60 bg-red-400/8 line-through'
                        }`}
                        title={link.targetId ? t('backlinks.jumpToNote', { title: link.title }) : t('backlinks.noteNotFound', { title: link.title })}
                      >
                        {link.display}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Incoming backlinks with context */}
              <span className="text-2xs text-text-muted uppercase tracking-wider block mb-1.5">{t('backlinks.labelIncoming')}</span>
              {sourceNotes.length === 0 ? (
                <div className="text-xs text-text-dim py-4 text-center">{t('backlinks.noReferences')}</div>
              ) : (
                <div className="space-y-2">
                  {sourceNotes.map(sn => {
                    const snippets = getContextSnippets(sn.content || '', selectedNote.title)
                    return (
                      <div
                        key={sn.id}
                        className="rounded-lg px-3 py-2 transition-colors hover:bg-white/[0.03] cursor-pointer"
                        onClick={() => handleSelectNote(sn.id)}
                        style={{ border: '1px solid rgba(255,255,255,0.06)' }}
                      >
                        <div className="flex items-center gap-1.5 mb-1">
                          <Icon icon="fa-regular fa-note-sticky text-[8px] text-text-dim" />
                          <span className="text-xs font-medium text-text-secondary truncate">
                            {sn.title || t('note.newNote')}
                          </span>
                        </div>
                        {snippets.length > 0 && (
                          <div className="space-y-0.5">
                            {snippets.map((snippet, i) => (
                              <div
                                key={i}
                                className="text-2xs text-text-muted leading-relaxed pl-3 border-l-2"
                                style={{ borderColor: 'rgba(167,139,250,0.3)' }}
                              >
                                {snippet}
                              </div>
                            ))}
                          </div>
                        )}
                        <div className="flex items-center gap-2 mt-1 text-[8px] text-text-dim">
                          <span>{t('backlinks.incomingShort', { count: backlinkIndex.get(sn.id)?.length || 0 })}</span>
                          <span>{t('backlinks.outgoingShort', { count: parseWikiLinks(sn.content || '').length })}</span>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default BacklinksModule
