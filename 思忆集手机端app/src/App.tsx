import { useState, useEffect, useCallback } from 'react'
import { useStore, useTranslation } from '@desk-notes/shared'
import { IconTodo, IconNote, IconSettings, IconSparkles, IconToolbox } from './components/ui'
import TodoView from './components/features/todo/TodoView'
import NotesView from './components/features/notes/NotesView'
import SettingsView from './components/features/settings/SettingsView'
import ChatView from './components/features/chat/ChatView'
import ToolsView from './components/features/tools/ToolsView'
import QuickNoteSheet from './components/features/notes/QuickNoteSheet'
import GlobalSearch from './components/features/search/GlobalSearch'
import LockScreen from './components/features/lock/LockScreen'
import Onboarding from './components/features/onboarding/Onboarding'
import { Toast, SkeletonLoader } from './components/ui'
import ErrorBoundary from './components/ui/ErrorBoundary'
import { useKeyboardAvoidance } from './hooks/useKeyboardAvoidance'
import { useAutoSyncOnForeground } from './hooks/useAutoSync'
import { useBadge } from './hooks/useBadge'
import { useFloatingBall } from './hooks/useFloatingBall'
import { haptic, touchScale } from './lib/haptic'
import { ensureWsConnected, configureWs } from './lib/rpcClient'
import { registerEventBridge, unregisterEventBridge } from './lib/eventBridge'
import { startInboxBridge, stopInboxBridge } from './lib/bridges'
import { tokenManager } from './lib/tokenManager'

type Tab = 'todo' | 'notes' | 'ai' | 'tools' | 'settings'

const tabIcons: { key: Tab; Icon: React.FC<{ size?: number }> }[] = [
  { key: 'todo', Icon: IconTodo },
  { key: 'notes', Icon: IconNote },
  { key: 'ai', Icon: IconSparkles },
  { key: 'tools', Icon: IconToolbox },
  { key: 'settings', Icon: IconSettings },
]

