import Icon from '../Common/Icon'
import React, { useRef, useState, useEffect, Suspense } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useStore } from '../../store/useStore'
import CategoryPills from '../Sidebar/CategoryPills'
import TodoList from '../Todo/TodoList'
import Toolbar from '../Common/Toolbar'
import FolderTree from '../Sidebar/FolderTree'
import { COLOR_PRESETS, hexToRgb } from '../../utils/helpers'
import TrashPanel from '../Common/TrashPanel'
import CustomThemeEditor from '../Common/CustomThemeEditor'
import NotesBrowser from '../Common/NotesBrowser'
import NoteEditor from '../Common/NoteEditor'
import MorningBrief from '../Todo/MorningBrief'
import { useTranslation } from '../../i18n'

const CalendarViewLazy = React.lazy(() => import('../Todo/CalendarView'))

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const nd = isElectron ? ({ WebkitAppRegion: 'no-drag' } as unknown as React.CSSProperties) : undefined

const AcrylicPanel: React.FC = () => {
  const { t } = useTranslation()
  const panel = useStore(s => s.panel)
  const setPanelPos = useStore(s => s.setPanelPos)
  const togglePanelPin = useStore(s => s.togglePanelPin)
  const togglePanelLock = useStore(s => s.togglePanelLock)
  const setPanelOpacity = useStore(s => s.setPanelOpacity)
  const setPanelColor = useStore(s => s.setPanelColor)
  const theme = useStore(s => s.theme)
  const toggleTheme = useStore(s => s.toggleTheme)
  const viewMode = useStore(s => s.viewMode)
  const setViewMode = useStore(s => s.setViewMode)
  const backgroundMode = useStore(s => s.backgroundMode)
  const setBackgroundMode = useStore(s => s.setBackgroundMode)
  const setLocale = useStore(s => s.setLocale)
  const locale = useStore(s => s.locale)
  const appPin = useStore(s => s.appPin)
  const appLocked = useStore(s => s.appLocked)
  const lockApp = useStore(s => s.lockApp)
  const panelMode = useStore(s => s.panelMode)
  const setPanelMode = useStore(s => s.setPanelMode)
  const editingNoteId = useStore(s => s.editingNoteId)
  const [dragging, setDragging] = useState(false)
  const [resizing, setResizing] = useState(false)
  const [showPanelOpacity, setShowPanelOpacity] = useState(false)
  const [showTrash, setShowTrash] = useState(false)
  const dragRef = useRef({ startX: 0, startY: 0, origX: 0, origY: 0 })
  const resizeRef = useRef({ startX: 0, startW: 0 })
  const dragCleanupRef = useRef<(() => void) | null>(null)
  const resizeCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    return () => {
      dragCleanupRef.current?.()
      resizeCleanupRef.current?.()
    }
  }, [])

  const handleMouseDown = (e: React.MouseEvent) => {
    if (isElectron || panel.locked) return
    dragRef.current = { startX: e.clientX, startY: e.clientY, origX: panel.x, origY: panel.y }
    setDragging(true)

    const onMove = (ev: MouseEvent) => {
      const nx = dragRef.current.origX + ev.clientX - dragRef.current.startX
      const ny = Math.max(0, dragRef.current.origY + ev.clientY - dragRef.current.startY)
      setPanelPos(nx, ny)
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

  // Window width resize handle (Electron only)
  const handleResizeStart = (e: React.MouseEvent) => {
    if (!isElectron || panel.locked) return
    e.preventDefault()
    e.stopPropagation()
    resizeRef.current = { startX: e.clientX, startW: window.innerWidth }

    const onMove = (ev: MouseEvent) => {
      const dw = ev.clientX - resizeRef.current.startX
      const newW = Math.max(280, resizeRef.current.startW + dw)
      ei.resizeWindow(newW, window.innerHeight)
    }
    const onUp = () => {
      setResizing(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      resizeCleanupRef.current = null
    }
    if (resizeCleanupRef.current) resizeCleanupRef.current()
    resizeCleanupRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    setResizing(true)
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // Resize Electron window / adjust panel pos when entering/leaving kanban/calendar mode
  useEffect(() => {
    if (isElectron) {
      const w = viewMode === 'calendar' ? 700 : 450
      const h = window.innerHeight
      ei.resizeWindow(w, h)
    } else {
      // Browser mode: ensure panel doesn't overflow right edge
      const { x: px, y: py } = useStore.getState().panel
      const newWidth = viewMode === 'calendar' ? 700 : 340
      if (px + newWidth > window.innerWidth) {
        setPanelPos(Math.max(0, window.innerWidth - newWidth - 16), py)
      }
    }
  }, [viewMode])

  // Hide/show Electron floating windows when switching panel mode
  useEffect(() => {
    if (!isElectron) return
    if (panelMode === 'notes') {
      ei?.hideFloatingWindows()
    } else {
      ei?.showFloatingWindows()
    }
  }, [panelMode])

  // Lock: disable OS window resize when locked (Electron only)
  useEffect(() => {
    if (!isElectron) return
    ei.setResizable(!panel.locked)
  }, [panel.locked])

  // Pin: sync Electron window always-on-top state on mount/rehydration
  useEffect(() => {
    if (!isElectron) return
    ei.alwaysOnTop(panel.pinned)
  }, [panel.pinned])

  const panelZ = 99999 // fixed high, never buried by notes

  // ── Background color per mode ─────────────────────────────────────
  let bgColor: string
  let backdropFilter: string | undefined
  let borderColor: string
  let boxShadowVal: string | undefined

  if (backgroundMode === 'pure-white') {
    if (panel.color) {
      const rgb = hexToRgb(panel.color)
      if (rgb) {
        const r = Math.round(250 + (rgb.r - 250) * 0.3)
        const g = Math.round(252 + (rgb.g - 252) * 0.3)
        const b = Math.round(252 + (rgb.b - 252) * 0.3)
        bgColor = `rgba(${r},${g},${b},0.95)`
      } else {
        bgColor = 'rgba(250,250,252,0.95)'
      }
    } else {
      bgColor = 'rgba(250,250,252,0.95)'
    }
    backdropFilter = 'none'
    borderColor = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(0,0,0,0.06)'
    boxShadowVal = panel.color
      ? `0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04), 0 0 30px ${panel.color}12`
      : `0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04)`
  } else if (backgroundMode === 'pure-black') {
    if (panel.color) {
      const rgb = hexToRgb(panel.color)
      if (rgb) {
        const tint = 0.15
        const r = Math.round(14 + (rgb.r - 14) * tint)
        const g = Math.round(14 + (rgb.g - 14) * tint)
        const b = Math.round(20 + (rgb.b - 20) * tint)
        bgColor = `rgba(${r},${g},${b},0.98)`
      } else {
        bgColor = 'rgba(14,14,20,0.98)'
      }
    } else {
      bgColor = 'rgba(14,14,20,0.98)'
    }
    backdropFilter = 'none'
    borderColor = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(255,255,255,0.04)'
    boxShadowVal = panel.color
      ? `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03), 0 0 30px ${panel.color}12`
      : `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03)`
  } else {
    // Acrylic (current behaviour)
    const baseAlpha = isElectron ? 0.65 : 0.72
    const bgAlpha = baseAlpha * panel.opacity
    if (panel.color) {
      const rgb = hexToRgb(panel.color)
      if (rgb) {
        const tint = 0.30 * panel.opacity
        const r = Math.round(22 + (rgb.r - 22) * tint)
        const g = Math.round(22 + (rgb.g - 22) * tint)
        const b = Math.round(32 + (rgb.b - 32) * tint)
        bgColor = `rgba(${r},${g},${b},${bgAlpha.toFixed(2)})`
      } else {
        bgColor = `rgba(22,22,32,${bgAlpha.toFixed(2)})`
      }
    } else {
      bgColor = `rgba(22,22,32,${bgAlpha.toFixed(2)})`
    }
    backdropFilter = isElectron ? 'none' : 'blur(20px) saturate(1.3)'
    borderColor = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(255,255,255,0.05)'
    boxShadowVal = panel.color ? `0 8px 40px rgba(0,0,0,0.35), 0 0 30px ${panel.color}12` : undefined
  }

  return (
    <motion.div
      initial={{ opacity: 0.3, x: 60 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      className={`fixed z-20 overflow-hidden flex flex-col fluent-shadow-lg ${isElectron ? '' : 'rounded-2xl pwa-safe-bottom'}`}
      style={{
        left: isElectron ? 0 : panel.x,
        top: isElectron ? 0 : panel.y,
        right: isElectron ? 0 : undefined,
        width: isElectron ? '100%' : (viewMode === 'calendar' ? 700 : 340),
        maxWidth: isElectron ? (viewMode === 'calendar' ? 'min(85vw, 800px)' : 'min(85vw, 560px)') : undefined,
        height: isElectron ? '100vh' : 'calc(100dvh - 32px)',
        margin: isElectron ? '0 auto' : undefined,
        zIndex: panelZ,
        background: bgColor,
        backdropFilter,
        border: borderColor,
        boxShadow: boxShadowVal,
        borderRadius: isElectron ? 0 : undefined,
        cursor: isElectron ? 'default' : (panel.locked ? 'default' : dragging ? 'grabbing' : 'default'),
      }}
    >
      {/* Header with drag + window controls */}
      <div
        className="flex items-center justify-between px-4 pt-3 pb-2 border-b border-white/[0.04]"
        onMouseDown={handleMouseDown}
        style={isElectron ? ({ WebkitAppRegion: 'drag' as unknown as React.CSSProperties, cursor: 'default' } as React.CSSProperties) : {
          cursor: panel.locked ? 'default' : dragging ? 'grabbing' : 'grab',
        }}
      >
        <div className="flex items-center gap-2" style={nd}>
          <span className="text-xs font-medium tracking-wide" style={{ color: 'var(--text-primary)' }}>{t('app.title')}</span>
          {/* Mode tabs */}
          <div className="flex items-center bg-white/[0.04] rounded-lg p-0.5 gap-0.5">
            <button
              onClick={() => setPanelMode('todo')}
              className={`px-2 py-0.5 rounded-md text-2xs font-medium transition-all ${
                panelMode === 'todo'
                  ? 'bg-white/[0.08] text-text-secondary'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Icon icon="fa-regular fa-square-check mr-1" />{t('panel.todo')}
            </button>
            <button
              onClick={() => setPanelMode('notes')}
              className={`px-2 py-0.5 rounded-md text-2xs font-medium transition-all ${
                panelMode === 'notes'
                  ? 'bg-white/[0.08] text-text-secondary'
                  : 'text-text-muted hover:text-text-secondary'
              }`}
            >
              <Icon icon="fa-regular fa-note-sticky mr-1" />{t('panel.notes')}
            </button>
          </div>
        </div>
        <div className="flex items-center gap-1" style={nd}>
          <button onClick={() => appPin ? lockApp() : undefined}
            className={`w-6 h-6 flex items-center justify-center rounded-lg transition-all text-xs ${appPin && appLocked ? 'text-fluent-blue/60' : 'text-white/65 hover:text-text-secondary hover:bg-white/[0.06]'}`}
            title={t('lock.title')}>
            <Icon icon="fa-solid fa-lock" />
          </button>
          <button onClick={() => setShowTrash(true)}
            className="w-6 h-6 flex items-center justify-center rounded-lg text-white/65 hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
            title={t('trash.title')}>
            <Icon icon="fa-regular fa-trash-can" />
          </button>
          <button onClick={() => {
            const next = !panel.pinned
            togglePanelPin()
            if (isElectron) ei.alwaysOnTop(next)
          }}
            className={`w-6 h-6 flex items-center justify-center rounded-lg transition-all text-xs ${panel.pinned ? 'text-fluent-blue/60' : 'text-white/65 hover:text-text-secondary hover:bg-white/[0.06]'}`}
            style={panel.locked ? ({ pointerEvents: 'auto', position: 'relative', zIndex: 1001 } as React.CSSProperties) : undefined}
            title={panel.pinned ? t('note.unpin') : t('note.pin')}>
            <Icon icon="fa-solid fa-thumbtack" />
          </button>
          <button onClick={togglePanelLock}
            className={`w-6 h-6 flex items-center justify-center rounded-lg transition-all text-xs ${panel.locked ? 'text-amber-400/60' : 'text-white/65 hover:text-text-secondary hover:bg-white/[0.06]'}`}
            style={panel.locked ? ({ pointerEvents: 'auto', position: 'relative', zIndex: 1000 } as React.CSSProperties) : undefined}
            title={panel.locked ? t('note.unlock') : t('note.lock')}>
            <Icon icon="fa-solid fa-lock" />
          </button>
          <div className="relative">
            <button onClick={() => setShowPanelOpacity(!showPanelOpacity)}
              className="w-6 h-6 flex items-center justify-center rounded-lg text-white/65 hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
              title={t('note.appearance')}>
              <Icon icon="fa-solid fa-circle-half-stroke" />
            </button>
            {showPanelOpacity && (
              <div className="absolute top-full right-0 mt-1 z-50 border border-white/[0.08] rounded-xl px-3 py-2 shadow-xl flex flex-col gap-2 min-w-[180px]"
                onMouseDown={e => e.stopPropagation()}
                style={{ background: 'var(--panel-bg-solid)' }}>
                {/* Theme */}
                <div>
                  <div className="text-xs text-text-secondary mb-1.5">{t('note.theme.title')}</div>
                  <div className="flex items-center gap-1">
                    <button onClick={() => { if (theme !== 'dark') toggleTheme() }}
                      className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${
                        theme === 'dark'
                          ? 'bg-white/[0.08] text-text-primary'
                          : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'
                      }`}
                    >
                      {t('note.theme.dark')}
                    </button>
                    <button onClick={() => { if (theme !== 'light') toggleTheme() }}
                      className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${
                        theme === 'light'
                          ? 'bg-white/[0.08] text-text-primary'
                          : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'
                      }`}
                    >
                      {t('note.theme.light')}
                    </button>
                  </div>
                </div>
                <div className="border-t border-white/[0.06]" />
                {/* Background mode */}
                <div>
                  <div className="text-xs text-text-secondary mb-1.5">{t('settings.background')}</div>
                  <div className="flex items-center gap-1">
                    {(['acrylic', 'pure-white', 'pure-black'] as const).map(m => (
                      <button key={m}
                        onClick={() => { setBackgroundMode(m) }}
                        className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${
                          backgroundMode === m
                            ? 'bg-white/[0.08] text-text-primary'
                            : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'
                        }`}
                      >
                        {t('backgroundMode.' + m)}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="border-t border-white/[0.06]" />
                <div className="flex items-center gap-2">
                  <span className="text-xs text-text-secondary w-7">{t('settings.opacity')}</span>
                  <input type="range" min="0.15" max="1" step="0.05"
                    value={panel.opacity}
                    onChange={e => { setPanelOpacity(parseFloat(e.target.value)) }}
                    className="flex-1 h-1 accent-fluent-blue cursor-pointer"
                  />
                  <span className="text-xs text-text-secondary w-5 text-right">{Math.round(panel.opacity * 100)}%</span>
                </div>
                <div className="border-t border-white/[0.06] pt-2">
                  <div className="flex items-center gap-1.5 mb-1.5">
                    <span className="text-xs text-text-secondary">{t('settings.color')}</span>
                  </div>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {COLOR_PRESETS.map(p => (
                      <button key={p.label}
                        onClick={() => { setPanelColor(p.color || undefined) }}
                        className="w-4 h-4 rounded-full border border-white/[0.15] flex items-center justify-center transition-transform hover:scale-125"
                        style={{ background: p.color || 'transparent' }}
                        title={p.label}>
                        {!p.color ? <span className="w-2 h-0.5 bg-white/30 rounded-full" /> : null}
                        {(panel.color || '') === p.color && <Icon icon="fa-solid fa-check text-[6px] text-text-primary" />}
                      </button>
                    ))}
                    <label className="w-4 h-4 rounded-full border border-dashed border-white/[0.25] flex items-center justify-center cursor-pointer hover:border-white/50 transition-colors"
                      title={t('note.customColor')}>
                      <Icon icon="fa-solid fa-plus text-[7px] text-text-secondary" />
                      <input type="color" value={panel.color || '#3b82f6'}
                        onChange={e => { setPanelColor(e.target.value) }}
                        className="absolute opacity-0 w-0 h-0" />
                    </label>
                  </div>
                </div>
                <div className="border-t border-white/[0.06] pt-2">
                  <div className="text-xs text-text-secondary mb-1.5">{t('settings.language')}</div>
                  <div className="flex items-center gap-1">
                    <button onClick={() => setLocale('zh-CN')}
                      className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${
                        locale === 'zh-CN'
                          ? 'bg-white/[0.08] text-text-primary'
                          : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'
                      }`}>
                      中文
                    </button>
                    <button onClick={() => setLocale('en')}
                      className={`flex-1 h-6 rounded-lg text-xs font-medium transition-all ${
                        locale === 'en'
                          ? 'bg-white/[0.08] text-text-primary'
                          : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.04]'
                      }`}>
                      English
                    </button>
                  </div>
                </div>
              <CustomThemeEditor />
            </div>
            )}
          </div>
          {isElectron && (
            <>
              <button style={nd} onClick={() => ei.minimize()}
                className="w-6 h-6 flex items-center justify-center rounded-lg text-white/65 hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs">
                <Icon icon="fa-regular fa-window-minimize" />
              </button>
              <button style={nd} onClick={() => ei.close()}
                className="w-6 h-6 flex items-center justify-center rounded-lg text-white/65 hover:text-red-400/60 hover:bg-white/[0.06] transition-all text-xs">
                <Icon icon="fa-solid fa-xmark" />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Morning Brief — today's overview (todo mode only, hidden in calendar) */}
      {panelMode === 'todo' && viewMode !== 'calendar' && (
        <div className="px-3 pt-2 flex-shrink-0">
          <MorningBrief />
        </div>
      )}

      {/* Content: unified calendar view vs notes/todo */}
      <div className="flex flex-1 min-h-0 overflow-hidden" style={nd}>
        {viewMode === 'calendar' ? (
          <div className="flex flex-col flex-1 min-h-0">
            <div className="flex items-center gap-2 px-3 pt-2 pb-1">
              <button
                onClick={() => setViewMode('list')}
                className="flex items-center gap-1 text-2xs px-1.5 py-0.5 rounded-lg transition-colors text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]"
              >
                <Icon icon="fa-solid fa-arrow-left text-2xs" />
                <span>{panelMode === 'notes' ? t('panel.notes') : t('panel.todo')}</span>
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-2 pb-2">
              <Suspense fallback={<div className="h-8" />}>
                <CalendarViewLazy />
              </Suspense>
            </div>
          </div>
        ) : panelMode === 'notes' ? (
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
        ) : (
          <>
            {<CategoryPills />}
            <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
              <TodoList />
            </div>
          </>
        )}
      </div>

      {/* Bottom toolbar */}
      <div style={nd}><Toolbar /></div>

      {/* Window width resize handle (Electron only) */}
      {isElectron && (
        <div
          onMouseDown={handleResizeStart}
          className="absolute right-0 top-0 bottom-0 w-1.5 cursor-ew-resize z-50 group"
        >
          <div className={`absolute right-0 top-1/2 -translate-y-1/2 w-0.5 h-8 rounded-full transition-all ${resizing ? 'bg-white/30' : 'bg-white/0 group-hover:bg-white/20'}`} />
        </div>
      )}
      {/* Trash panel modal */}
      <AnimatePresence>
        {showTrash && <TrashPanel onClose={() => setShowTrash(false)} />}
      </AnimatePresence>
    </motion.div>
  )
}

export default AcrylicPanel
