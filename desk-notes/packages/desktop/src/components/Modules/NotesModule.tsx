import React, { useState, useCallback, useEffect } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import { getWsClient } from '../../utils/ai'
import { runTrackedJob } from '@desk-notes/shared'
import { useStore } from '../../store/useStore'
import NotesBrowser from '../Common/NotesBrowser'
import NoteEditor from '../Common/NoteEditor'
import FolderTree from '../Sidebar/FolderTree'
import TrashView from '../Common/TrashView'
import DailyNoteView from '../Common/DailyNoteView'
import PageHeader from '../Common/PageHeader'

const ei = window.electronAPI as ElectronAPI | undefined

const NotesModule: React.FC = () => {
  const { t } = useTranslation()
  const [showUrlInput, setShowUrlInput] = useState(false)
  const [urlInput, setUrlInput] = useState('')
  const [urlLoading, setUrlLoading] = useState(false)
  const [urlError, setUrlError] = useState('')
  const [showTrash, setShowTrash] = useState(false)
  const [showDaily, setShowDaily] = useState(false)
  const [trashKey, setTrashKey] = useState(0)
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const editingNoteId = useStore(s => s.editingNoteId)
  const setCurrentContext = useStore(s => s.setCurrentContext)
  const floatingNotes = useStore(s => s.floatingNotes)
  const notesCount = floatingNotes.filter(n => !n.archived).length

  useEffect(() => {
    if (editingNoteId) {
      setCurrentContext('notes', editingNoteId, null)
    }
  }, [editingNoteId, setCurrentContext])

  const handleUrlLearn = useCallback(async () => {
    const url = urlInput.trim()
    if (!url || urlLoading) return
    setUrlLoading(true)
    try {
      const ws = getWsClient()
      if (ws.status !== 'connected') return
      // B1：长时操作入 backgroundJobs 通用层，切页不丢进度
      const result = await runTrackedJob({
        type: 'url_learn',
        label: url,
        run: () => ws.call<{
          title: string; summary: string; key_points: string[]; learning_suggestions: string[]
        }>('url_learning.learn', { url, timeout: 30 }),
      })
      const content = [
        result.summary ? `## ${t('notesModule.mdSummary')}\n\n${result.summary}` : '',
        result.key_points?.length ? `## ${t('notesModule.mdKeyPoints')}\n\n${result.key_points.map((p: string) => `- ${p}`).join('\n')}` : '',
        result.learning_suggestions?.length ? `## ${t('notesModule.mdLearningSuggestions')}\n\n${result.learning_suggestions.map((s: string) => `- ${s}`).join('\n')}` : '',
      ].filter(Boolean).join('\n\n')
      addFloatingNote({
        id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
        title: result.title || url,
        content,
        type: 'text',
        x: 0, y: 0, zIndex: 1,
        width: 320, height: 400,
        createdAt: Date.now(),
        updatedAt: Date.now(),
        tags: ['url-learning'],
      })
      setShowUrlInput(false)
      setUrlInput('')
      setUrlError('')
    } catch {
      setUrlError(t('notesModule.learnFailed'))
    } finally {
      setUrlLoading(false)
    }
  }, [urlInput, urlLoading, addFloatingNote])

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      <div className="flex-shrink-0 px-5 pt-5 pb-3">
        <PageHeader
          title={t('nav.notes')}
          subtitle={t('notesModule.subtitleFmt', { count: notesCount })}
          actions={(
            <>
              <button onClick={() => setShowDaily(true)}
                className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
                style={{ background: 'rgba(251,191,36,0.12)', color: '#fbbf24' }}>
                <Icon icon="fa-solid fa-pen mr-1" />{t('notesModule.dailyRecord')}
              </button>
              <button
                onClick={() => setShowUrlInput(!showUrlInput)}
                className="w-8 h-8 flex items-center justify-center rounded-lg text-sm text-text-dim hover:text-text-secondary hover:bg-hover-bg transition-all"
                title={t('notesModule.createFromLink')}
              >
                <Icon icon="fa-solid fa-link" />
              </button>
              <button
                onClick={() => { setShowTrash(true); setTrashKey(k => k + 1) }}
                className="w-8 h-8 flex items-center justify-center rounded-lg text-sm text-text-dim hover:text-text-secondary hover:bg-hover-bg transition-all"
                title={t('notesModule.trash')}
              >
                <Icon icon="fa-regular fa-trash-can" />
              </button>
              {ei && (
                <button
                  onClick={() => ei.createViewWindow({ viewType: 'notes' })}
                  className="w-8 h-8 flex items-center justify-center rounded-lg text-sm text-text-dim hover:text-text-secondary hover:bg-hover-bg transition-all"
                  title={t('notesModule.independentWindow')}
                >
                  <Icon icon="fa-solid fa-external-link-alt" />
                </button>
              )}
            </>
          )}
        />
      </div>
      {showUrlInput && (
        <div className="flex-shrink-0 px-4 py-2 border-b border-border">
          <div className="flex items-center gap-2">
            <input
              value={urlInput}
              onChange={e => setUrlInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleUrlLearn() }}
              placeholder={t('notesModule.urlInputPlaceholder')}
              className="flex-1 px-2.5 py-1.5 rounded-lg border text-xs outline-none"
              style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
            />
            <button onClick={handleUrlLearn} disabled={!urlInput.trim() || urlLoading}
              className="flex-shrink-0 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-30 disabled:cursor-not-allowed">
              <Icon icon={`fa-solid ${urlLoading ? 'fa-spinner animate-spin' : 'fa-arrow-right'}`} />
            </button>
          </div>
          {urlError && (
            <div className="flex items-center gap-1.5 mt-1.5 px-2 py-1 rounded-lg text-2xs" style={{ background: 'rgba(239,68,68,0.1)', color: '#f87171' }}>
              <Icon icon="fa-solid fa-circle-exclamation text-[8px]" />
              {urlError}
            </div>
          )}
        </div>
      )}
      {showDaily ? (
        <DailyNoteView onClose={() => setShowDaily(false)} />
      ) : showTrash ? (
        <div className="flex-1 min-h-0">
          <TrashView
            key={trashKey}
            onClose={() => setShowTrash(false)}
            onRestore={() => setTrashKey(k => k + 1)}
          />
        </div>
      ) : (
        <>
          <div className="overflow-y-auto flex-shrink-0 max-h-[40%]">
            <FolderTree />
          </div>
          {editingNoteId ? <NoteEditor /> : <NotesBrowser />}
        </>
      )}
    </div>
  )
}

export default NotesModule
