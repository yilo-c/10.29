import React, { useMemo, useState, useCallback } from 'react'
import Icon from '../Common/Icon'

import { createPortal } from 'react-dom'
import { AnimatePresence } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { getDescendantFolderIds } from '../../store/helpers'
import FloatingNoteComp from './FloatingNote'
import CanvasMode from './CanvasMode'
import NotesListView from '../Search/NotesListView'
import { useTranslation } from '../../i18n'

const NotesLayer: React.FC = () => {
  const { t } = useTranslation()
  const floatingNotes = useStore(s => s.floatingNotes)
  const noteSearchQuery = useStore(s => s.noteSearchQuery)
  const setNoteSearchQuery = useStore(s => s.setNoteSearchQuery)
  const panelMode = useStore(s => s.panelMode)
  const notesViewMode = useStore(s => s.notesViewMode)
  const activeFolderId = useStore(s => s.activeFolderId)
  const folders = useStore(s => s.folders)
  const searchHighlight = useStore(s => s.searchHighlight)
  const [showArchived, setShowArchived] = useState(false)
  const [tagFilter, setTagFilter] = useState<string | null>(null)

  // Collect all unique tags
  const allTags = useMemo(() => {
    const tagSet = new Set<string>()
    floatingNotes.forEach(n => (n.tags || []).forEach(t => tagSet.add(t)))
    return Array.from(tagSet).sort()
  }, [floatingNotes])

  const filteredNotes = useMemo(() => {
    let list = showArchived
      ? floatingNotes.filter(n => n.archived)
      : floatingNotes.filter(n => !n.archived)

    // In Electron mode, hide floated notes (they have dedicated OS windows)
    const isElectron = !!(window.electronAPI as ElectronAPI | undefined)
    if (isElectron) {
      list = list.filter(n => !n.floated)
    }

    // Folder filter — include notes from descendant folders too
    if (activeFolderId === '__uncategorized__') {
      list = list.filter(n => !n.folderId)
    } else if (activeFolderId) {
      const folderIds = getDescendantFolderIds(folders, activeFolderId)
      list = list.filter(n => n.folderId && folderIds.includes(n.folderId))
    }

    const q = noteSearchQuery.trim().toLowerCase()
    if (q) {
      const keywords = q.split(/\s+/).filter(Boolean)
      list = list.filter(n => {
        const title = (n.title || '').toLowerCase()
        const content = n.content?.toLowerCase() || ''
        const todoTexts = (n.todos || []).map(t => t.text.toLowerCase()).join(' ')
        const tagTexts = (n.tags || []).join(' ').toLowerCase()
        return keywords.every(k =>
          title.includes(k) || content.includes(k) || todoTexts.includes(k) || tagTexts.includes(k)
        )
      })
    }

    if (tagFilter) {
      list = list.filter(n => (n.tags || []).includes(tagFilter))
    }

    return list
  }, [floatingNotes, noteSearchQuery, showArchived, tagFilter, activeFolderId, folders])

  // Split: floated notes → portal above panel, pinned → portal on body, rest in-place
  const nonFloated = useMemo(() => filteredNotes.filter(n => !n.floated && !n.pinned), [filteredNotes])
  const floated = useMemo(() => filteredNotes.filter(n => n.floated && !n.pinned), [filteredNotes])
  const pinned = useMemo(() => filteredNotes.filter(n => n.pinned), [filteredNotes])

  const hasNotes = floatingNotes.length > 0
  const showEmpty = hasNotes && filteredNotes.length === 0 && (noteSearchQuery.trim().length > 0 || tagFilter !== null)

  const renderNote = useCallback((n: typeof filteredNotes[0]) => (
    <FloatingNoteComp
      key={n.id}
      note={n}
      matched={noteSearchQuery.trim().length > 0}
      searchKeyword={
        searchHighlight && searchHighlight.noteId === n.id
          ? searchHighlight.keyword
          : undefined
      }
    />
  ), [noteSearchQuery, searchHighlight])

  // In notes mode, don't render any floating sticky notes
  if (panelMode === 'notes') return null

  return (
    <>
    <div className="absolute inset-0 z-10">
      {/* List view */}
      {notesViewMode === 'list' ? (
        <NotesListView />
      ) : (
        <>
        {/* Search bar - only visible when there are notes */}
        {hasNotes && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 flex flex-col items-center gap-1.5">
          <div className="w-[280px] max-w-[70vw] flex items-center gap-2 bg-white/[0.04] rounded-xl px-3 py-2 border border-white/[0.04] focus-within:border-white/[0.1] transition-colors backdrop-blur-lg">
            <span className="text-text-secondary text-xs"><Icon icon="fa-solid fa-magnifying-glass" /></span>
            <input
              value={noteSearchQuery}
              onChange={e => setNoteSearchQuery(e.target.value)}
              placeholder={t('search.placeholder')}
              className="flex-1 bg-transparent text-xs text-text-primary placeholder:text-text-secondary outline-none"
            />
            {noteSearchQuery && (
              <button onClick={() => setNoteSearchQuery('')}
                className="text-xs text-text-secondary hover:text-text-secondary transition-colors">
                <Icon icon="fa-solid fa-xmark" />
              </button>
            )}
          </div>

          {/* Controls row: view toggle + archive toggle + tag filter */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => useStore.getState().setNotesViewMode('list')}
              className={'px-2.5 py-1 rounded-lg text-2xs transition-all border bg-white/[0.04] border-white/[0.04] text-text-secondary hover:text-text-secondary'}
            >
              <Icon icon="fa-solid fa-list mr-1" />
              {t('folder.allNotes')}
            </button>

            <button
              onClick={() => setShowArchived(!showArchived)}
              className={`px-2.5 py-1 rounded-lg text-2xs transition-all border ${showArchived ? 'bg-amber-400/10 border-amber-400/20 text-amber-400/70' : 'bg-white/[0.04] border-white/[0.04] text-text-secondary hover:text-text-secondary'}`}
            >
              <Icon icon="fa-solid fa-box-archive mr-1" />
              {showArchived ? t('search.viewNotes') : t('search.viewArchived')}
            </button>

            {allTags.length > 0 && !showArchived && (
              <select
                value={tagFilter || ''}
                onChange={e => setTagFilter(e.target.value || null)}
                className="bg-white/[0.04] border border-white/[0.04] rounded-lg text-2xs px-2 py-1 text-text-secondary outline-none cursor-pointer"
                style={{ color: 'var(--text-secondary)' }}
              >
                <option value="">{t('search.allTags')}</option>
                {allTags.map(tag => (
                  <option key={tag} value={tag}>{tag}</option>
                ))}
              </select>
            )}

            {tagFilter && (
              <button onClick={() => setTagFilter(null)}
                className="text-[8px] px-1.5 py-0.5 rounded bg-white/[0.04] text-text-muted hover:text-text-secondary transition-colors">
                <Icon icon="fa-solid fa-xmark" />
              </button>
            )}
          </div>
        </div>
      )}

      {/* Empty state */}
      {showEmpty && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
          <div className="flex flex-col items-center text-text-dim">
            <Icon icon="fa-solid fa-magnifying-glass text-lg mb-2" />
            <span className="text-xs">{t('search.noMatch')}</span>
          </div>
        </div>
      )}

      {/* Empty archived state */}
      {showArchived && !showEmpty && filteredNotes.length === 0 && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-10">
          <div className="flex flex-col items-center text-text-dim">
            <Icon icon="fa-solid fa-box-archive text-lg mb-2" />
            <span className="text-xs">{t('search.noArchived')}</span>
          </div>
        </div>
      )}

      {/* Canvas with pan/zoom */}
      <CanvasMode notes={nonFloated} renderNote={renderNote} />
      </>
      )}
    </div>

    {/* Floated notes portal — above the panel */}
    {floated.length > 0 && createPortal(
      <div style={{ position: 'fixed', inset: 0, zIndex: 100000, pointerEvents: 'none' }}>
        <AnimatePresence>
          {floated.map(n => (
            <React.Fragment key={n.id}>
              {renderNote(n)}
            </React.Fragment>
          ))}
        </AnimatePresence>
      </div>,
      document.body
    )}

    {/* Pinned notes portal — rendered at <body> end for highest stacking context */}
    {pinned.length > 0 && createPortal(
      <AnimatePresence>
        {pinned.map(renderNote)}
      </AnimatePresence>,
      document.body
    )}
    </>
  )
}

export default NotesLayer
