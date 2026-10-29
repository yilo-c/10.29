import React, { useRef, useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { hexToRgb } from '../../utils/helpers'
import { registerShortcut, installGlobalShortcutListener } from '../../utils/shortcutRegistry'
import { useTranslation } from '../../i18n'
import { trackUsage } from '../../utils/trackUsage'
import type { ActiveModule } from '@desk-notes/shared'
import HeaderBar from './HeaderBar'
import NavSidebar from './NavSidebar'
import ContentRouter from './ContentRouter'
import Toolbar from '../Common/Toolbar'
import StatusBar from './StatusBar'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const MainWindow: React.FC = () => {
  const panel = useStore(s => s.panel)
  const backgroundMode = useStore(s => s.backgroundMode)
  const viewMode = useStore(s => s.viewMode)
  const activeModule = useStore(s => s.activeModule)
  const setActiveModule = useStore(s => s.setActiveModule)
  const setPanelMode = useStore(s => s.setPanelMode)
  const { t } = useTranslation()

  // Sync panelMode when switching to notes/todo (so Toolbar shows correct buttons)
  useEffect(() => {
    if (activeModule === 'notes' || activeModule === 'todo') {
      setPanelMode(activeModule)
    }
  }, [activeModule, setPanelMode])

  // V2-02：模块导航行为上报（覆盖侧栏点击 + Ctrl+1..8 快捷键 + 初始挂载）
  const prevModuleRef = useRef<ActiveModule | null>(null)
  useEffect(() => {
    if (prevModuleRef.current !== activeModule) {
      prevModuleRef.current = activeModule
      if (activeModule) trackUsage('module.open', { domain: activeModule })
    }
  }, [activeModule])
  const [resizing, setResizing] = useState(false)
  const resizeRef = useRef({ startX: 0, startW: 0 })
  const resizeCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    return () => { resizeCleanupRef.current?.() }
  }, [])

  // Browser mode: local panel size (allows user resize)
  const [panelWidth, setPanelWidth] = useState(() => Math.min(window.innerWidth * 0.92, 1280))
  const panelWidthRef = useRef(panelWidth)

  // Calendar needs more width — expand only, never shrink the user's window back
  useEffect(() => {
    if (isElectron && viewMode === 'calendar' && window.innerWidth < 900) {
      ei.resizeWindow(900, window.innerHeight)
    }
  }, [viewMode])

  // Center window on first browser load
  useEffect(() => {
    if (isElectron) return
    const w = Math.min(window.innerWidth * 0.92, 1280)
    const cx = Math.round((window.innerWidth - w) / 2)
    const cy = Math.round(Math.max(window.innerHeight * 0.06, 16))
    const p = useStore.getState().panel
    const needsCenter = p.x < 0 || p.x > window.innerWidth - 100 || p.y < 0 || p.y > window.innerHeight - 100
    if (needsCenter) {
      useStore.getState().setPanelPos(cx, cy)
    }
  }, [])

  // Lock: disable OS window resize when locked
  useEffect(() => {
    if (!isElectron) return
    ei.setResizable(!panel.locked)
  }, [panel.locked])

  // Pin: sync Electron always-on-top state
  useEffect(() => {
    if (!isElectron) return
    ei.alwaysOnTop(panel.pinned)
  }, [panel.pinned])

  // Electron 主窗四角圆角（transparent 无边框窗，CSS 圆角直接透出桌面）。
  // 贴边吸附/最大化时窗口铺满工作区，圆角会露桌面，故此时归零。
  const [maximized, setMaximized] = useState(false)
  useEffect(() => {
    if (!isElectron) return
    const check = () => {
      setMaximized(
        window.outerWidth >= window.screen.availWidth - 2 &&
        window.outerHeight >= window.screen.availHeight - 2,
      )
    }
    check()
    window.addEventListener('resize', check)
    return () => window.removeEventListener('resize', check)
  }, [])

  // B2: Ctrl+B 侧栏 + Ctrl+1..8 模块切换 —— 迁入统一快捷键注册中心。
  // （修复原实现在条件 return 后注册 hook 的 Rules of Hooks 违规；
  // 注册移至 return 前，隐藏时注销，行为与原逻辑一致）
  useEffect(() => {
    if (!panel.visible) return
    const nav = t('shortcuts.nav')
    const moduleMap: Record<string, ActiveModule> = {
      '1': 'chat', '2': 'notes', '3': 'todo', '4': 'dashboard',
      '5': 'knowledge-network', '6': 'agent', '7': 'automation', '8': 'settings',
    }
    const moduleLabels: Record<string, string> = {
      '1': t('ux.modChat'), '2': t('nav.notes'), '3': t('nav.todo'), '4': t('ux.modDashboard'),
      '5': t('ux.modKnowledgeGraph'), '6': 'Agent', '7': t('ux.modAutomation'), '8': t('nav.settings'),
    }
    const offs = Object.entries(moduleMap).map(([digit, mod]) =>
      registerShortcut({
        id: `nav.${mod}`,
        combo: `ctrl+${digit}`,
        label: moduleLabels[digit] ?? mod,
        group: nav,
        handler: () => setActiveModule(mod),
      }),
    )
    offs.push(registerShortcut({
      id: 'nav.toggleSidebar',
      combo: 'ctrl+b',
      label: '折叠/展开侧栏',
      group: nav,
      handler: () => useStore.getState().toggleNavCollapsed(),
    }))
    const offListener = installGlobalShortcutListener()
    return () => {
      for (const off of offs) off()
      offListener()
    }
  }, [panel.visible, setActiveModule, t])

  // Don't render when hidden (browser-mode minimize/close)
  if (!panel.visible) return null

  // Window resize handle (Electron → OS window; browser → local state)
  const handleResizeStart = (e: React.MouseEvent) => {
    if (panel.locked) return
    e.preventDefault()
    e.stopPropagation()
    if (isElectron) {
      resizeRef.current = { startX: e.clientX, startW: window.innerWidth }
    } else {
      resizeRef.current = { startX: e.clientX, startW: panelWidthRef.current }
    }
    const onMove = (ev: MouseEvent) => {
      const dw = ev.clientX - resizeRef.current.startX
      const newW = Math.max(480, resizeRef.current.startW + dw)
      if (isElectron) {
        ei.resizeWindow(newW, window.innerHeight)
      } else {
        panelWidthRef.current = newW
        setPanelWidth(newW)
      }
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

  // ── Background ──
  let bgColor: string
  let borderColor: string
  let boxShadowVal: string | undefined

  if (backgroundMode === 'pure-white') {
    if (panel.color) {
      const rgb = hexToRgb(panel.color)
      if (rgb) {
        bgColor = `rgba(${Math.round(250 + (rgb.r - 250) * 0.3)},${Math.round(252 + (rgb.g - 252) * 0.3)},${Math.round(252 + (rgb.b - 252) * 0.3)},0.95)`
      } else { bgColor = 'rgba(250,250,252,0.95)' }
    } else { bgColor = 'rgba(250,250,252,0.95)' }
    borderColor = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(0,0,0,0.06)'
    boxShadowVal = panel.color
      ? `0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04), 0 0 30px ${panel.color}12`
      : `0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04)`
  } else if (backgroundMode === 'pure-black') {
    if (panel.color) {
      const rgb = hexToRgb(panel.color)
      if (rgb) {
        bgColor = `rgba(${Math.round(14 + (rgb.r - 14) * 0.15)},${Math.round(14 + (rgb.g - 14) * 0.15)},${Math.round(20 + (rgb.b - 20) * 0.15)},0.98)`
      } else { bgColor = 'rgba(14,14,20,0.98)' }
    } else { bgColor = 'rgba(14,14,20,0.98)' }
    borderColor = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(255,255,255,0.04)'
    boxShadowVal = panel.color
      ? `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03), 0 0 30px ${panel.color}12`
      : `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03)`
  } else {
    const baseAlpha = isElectron ? 0.65 : 0.72
    const bgAlpha = baseAlpha * panel.opacity
    if (panel.color) {
      const rgb = hexToRgb(panel.color)
      if (rgb) {
        const tint = 0.30 * panel.opacity
        bgColor = `rgba(${Math.round(22 + (rgb.r - 22) * tint)},${Math.round(22 + (rgb.g - 22) * tint)},${Math.round(32 + (rgb.b - 32) * tint)},${bgAlpha.toFixed(2)})`
      } else { bgColor = `rgba(22,22,32,${bgAlpha.toFixed(2)})` }
    } else { bgColor = `rgba(22,22,32,${bgAlpha.toFixed(2)})` }
    borderColor = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(255,255,255,0.05)'
    boxShadowVal = panel.color ? `0 8px 40px rgba(0,0,0,0.35), 0 0 30px ${panel.color}12` : undefined
  }

  return (
    <motion.div
      initial={{ opacity: 0.3, x: 60 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
      data-testid="main-window"
      className="main-window fixed z-20 overflow-hidden flex flex-col fluent-shadow-lg"
      style={{
        left: isElectron ? 0 : panel.x,
        top: isElectron ? 0 : panel.y,
        width: isElectron ? '100%' : panelWidth,
        height: isElectron ? '100vh' : 'calc(100dvh - 32px)',
        margin: isElectron ? '0 auto' : undefined,
        zIndex: 99999,
        background: bgColor,
        backdropFilter: 'blur(20px) saturate(1.2)',
        border: borderColor,
        boxShadow: boxShadowVal,
        borderRadius: isElectron ? (maximized ? 0 : 14) : 16,
        cursor: 'default',
      }}
    >
      {/* Header */}
      <HeaderBar />

      {/* Body: sidebar + content */}
      <div data-testid="main-window-body" className="main-window-body flex flex-1 min-h-0 overflow-hidden">
        <NavSidebar />
        <main data-testid="main-workspace" className="main-workspace flex-1 flex min-w-0 min-h-0">
          <ContentRouter />
        </main>
      </div>

      {/* Bottom toolbar — only for notes/todo modules, each has its own buttons */}
      {(activeModule === 'notes' || activeModule === 'todo') && <Toolbar />}

      {/* Status bar */}
      <StatusBar />

      {/* Resize handle (right edge) */}
      <div
        onMouseDown={handleResizeStart}
        className="absolute right-0 top-0 bottom-0 w-1.5 cursor-ew-resize z-50 group"
      >
        <div className={`absolute right-0 top-1/2 -translate-y-1/2 w-0.5 h-8 rounded-full transition-all ${resizing ? 'bg-white/30' : 'bg-white/0 group-hover:bg-white/20'}`} />
      </div>
    </motion.div>
  )
}

export default MainWindow
