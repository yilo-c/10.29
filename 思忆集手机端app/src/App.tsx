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
import ApprovalDialog from './components/ui/ApprovalDialog'
import { useKeyboardAvoidance } from './hooks/useKeyboardAvoidance'
import { useSafeAreaInsets } from './hooks/useSafeAreaInsets'
import { useAutoSyncOnForeground } from './hooks/useAutoSync'
import { useBadge } from './hooks/useBadge'
import { useFloatingBall } from './hooks/useFloatingBall'
import { useMobileStore } from './store/mobileStore'
import { haptic } from './lib/haptic'
import { ensureWsConnected, configureWs } from './lib/rpcClient'
import { registerEventBridge, unregisterEventBridge } from './lib/eventBridge'
import { startInboxBridge, stopInboxBridge } from './lib/bridges'
import { tokenManager } from './lib/tokenManager'

type Tab = 'todo' | 'notes' | 'ai' | 'tools' | 'settings'

/**
 * Tab 顺序：AI 居中。
 * 布局理由：AI 是这个 App 的核心能力，放在拇指最容易触达的中央位置，
 * 并用抬起的主按钮（.tabbar__item--primary）做视觉强调，
 * 让「随时唤起 AI」成为肌肉记忆，而不是藏在某个角落。
 */
const tabIcons: { key: Tab; Icon: React.FC<{ size?: number }>; primary?: boolean }[] = [
  { key: 'todo', Icon: IconTodo },
  { key: 'notes', Icon: IconNote },
  { key: 'ai', Icon: IconSparkles, primary: true },
  { key: 'tools', Icon: IconToolbox },
  { key: 'settings', Icon: IconSettings },
]

