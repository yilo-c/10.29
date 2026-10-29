import React, { useState, useRef, useEffect } from 'react'
import Icon from './Icon'

import { AnimatePresence } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { uid } from '../../utils/helpers'
import { exportToMarkdown, exportToJSON, downloadAsFile } from '../../utils/export'
import { downloadBackup, getBackupMeta } from '../../utils/backup'
import { getTemplateById } from '../../utils/templates'
import TemplatePicker from './TemplatePicker'
import TemplateManagerPanel from './TemplateManagerPanel'
import ImportModal from './ImportModal'
import AISettingsPanel from './AISettingsPanel'
import UsageDocPanel from './UsageDocPanel'
import HistoryTimelinePanel from './HistoryTimelinePanel'
import { useTranslation } from '../../i18n'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const Toolbar: React.FC = () => {
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const nextZ = useStore(s => s.nextZ)
  const setAddingTodo = useStore(s => s.setAddingTodo)
  const todos = useStore(s => s.todos)
  const floatingNotes = useStore(s => s.floatingNotes)
  const categories = useStore(s => s.categories)
  const defaultTemplateId = useStore(s => s.defaultTemplateId)
  const activeFolderId = useStore(s => s.activeFolderId)
  const panelMode = useStore(s => s.panelMode)
  const { t } = useTranslation()
  const [showMore, setShowMore] = useState(false)
  const [showExport, setShowExport] = useState(false)
  const [showTemplates, setShowTemplates] = useState(false)
  const [showTemplateManager, setShowTemplateManager] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [showAISettings, setShowAISettings] = useState(false)
  const [showUsageDoc, setShowUsageDoc] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const moreRef = useRef<HTMLDivElement>(null)
  const exportRef = useRef<HTMLDivElement>(null)
  const templateRef = useRef<HTMLDivElement>(null)

  // Close dropdowns on outside click
  useEffect(() => {
    if (!showExport && !showTemplates && !showMore) return
    const handler = (e: MouseEvent) => {
      if (showExport && exportRef.current && !exportRef.current.contains(e.target as Node)) {
        setShowExport(false)
      }
      if (showTemplates && templateRef.current && !templateRef.current.contains(e.target as Node)) {
        setShowTemplates(false)
      }
      if (showMore && moreRef.current && !moreRef.current.contains(e.target as Node)) {
        setShowMore(false)
      }
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      setShowExport(false)
      setShowTemplates(false)
      setShowMore(false)
    }
    document.addEventListener('mousedown', handler)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('keydown', onKey)
    }
  }, [showExport, showTemplates, showMore])

  const createTextNote = (folderId?: string | null) => {
    const id = uid()
    let title = t('note.newNote')
    let content = ''
    // Auto-apply default template if set
    const defaultTmpl = defaultTemplateId ? getTemplateById(defaultTemplateId) : null
    if (defaultTmpl) {
      title = defaultTmpl.title
      content = defaultTmpl.content
    }
    const targetFolderId = folderId !== undefined ? folderId : (activeFolderId ?? undefined)
    addFloatingNote({
      id, type: 'text', title, content,
      folderId: targetFolderId || undefined,
      x: 100 + Math.random() * 80, y: 100 + Math.random() * 80,
      width: 260, height: 200, zIndex: nextZ, floated: true,
    })
    if (isElectron) {
      const note = useStore.getState().floatingNotes.find(n => n.id === id)
      ei.createFloatingWindow({ id, screenX: 100 + Math.random() * 80, screenY: 100 + Math.random() * 80, width: 360, height: 300, noteData: note })
    }
  }

  const createTodoNote = () => {
    setAddingTodo(true)
  }

  const doExportMarkdown = () => {
    const data = { todos, categories, floatingNotes }
    const md = exportToMarkdown(data)
    const ts = new Date().toISOString().slice(0, 10)
    downloadAsFile(md, `${t('app.title')}-${ts}.md`, 'text/markdown;charset=utf-8')
    setShowExport(false)
  }

  const doExportJSON = () => {
    const data = { todos, categories, floatingNotes }
    const json = exportToJSON(data)
    const ts = new Date().toISOString().slice(0, 10)
    downloadAsFile(json, `${t('app.title')}-${ts}.json`, 'application/json;charset=utf-8')
    setShowExport(false)
  }

  const doDownloadBackup = () => {
    downloadBackup()
    setShowExport(false)
  }

  const handleCheckUpdate = () => {
    if (ei?.update?.check) ei.update.check()
  }

  const backupMeta = getBackupMeta()
  const backupTimeStr = backupMeta
    ? new Date(backupMeta.timestamp).toLocaleTimeString(
        useStore.getState().locale === 'en' ? 'en-US' : 'zh-CN',
        { hour: '2-digit', minute: '2-digit' }
      )
    : null

  return (
    <div className="flex items-center justify-center gap-2 px-3 py-2.5 border-t border-white/[0.04] bg-white/[0.02]">
      {panelMode === 'notes' ? (
        /* Notes mode: New Note + Import + Export dropdown + More */
        <>
          {/* New note (simple ToolBtn) */}
          <ToolBtn icon="fa-note-sticky" label={t('toolbar.newNote')} onClick={() => createTextNote()} />

          {/* Import (direct) */}
          <ToolBtn icon="fa-file-arrow-down" label={t('toolbar.import')} onClick={() => setShowImport(true)} />

          {/* Export (dropdown) — like todo mode */}
          <div className="relative" ref={exportRef}>
            <button
              onClick={() => setShowExport(!showExport)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-text-secondary hover:text-text-secondary hover:bg-white/[0.05] transition-all text-xs"
            >
              <Icon icon="fa-solid fa-download" />
              <span>{t('toolbar.export')}</span>
            </button>
            {showExport && (
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50 border border-white/[0.08] rounded-xl py-1 shadow-2xl min-w-[150px] backdrop-blur-xl"
                style={{ background: 'var(--panel-bg-solid)' }}>
                <DropItem icon="fa-brands fa-markdown" label={t('toolbar.exportMarkdown')} onClick={() => { doExportMarkdown(); setShowExport(false) }} />
                <DropItem icon="fa-solid fa-code" label={t('toolbar.exportJSON')} onClick={() => { doExportJSON(); setShowExport(false) }} />
              </div>
            )}
          </div>
        </>
      ) : (
        /* Todo mode: New Todo + Template + Import + Export dropdown + More */
        <>
          <ToolBtn icon="fa-list-check" label={t('toolbar.newTodo')} onClick={createTodoNote} />

          {/* Template (direct) */}
          <div className="relative" ref={templateRef}>
            <ToolBtn icon="fa-regular fa-copy" label={t('toolbar.template')} onClick={() => { setShowTemplates(true) }} />
            {showTemplates && <TemplatePicker onClose={() => setShowTemplates(false)} onManage={() => setShowTemplateManager(true)} />}
          </div>

          {/* Import (direct) */}
          <ToolBtn icon="fa-file-arrow-down" label={t('toolbar.import')} onClick={() => setShowImport(true)} />

          {/* Export (dropdown) */}
          <div className="relative" ref={exportRef}>
            <button
              onClick={() => setShowExport(!showExport)}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-text-secondary hover:text-text-secondary hover:bg-white/[0.05] transition-all text-xs"
            >
              <Icon icon="fa-solid fa-download" />
              <span>{t('toolbar.export')}</span>
            </button>
            {showExport && (
              <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50 border border-white/[0.08] rounded-xl py-1 shadow-2xl min-w-[150px] backdrop-blur-xl"
                style={{ background: 'var(--panel-bg-solid)' }}>
                <DropItem icon="fa-brands fa-markdown" label={t('toolbar.exportMarkdown')} onClick={() => { doExportMarkdown(); setShowExport(false) }} />
                <DropItem icon="fa-solid fa-code" label={t('toolbar.exportJSON')} onClick={() => { doExportJSON(); setShowExport(false) }} />
              </div>
            )}
          </div>
        </>
      )}

      {/* More menu — AI Settings, Update Check, Backup */}
      <div className="relative" ref={moreRef}>
        <button
          onClick={() => setShowMore(!showMore)}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-text-secondary hover:text-text-secondary hover:bg-white/[0.05] transition-all text-xs"
        >
          <Icon icon="fa-solid fa-ellipsis" />
          <span>{t('toolbar.more')}</span>
        </button>
        {showMore && (
          <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50 border border-white/[0.08] rounded-xl py-1 shadow-2xl min-w-[150px] backdrop-blur-xl"
            style={{ background: 'var(--panel-bg-solid)' }}>
            <DropItem icon="fa-solid fa-robot" label={t('ai.settings.title')} onClick={() => { setShowAISettings(true); setShowMore(false) }} />
            {isElectron && (
              <DropItem icon="fa-solid fa-cloud-arrow-down" label={t('update.checkForUpdates')} onClick={() => { handleCheckUpdate(); setShowMore(false) }} />
            )}
            <div className="h-px bg-white/[0.06] mx-2 my-1" />
            <DropItem icon="fa-solid fa-clock-rotate-left" label={t('history.title')} onClick={() => { setShowHistory(true); setShowMore(false) }} />
            <div className="h-px bg-white/[0.06] mx-2 my-1" />
            <DropItem icon="fa-solid fa-book" label={t('toolbar.usageDoc')} onClick={() => { setShowUsageDoc(true); setShowMore(false) }} />
            <div className="h-px bg-white/[0.06] mx-2 my-1" />
            <DropItem icon="fa-solid fa-database" label={t('toolbar.downloadBackup')} onClick={() => { doDownloadBackup(); setShowMore(false) }} />
          </div>
        )}
      </div>

      {/* Backup time indicator */}
      {backupTimeStr && (
        <span className="text-2xs text-text-dim absolute right-3 bottom-1">
          {t('toolbar.backupTime', { time: backupTimeStr })}
        </span>
      )}

      {/* Modals */}
      <AnimatePresence>
        {showImport && <ImportModal onClose={() => setShowImport(false)} />}
      </AnimatePresence>
      <AnimatePresence>
        {showAISettings && <AISettingsPanel onClose={() => setShowAISettings(false)} />}
      </AnimatePresence>
      <AnimatePresence>
        {showUsageDoc && <UsageDocPanel onClose={() => setShowUsageDoc(false)} />}
      </AnimatePresence>
      <AnimatePresence>
        {showHistory && <HistoryTimelinePanel onClose={() => setShowHistory(false)} />}
      </AnimatePresence>
      {showTemplateManager && <TemplateManagerPanel onClose={() => setShowTemplateManager(false)} />}
    </div>
  )
}

const ToolBtn: React.FC<{ icon: string; label: string; onClick?: () => void }> = ({ icon, label, onClick }) => (
  <button
    onClick={onClick}
    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-text-secondary hover:text-text-secondary hover:bg-white/[0.05] transition-all text-xs"
  >
    <Icon icon={`fa-solid ${icon}`} />
    <span>{label}</span>
  </button>
)

const DropItem: React.FC<{ icon: string; label: string; onClick: () => void }> = ({ icon, label, onClick }) => (
  <button
    onClick={onClick}
    className="w-full flex items-center gap-2.5 px-3 py-1.5 text-xs text-text-primary hover:bg-white/[0.06] hover:text-text-primary transition-colors text-left"
  >
    <Icon icon={`${icon.startsWith('fa-') ? icon : `fa-solid ${icon}`} w-3.5 text-center text-xs`} />
    {label}
  </button>
)

export default Toolbar
