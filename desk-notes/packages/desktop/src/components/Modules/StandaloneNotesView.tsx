import React, { useEffect, useState } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { uid, hexToRgb } from '../../utils/helpers'
import { initFileStore } from '../../utils/fileStore'
import { useTranslation } from '../../i18n'
import HeaderBar from '../MainWindow/HeaderBar'
import FolderTree from '../Sidebar/FolderTree'
import NotesBrowser from '../Common/NotesBrowser'
import NoteEditor from '../Common/NoteEditor'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const StandaloneNotesView: React.FC = () => {
  const editingNoteId = useStore(s => s.editingNoteId)
  const panel = useStore(s => s.panel)
  const backgroundMode = useStore(s => s.backgroundMode)
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const nextZ = useStore(s => s.nextZ)
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [initError, setInitError] = useState(false)

  const doInit = () => {
    let cancelled = false
    setLoading(true)
    setInitError(false)
    ;(async () => {
      try {
        await initFileStore()
        await useStore.getState().loadNotesFromDisk()
        if (cancelled) return
        setLoading(false)
      } catch (e: unknown) {
        console.error('[StandaloneNotesView] init failed:', e)
        if (!cancelled) { setLoading(false); setInitError(true) }
      }
    })()
    return () => { cancelled = true }
  }

  useEffect(() => {
    const cleanup = doInit()
    return cleanup
  }, [])

  const createTextNote = () => {
    const id = uid()
    addFloatingNote({
      id, type: 'text', title: t('note.newNote'), content: '',
      x: 100 + Math.random() * 80, y: 100 + Math.random() * 80,
      width: 260, height: 200, zIndex: nextZ, floated: true,
    })
    if (isElectron) {
      const note = useStore.getState().floatingNotes.find(n => n.id === id)
      if (note) ei!.createFloatingWindow({ id, screenX: 100 + Math.random() * 80, screenY: 100 + Math.random() * 80, width: 360, height: 300, noteData: note })
    }
  }

  // ── Background calculation (matches MainWindow) ──
  let bgColor: string
  let borderVal: string
  let boxShadowVal: string | undefined

  if (backgroundMode === 'pure-white') {
    const rgb = panel.color ? hexToRgb(panel.color) : null
    if (rgb) {
      bgColor = `rgba(${Math.round(250 + (rgb.r - 250) * 0.3)},${Math.round(252 + (rgb.g - 252) * 0.3)},${Math.round(252 + (rgb.b - 252) * 0.3)},0.95)`
    } else { bgColor = 'rgba(250,250,252,0.95)' }
    borderVal = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(0,0,0,0.06)'
    boxShadowVal = panel.color
      ? `0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04), 0 0 30px ${panel.color}12`
      : `0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04)`
  } else if (backgroundMode === 'pure-black') {
    const rgb = panel.color ? hexToRgb(panel.color) : null
    if (rgb) {
      bgColor = `rgba(${Math.round(14 + (rgb.r - 14) * 0.15)},${Math.round(14 + (rgb.g - 14) * 0.15)},${Math.round(20 + (rgb.b - 20) * 0.15)},0.98)`
    } else { bgColor = 'rgba(14,14,20,0.98)' }
    borderVal = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(255,255,255,0.04)'
    boxShadowVal = panel.color
      ? `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03), 0 0 30px ${panel.color}12`
      : `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03)`
  } else {
    const baseAlpha = isElectron ? 0.65 : 0.72
    const bgAlpha = baseAlpha * panel.opacity
    const rgb = panel.color ? hexToRgb(panel.color) : null
    if (rgb) {
      const tint = 0.30 * panel.opacity
      bgColor = `rgba(${Math.round(22 + (rgb.r - 22) * tint)},${Math.round(22 + (rgb.g - 22) * tint)},${Math.round(32 + (rgb.b - 32) * tint)},${bgAlpha.toFixed(2)})`
    } else { bgColor = `rgba(22,22,32,${bgAlpha.toFixed(2)})` }
    borderVal = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(255,255,255,0.05)'
    boxShadowVal = panel.color ? `0 8px 40px rgba(0,0,0,0.35), 0 0 30px ${panel.color}12` : undefined
  }

  if (loading || initError) {
    return (
      <div className="w-screen h-screen flex flex-col items-center justify-center gap-3 select-none"
        style={{ background: '#0c0c10' }}>
        {loading ? (
          <span className="text-xs text-white/48">{t('common.loading')}</span>
        ) : (
          <>
            <span className="text-xs text-red-400/70">{t('common.initFailed')}</span>
            <button
              onClick={() => doInit()}
              className="text-xs px-3 py-1.5 rounded-lg bg-white/[0.06] text-text-secondary hover:bg-white/[0.12] transition-colors"
            >
              <Icon icon="fa-solid fa-rotate-right mr-1.5" />
              {t('common.retry')}
            </button>
          </>
        )}
      </div>
    )
  }

  return (
    <div
      className="fixed inset-0 overflow-hidden flex flex-col"
      style={{
        background: bgColor,
        backdropFilter: isElectron ? 'none' : 'blur(20px) saturate(1.3)',
        border: borderVal,
        boxShadow: boxShadowVal,
      }}
    >
      <HeaderBar />
      <div className="flex items-center justify-between px-4 py-1.5 border-b border-white/[0.04] flex-shrink-0">
        <h2 className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
          <Icon icon="fa-regular fa-note-sticky mr-2 text-xs text-text-muted" />
          {t('nav.notes')}
        </h2>
        <button
          onClick={createTextNote}
          className="text-xs text-text-dim hover:text-text-secondary transition-colors flex items-center gap-1"
          title={t('toolbar.newNote')}
        >
          <Icon icon="fa-solid fa-plus" />
          <span className="text-xs">{t('toolbar.newNote')}</span>
        </button>
      </div>
      <div className="flex flex-col flex-1 min-h-0 overflow-hidden">
        {!editingNoteId && (
          <div className="overflow-y-auto flex-shrink-0 max-h-[40%]">
            <FolderTree />
          </div>
        )}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
          {editingNoteId ? <NoteEditor /> : <NotesBrowser />}
        </div>
      </div>
    </div>
  )
}

export default StandaloneNotesView