export default function App() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<Tab>('todo')
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
    handler(mq)
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
    checkAndLock()
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
  // 系统栏避让：把原生 WindowInsets 写进 --safe-bottom / --safe-top，
  // 解决 Redmi 三键导航下底部 Tab 栏与系统导航键重叠
  useSafeAreaInsets()
  useAutoSyncOnForeground()
  useBadge()
  useFloatingBall()

  /* 高风险操作审批：AI 要 tap/swipe 前必须经过用户确认 */
  const pendingApproval = useMobileStore(s => s.pendingApproval)
  const resolveApproval = useMobileStore(s => s.resolveApproval)

  /*
   * 启动时恢复上次会话。
   *
   * 聊天历史存在 IndexedDB（不是 localStorage），因为多步任务的工具轨迹
   * 会让单次对话体积很快超过 localStorage 的 5MB 上限，
   * 写满后 persist 会静默失败，用户看到的就是"记录消失"。
   */
  useEffect(() => {
    void useMobileStore.getState().restoreLastSession()
  }, [])

  /**
   * 消费悬浮球发来的导航请求。
   * 悬浮球在 App 之外，无法直接改 App 的本地 state，
   * 所以通过 mobileStore 发信号，这里订阅并执行。
   */
  useEffect(() => {
    const unsub = useMobileStore.subscribe(state => {
      const tab = state.pendingTabRequest
      if (tab) {
        if (tab === 'ai' || tab === 'todo' || tab === 'notes' || tab === 'tools' || tab === 'settings') {
          setActiveTab(tab as Tab)
        }
        useMobileStore.getState().consumeTabRequest()
      }
      if (state.quickNoteRequested) {
        setShowQuickNote(true)
        useMobileStore.getState().consumeQuickNoteRequest()
      }
    })
    return unsub
  }, [])

  const switchTab = useCallback((tab: Tab) => {
    if (tab === activeTab) return
    haptic.light()
    setActiveTab(tab)
  }, [activeTab])

  return (
    <div className="app-shell">
      <main className="app-main">
        {/* P1-18: 所有 Tab 始终挂载，切换时只切 display，保留滚动位置 / 输入草稿 / 局部状态 */}
        <div className="page-enter" style={{ height: '100%' }}>
          <div className={`page${activeTab === 'todo' ? '' : ' page--hidden'}`}>
            <ErrorBoundary context="todo"><TodoView /></ErrorBoundary>
          </div>
          <div className={`page${activeTab === 'notes' ? '' : ' page--hidden'}`}>
            <ErrorBoundary context="notes"><NotesView /></ErrorBoundary>
          </div>
          <div className={`page${activeTab === 'ai' ? '' : ' page--hidden'}`}>
            <ErrorBoundary context="ai"><ChatView /></ErrorBoundary>
          </div>
          <div className={`page${activeTab === 'tools' ? '' : ' page--hidden'}`}>
            <ErrorBoundary context="tools"><ToolsView /></ErrorBoundary>
          </div>
          <div className={`page${activeTab === 'settings' ? '' : ' page--hidden'}`}>
            <ErrorBoundary context="settings"><SettingsView /></ErrorBoundary>
          </div>
        </div>
      </main>

      {bootPhase === 'booting' && <SkeletonLoader />}

      {/*
        浮层必须各自有 ErrorBoundary。此前它们裸挂在 App 根部：
        LockScreen / Onboarding / GlobalSearch / Toast / QuickNoteSheet / ApprovalDialog
        任一抛错都会让整棵树卸载 → 白屏，连解锁界面都没了。
        单 Tab 崩溃只降级自己是已有的设计（见上面各 Tab 的包裹），
        浮层这块当时漏了，这里补齐。
      */}
      <ErrorBoundary context="lock"><LockScreen /></ErrorBoundary>

      {showOnboarding && (
        <ErrorBoundary context="onboarding">
          <Onboarding onComplete={handleOnboardingComplete} />
        </ErrorBoundary>
      )}

      <ErrorBoundary context="search">
        <GlobalSearch
          open={searchModalOpen}
          onClose={() => setSearchModalOpen(false)}
          onSelectTodo={() => { setActiveTab('todo'); setSearchModalOpen(false) }}
          onSelectNote={() => { setActiveTab('notes'); setSearchModalOpen(false) }}
        />
      </ErrorBoundary>

      <ErrorBoundary context="toast"><Toast /></ErrorBoundary>

      <nav className="tabbar tabbar--glass" role="tablist" aria-label={t('mobileNav.label') || '主导航'}>
        {tabIcons.map(({ key, Icon, primary }) => {
          const active = activeTab === key
          const label = t(`mobileNav.${key}`)
          if (primary) {
            return (
              <button
                key={key}
                role="tab"
                aria-label={label}
                aria-selected={active}
                className={`tabbar__item tabbar__item--primary${active ? ' tabbar__item--active' : ''}`}
                onClick={() => switchTab(key)}
              >
                <span className="tabbar__fab"><Icon size={24} /></span>
                <span className="tabbar__label">{label}</span>
              </button>
            )
          }
          return (
            <button
              key={key}
              role="tab"
              aria-label={label}
              aria-selected={active}
              className={`tabbar__item${active ? ' tabbar__item--active' : ''}`}
              onClick={() => switchTab(key)}
            >
              <Icon size={22} />
              <span className="tabbar__label">{label}</span>
            </button>
          )
        })}
      </nav>

      <ErrorBoundary context="quicknote">
        <QuickNoteSheet
          open={showQuickNote}
          onClose={() => setShowQuickNote(false)}
        />
      </ErrorBoundary>

      {/*
        高风险操作审批弹窗。
        放在最外层，确保任何 Tab 下都能弹出（AI 可能在后台执行多步任务）。
        AI 被拒绝后会收到原因并转述给用户，不会静默重试。
      */}
      <ErrorBoundary context="approval">
        <ApprovalDialog
          request={pendingApproval}
          onDecide={(approved, reason) => resolveApproval(approved, reason)}
        />
      </ErrorBoundary>
    </div>
  )
}
