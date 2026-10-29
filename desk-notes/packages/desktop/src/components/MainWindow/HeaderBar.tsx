import React, { useState, useRef, useEffect } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const HeaderBar: React.FC = () => {
  const { t } = useTranslation()
  const panel = useStore(s => s.panel)
  const togglePanelPin = useStore(s => s.togglePanelPin)
  const togglePanelLock = useStore(s => s.togglePanelLock)
  const appPin = useStore(s => s.appPin)
  const lockApp = useStore(s => s.lockApp)
  const toggleNavCollapsed = useStore(s => s.toggleNavCollapsed)
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({ startX: 0, startY: 0, startPX: 0, startPY: 0 })
  const dragCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    return () => { dragCleanupRef.current?.() }
  }, [])

  const handleDragStart = (e: React.MouseEvent) => {
    if (isElectron) return
    e.preventDefault()
    const p = useStore.getState().panel
    dragRef.current = { startX: e.clientX, startY: e.clientY, startPX: p.x, startPY: p.y }
    setDragging(true)
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - dragRef.current.startX
      const dy = ev.clientY - dragRef.current.startY
      useStore.getState().setPanelPos(
        Math.max(0, dragRef.current.startPX + dx),
        Math.max(0, dragRef.current.startPY + dy),
      )
    }
    const onUp = () => {
      setDragging(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      dragCleanupRef.current = null
    }
    if (dragCleanupRef.current) dragCleanupRef.current()
    dragCleanupRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  const nd = isElectron ? ({ WebkitAppRegion: 'no-drag' } as React.CSSProperties) : undefined

  return (
    <div
      className={`flex items-center justify-between px-4 h-11 flex-shrink-0 border-b border-border select-none ${dragging ? 'cursor-grabbing' : !isElectron ? 'cursor-grab' : ''}`}
      style={isElectron ? { WebkitAppRegion: 'drag' } as React.CSSProperties : undefined}
      onMouseDown={!isElectron ? handleDragStart : undefined}
    >
      {/* Left: nav toggle + module title */}
      <div className="flex items-center gap-1.5" style={nd}>
        <button onClick={toggleNavCollapsed}
          className="w-7 h-7 flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-all text-xs"
          title={t('nav.expand')}
        >
          <Icon icon="fa-solid fa-bars" />
        </button>
      </div>

      {/* Right: window controls */}
      <div className="flex items-center gap-0.5" style={nd}>
        {/* Global Search */}
        <button onClick={() => useStore.getState().setSearchModalOpen(true)}
          className="w-7 h-7 flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-all text-xs"
          title={t('search.placeholder')}
        >
          <Icon icon="fa-solid fa-magnifying-glass" />
        </button>

        {/* Pin */}
        <button onClick={() => {
          const next = !panel.pinned
          togglePanelPin()
          if (isElectron) ei.alwaysOnTop(next)
        }}
          className={`w-7 h-7 flex items-center justify-center rounded-md transition-all text-xs ${panel.pinned ? 'text-brand' : 'text-text-secondary hover:text-text-primary hover:bg-hover-bg'}`}
          title={panel.pinned ? t('note.unpin') : t('note.pin')}
        >
          <Icon icon="fa-solid fa-thumbtack" />
        </button>

        {/* Lock */}
        <button onClick={() => appPin ? lockApp() : togglePanelLock()}
          className={`w-7 h-7 flex items-center justify-center rounded-md transition-all text-xs ${panel.locked ? 'text-warning' : 'text-text-secondary hover:text-text-primary hover:bg-hover-bg'}`}
          title={t('lock.title')}
        >
          <Icon icon="fa-solid fa-lock" />
        </button>

        {/* Minimize / Close */}
          <button style={nd} onClick={() => isElectron ? ei.minimize() : useStore.getState().setPanelVisible(false)}
            className="w-7 h-7 flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-all text-xs">
            <Icon icon="fa-regular fa-window-minimize" />
          </button>
          <button style={nd} onClick={() => isElectron ? ei.close() : useStore.getState().setPanelVisible(false)}
            className="w-7 h-7 flex items-center justify-center rounded-md text-text-muted hover:text-danger hover:bg-hover-bg transition-all text-xs">
            <Icon icon="fa-solid fa-xmark" />
          </button>
      </div>
    </div>
  )
}

export default HeaderBar