export default function App() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<Tab>('todo')
  // P1-18: animKey 移除 —— Tab 切换不再 remount，保留滚动位置/输入草稿
  const theme = useStore(s => s.theme)
  const setTheme = useStore(s => s.setTheme)
  const appPin = useStore(s => s.appPin)
  const lockApp = useStore(s => s.lockApp)
  const bootPhase = useStore(s => s.bootPhase)
  const searchModalOpen = useStore(s => s.searchModalOpen)
  const setSearchModalOpen = useStore(s => s.setSearchModalOpen)

  const [showQuickNote, setShowQuickNote] = useState(false)
  const [showOnboarding, setShowOnboarding] = useState(() => {
    return !localStorage.getItem('siyi-onboarding-done')
  })

  // System theme follow
  const [systemFollow, _setSystemFollow] = useState(() => {
    return localStorage.getItem('siyi-system-theme') === '1'
  })

  useEffect(() => {
    if (!systemFollow) return
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const handler = (e: MediaQueryListEvent | MediaQueryList) => {
      setTheme(e.matches ? 'dark' : 'light')
    }
    handler(mq) // apply immediately
    mq.addEventListener('change', handler as (e: MediaQueryListEvent) => void)
    return () => mq.removeEventListener('change', handler as (e: MediaQueryListEvent) => void)
  }, [systemFollow, setTheme])

  const handleOnboardingComplete = useCallback(() => {
    localStorage.setItem('siyi-onboarding-done', '1')
    setShowOnboarding(false)
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  useEffect(() => {
    if ('Notification' in window && Notification.permission === 'default') {
      Notification.requestPermission()
    }
  }, [])

  // P0-stage0: 把 shared WsClient 拉起并注册事件桥（取代手机端独立 WS）。
  // 已配对时立即用桌面 URL 初始化；未配对时 fallback URL 也能让 RPC 优雅降级到 HTTP。
  useEffect(() => {
    try {
      configureWs(tokenManager.baseUrl ?? null)
      ensureWsConnected()
    } catch { /* ignore */ }
    const unregister = registerEventBridge()
    startInboxBridge()
    return () => {
      try { unregister() } catch { /* ignore */ }
      stopInboxBridge()
      unregisterEventBridge()
    }
  }, [])

  // Auto-lock on mount + after hydration if PIN is set
  useEffect(() => {
    const checkAndLock = () => {
      if (useStore.getState().appPin) {
        lockApp()
      }
    }
    // Try immediately (works if already hydrated)
    checkAndLock()
    // Also subscribe to hydration completion (async rehydrate)
    const unsub = useStore.persist.onFinishHydration(checkAndLock)
    return unsub
  }, [lockApp])

  // Auto-lock when app goes to background
  useEffect(() => {
    const handleVisibility = () => {
      if (document.hidden && appPin) {
        lockApp()
      }
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [appPin, lockApp])

  useKeyboardAvoidance()
  useAutoSyncOnForeground()
  useBadge()
  useFloatingBall()

  const switchTab = useCallback((tab: Tab) => {
    if (tab === activeTab) return
    haptic.light()
    setActiveTab(tab)
  }, [activeTab])

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100dvh',
      background: 'var(--bg-primary)',
      color: 'var(--text-primary)',
    }}>
      <main style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
        {/* P1-18: 所有 Tab 始终挂载，切换时只切 display，保留滚动位置 / 输入草稿 / 局部状态 */}
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
            animation: 'fadeSlideIn 0.2s var(--ease-out)',
          }}
        >
          <div style={{ flex: 1, overflow: 'hidden', display: activeTab === 'todo' ? 'flex' : 'none', flexDirection: 'column' }}>
            <ErrorBoundary context="todo"><TodoView /></ErrorBoundary>
          </div>
          <div style={{ flex: 1, overflow: 'hidden', display: activeTab === 'notes' ? 'flex' : 'none', flexDirection: 'column' }}>
            <ErrorBoundary context="notes"><NotesView /></ErrorBoundary>
          </div>
          <div style={{ flex: 1, overflow: 'hidden', display: activeTab === 'ai' ? 'flex' : 'none', flexDirection: 'column' }}>
            <ErrorBoundary context="ai"><ChatView /></ErrorBoundary>
          </div>
          <div style={{ flex: 1, overflow: 'hidden', display: activeTab === 'tools' ? 'flex' : 'none', flexDirection: 'column' }}>
            <ErrorBoundary context="tools"><ToolsView /></ErrorBoundary>
          </div>
          <div style={{ flex: 1, overflow: 'hidden', display: activeTab === 'settings' ? 'flex' : 'none', flexDirection: 'column' }}>
            <ErrorBoundary context="settings"><SettingsView /></ErrorBoundary>
          </div>
        </div>
      </main>

      {bootPhase === 'booting' && <SkeletonLoader />}

      <LockScreen />

      {showOnboarding && (
        <Onboarding onComplete={handleOnboardingComplete} />
      )}

      <GlobalSearch
        open={searchModalOpen}
        onClose={() => setSearchModalOpen(false)}
        onSelectTodo={() => { setActiveTab('todo'); setSearchModalOpen(false) }}
        onSelectNote={() => { setActiveTab('notes'); setSearchModalOpen(false) }}
      />

      <Toast />

      <nav style={{
        display: 'flex',
        alignItems: 'center',
        background: 'var(--bg-primary)',
        borderTop: '0.5px solid var(--border-subtle)',
        paddingTop: 8,
        paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 48px)',
        minHeight: 90,
        overflowX: 'auto',
        WebkitOverflowScrolling: 'touch',
        flexShrink: 0,
      }}>
        {tabIcons.map(({ key, Icon }) => {
          const active = activeTab === key
          const label = t(`mobileNav.${key}`)
          return (
            <button
              key={key}
              aria-label={label}
              onClick={() => switchTab(key)}
              onTouchStart={touchScale.onStart}
              onTouchEnd={touchScale.onEnd}
              onMouseDown={touchScale.onStart}
              onMouseUp={touchScale.onEnd}
              style={{
                flex: '0 0 auto',
                minWidth: 56,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 2,
                padding: '8px 8px 6px',
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: active ? 'var(--accent)' : 'var(--text-tertiary)',
                transition: 'color 0.15s ease',
                WebkitTapHighlightColor: 'transparent',
              }}
            >
              <Icon size={22} />
              <span style={{
                fontSize: 10,
                fontWeight: active ? 600 : 400,
                letterSpacing: 0.2,
              }}>{label}</span>
            </button>
          )
        })}
      </nav>

      <QuickNoteSheet
        open={showQuickNote}
        onClose={() => setShowQuickNote(false)}
      />
    </div>
  )
}
