import React, { Suspense, lazy, useEffect, useState, useCallback } from 'react'
import Icon from './components/Common/Icon'

import MainWindow from './components/MainWindow/MainWindow'
import NotesLayer from './components/FloatingNote/NotesLayer'
import FloatingNoteComp from './components/FloatingNote/FloatingNote'
import { useStore } from './store/useStore'
import { errorStore } from './store/errorStore'
import { useTranslation } from './i18n'
import { scheduleAutoBackup } from './utils/backup'
import { getLocalAIConfig } from './utils/aiConfig'
import { uid } from './utils/helpers'
import { undoManager } from './store/undoManager'
import { trackUsage } from './utils/trackUsage'
import { WIKI_LINK_REGEX, PROTOCOL_LINK_REGEX } from './utils/wikiLinks'
import UndoToast from './components/Common/UndoToast'
import ErrorToast from './components/Common/ErrorToast'
import PermissionGate from './components/Common/PermissionGate'
import UpdateNotifier from './components/Common/UpdateNotifier'
import ResumableTasksBanner from './components/Common/ResumableTasksBanner'
import MorningBriefCard from './components/Common/MorningBriefCard'
import { getWsClient } from './utils/ai'
import { rehydrateBackgroundJobs } from '@desk-notes/shared'
import { registerShortcut, installGlobalShortcutListener } from './utils/shortcutRegistry'
import { initFileStore } from './utils/fileStore'
import { applyCustomTheme } from './utils/customTheme'
import { pluginManager } from './utils/pluginSystem'
import { registerBuiltinPlugins } from './plugins/builtins'
import { addAutoSnapshot } from './utils/noteHistory'
import LockScreen from './components/Common/LockScreen'
import SetupWizard from './components/Common/SetupWizard'
import InstallPrompt from './components/Common/InstallPrompt'
import ErrorBoundary from './components/Common/ErrorBoundary'
import { PWAProvider } from './components/PWA'
import StandaloneNotesView from './components/Modules/StandaloneNotesView'
import StandaloneTodoView from './components/Modules/StandaloneTodoView'
import StandaloneChatView from './components/Modules/StandaloneChatView'
import StandaloneInfoView from './components/Modules/StandaloneInfoView'
import FloatingBubbleApp from './FloatingBubble/FloatingBubbleApp'
import FloatingOrbApp from './FloatingBubble/FloatingOrbApp'
import FloatingChatApp from './FloatingBubble/FloatingChatApp'
import ClipboardPopupApp from './FloatingBubble/ClipboardPopupApp'
import ApprovalInbox from './components/Common/ApprovalInbox'
import type { FloatingNote } from './types'

const QuickSearchModal = lazy(() => import('./components/Search/QuickSearchModal'))
const QuickTodoInput = lazy(() => import('./components/Todo/QuickTodoInput'))
const QuickCapture = lazy(() => import('./components/QuickCapture/QuickCapture'))
const ShortcutsModal = lazy(() => import('./components/Common/ShortcutsModal'))

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei
// H9 修复：移除非空断言 ei!，改用安全访问；_ei 仅在 isElectron 为 true 时使用
const _ei = ei ?? ({} as ElectronAPI)
const hash = window.location.hash
const hashParams = new URLSearchParams(hash.replace(/^#/, ''))

const StandaloneNote: React.FC = () => {
  const noteId = hashParams.get('note') || ''
  const note = useStore(s => s.floatingNotes.find(n => n.id === noteId))
  const [loading, setLoading] = useState(true)
  const { t } = useTranslation()

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await initFileStore()

        // Load only the single note instead of scanning all .md files
        if (isElectron && ei?.fileStore) {
          const noteData = await _ei.fileStore.readNote(noteId) as FloatingNote | null
          if (noteData && !cancelled) {
            useStore.setState({ floatingNotes: [noteData] })
          }
        } else {
          await useStore.getState().loadNotesFromDisk()
        }

        if (cancelled) return
        // Sync data-background from store AFTER rehydration,
        // so FloatingNoteComp uses matching CSS variables from the first render
        const mode = useStore.getState().backgroundMode
        document.documentElement.setAttribute('data-background', mode)
        if (mode === 'pure-white') {
          document.documentElement.setAttribute('data-theme', 'light')
        } else if (mode === 'pure-black') {
          document.documentElement.setAttribute('data-theme', 'dark')
        } else {
          document.documentElement.setAttribute('data-theme', useStore.getState().theme)
        }
      } catch (e: unknown) {
        console.error('[StandaloneNote] init failed:', e)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  // Listen for cross-window sync in standalone OS windows
  useEffect(() => {
    if (!ei?.onNoteSynced) return
    const unsubSync = _ei.onNoteSynced((data: { id: string }) => {
      if (data.id !== noteId) return
      ei?.fileStore?.readNote(data.id).then((noteData: Record<string, unknown> | null) => {
        if (noteData && useStore.getState().floatingNotes.some(n => n.id === data.id)) {
          useStore.getState().updateFloatingNote(data.id, noteData)
        }
      }).catch((e: unknown) => console.error('[StandaloneNote] sync error:', e))
    })
    const unsubDeleted = _ei.onNoteDeleted?.((data: { id: string }) => {
      if (data.id === noteId) {
        window.close()
      }
    })
    return () => { unsubSync(); unsubDeleted?.() }
  }, [noteId])

  if (loading) {
    return (
      <div className="w-screen h-screen flex items-center justify-center bg-[var(--bg)] text-white/48 text-xs select-none">
        {t('common.loading')}
      </div>
    )
  }

  if (!note) {
    return (
      <div className="w-screen h-screen flex items-center justify-center bg-[var(--bg)] text-text-secondary text-xs select-none">
        {t('common.notFound')}
      </div>
    )
  }

  return (
    <div className="w-screen h-screen overflow-hidden" style={{ background: 'transparent' }}>
      <FloatingNoteComp note={note} standalone />
    </div>
  )
}

const MainApp: React.FC = () => {
  const setLastBackupTime = useStore(s => s.setLastBackupTime)
  const backgroundMode = useStore(s => s.backgroundMode)
  const activeThemeId = useStore(s => s.activeThemeId)
  const savedThemes = useStore(s => s.savedThemes)
  const appPin = useStore(s => s.appPin)
  const appLocked = useStore(s => s.appLocked)
  const lockApp = useStore(s => s.lockApp)
  const bootPhase = useStore(s => s.bootPhase)
  const bootError = useStore(s => s.bootError)
  const searchModalOpen = useStore(s => s.searchModalOpen)
  const setSearchModalOpen = useStore(s => s.setSearchModalOpen)
  const [quickTodoOpen, setQuickTodoOpen] = useState(false)
  const [quickCaptureOpen, setQuickCaptureOpen] = useState(false)
  const [clipboardText, setClipboardText] = useState('')
  const [shortcutsOpen, setShortcutsOpen] = useState(false)
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null)
  const { t } = useTranslation()

  // Sync DOM attributes and body background for background mode
  useEffect(() => {
    document.documentElement.setAttribute('data-background', backgroundMode)

    if (backgroundMode === 'pure-white') {
      document.documentElement.setAttribute('data-theme', 'light')
      document.body.style.background = 'var(--bg)'
      document.documentElement.style.background = 'var(--bg)'
    } else if (backgroundMode === 'pure-black') {
      document.documentElement.setAttribute('data-theme', 'dark')
      document.body.style.background = 'var(--bg)'
      document.documentElement.style.background = 'var(--bg)'
    } else if (isElectron) {
      // acrylic mode: restore theme from store
      const currentTheme = useStore.getState().theme
      document.documentElement.setAttribute('data-theme', currentTheme)
      document.body.style.background = 'transparent'
      document.documentElement.style.background = 'transparent'
    } else {
      document.documentElement.setAttribute('data-theme', 'dark')
      document.body.style.background = ''
      document.documentElement.style.background = ''
    }
  }, [backgroundMode])

  // Apply custom theme when active theme changes
  useEffect(() => {
    const activeTheme = activeThemeId ? savedThemes.find(t => t.id === activeThemeId) ?? null : null
    applyCustomTheme(activeTheme)
  }, [activeThemeId, savedThemes])

  // B1：WS 连接建立后恢复在途任务（system.active_tasks → backgroundJobs）。
  // 重连也会触发；rehydrate 内部按 id 去重，幂等。
  useEffect(() => {
    const client = getWsClient()
    const unsub = client.onStatusChange((s) => {
      if (s === 'connected') {
        rehydrateBackgroundJobs((m, p) => client.call(m, p)).catch(() => {})
      }
    })
    return unsub
  }, [])

  // Expose store for Electron executeJavaScript access (dev only)
  if (process.env.NODE_ENV === 'development') {
    window.__useStore = useStore as unknown as typeof window.__useStore
  }

  // Initialize Electron-specific setup after init.ts boots (no duplicate note loading)
  useEffect(() => {
    let cancelled = false
    const cleanupFns: (() => void)[] = []

    // Wait for init.ts to complete (it handles fileStore init + note loading)
    const unsub = useStore.subscribe((state) => {
      if (state.bootPhase === 'ready' || state.bootPhase === 'failed') {
        unsub()
        if (cancelled || !isElectron) return
        runElectronSetup()
      }
    })

    // Guard: if already ready by the time this effect runs
    if (useStore.getState().bootPhase === 'ready' || useStore.getState().bootPhase === 'failed') {
      unsub()
      if (!cancelled && isElectron) runElectronSetup()
    }

    async function runElectronSetup() {
      try {

        // Auto-create OS windows for notes that were floated (restore previous session)
        const allNotes = useStore.getState().floatingNotes
        const startupBgMode = useStore.getState().backgroundMode
        if (_ei.sendBackgroundMode) _ei.sendBackgroundMode(startupBgMode)
        for (const n of allNotes) {
          if (n.floated === undefined) {
            useStore.getState().updateFloatingNote(n.id, { floated: false })
          }
          if (n.floated) {
            _ei.createFloatingWindow({
              id: n.id, screenX: n.x, screenY: n.y,
              width: n.width, height: n.height,
              backgroundMode: startupBgMode,
            })
          }
        }

        // Global shortcuts
        if (_ei.onGlobalShortcut) {
          const handler = (data: { action: string; content?: string }) => {
            if (data.action === 'new-note') {
              const id = uid()
              const hasContent = data.content && data.content.trim()
              useStore.getState().addFloatingNote({
                id, type: 'text',
                title: hasContent ? data.content!.slice(0, 30) : t('note.newNote'),
                content: hasContent ? data.content! : '',
                folderId: useStore.getState().activeFolderId || undefined,
                x: 100 + Math.random() * 80, y: 100 + Math.random() * 80,
                width: 260, height: 200, zIndex: useStore.getState().nextZ,
              })
              trackUsage('capture.note')
            } else if (data.action === 'new-todo') {
              setQuickTodoOpen(true)
            } else if (data.action === 'quick-search') {
              setSearchModalOpen(true)
            } else if (data.action === 'quick-capture') {
              setQuickCaptureOpen(true)
            }
          }
          const cleanup = _ei.onGlobalShortcut(handler)
          cleanupFns.push(cleanup)
        }

        // When a floated note's OS window is closed, un-float it back to NotesLayer
        if (_ei.onNoteReturned) {
          const handler = (data: { id: string }) => {
            const note = useStore.getState().floatingNotes.find(n => n.id === data.id)
            if (note) {
              useStore.getState().updateFloatingNote(data.id, { floated: false })
            }
          }
          const cleanup = _ei.onNoteReturned(handler)
          cleanupFns.push(cleanup)
        }

        // Native theme sync from Electron
        if (_ei.onNativeThemeChanged) {
          const cleanup = _ei.onNativeThemeChanged((data: { darkMode: boolean }) => {
            const currentTheme = useStore.getState().theme
            const bgMode = useStore.getState().backgroundMode
            // Only auto-sync if using acrylic background mode (translucent)
            if (bgMode === 'acrylic') {
              const target = data.darkMode ? 'dark' : 'light'
              if (currentTheme !== target) {
                useStore.getState().toggleTheme()
              }
            }
          })
          cleanupFns.push(cleanup)
        }

        // File watcher �?reload note data when external edits detected
        if (_ei.onNoteFileChanged) {
          const cleanup = _ei.onNoteFileChanged((data: { noteId: string }) => {
            if (_ei.fileStore?.readNote) {
              _ei.fileStore.readNote(data.noteId).then((noteData: Record<string, unknown> | null) => {
                if (noteData) {
                  useStore.getState().updateFloatingNote(data.noteId, {
                    content: String(noteData.content ?? ''),
                    title: String(noteData.title ?? ''),
                    updatedAt: Date.now(),
                  })
                }
              }).catch((err: unknown) => {
                console.error('[sync] readNote failed:', err)
              })
            }
          })
          cleanupFns.push(cleanup)
        }

        // Tray actions (open-note from tray recent notes)
        if (_ei.onTrayAction) {
          _ei.onTrayAction((action: { type: string; noteId?: string }) => {
            if (action.type === 'open-note' && action.noteId) {
              useStore.getState().focusNote(action.noteId)
            }
          })
        }

        // Clipboard monitoring is handled by floating orb/chat windows (alwaysOnTop)

        // Init complete
        if (!cancelled) useStore.setState({ bootPhase: 'ready' })

        // Background: pre-warm today's astrology guidance cache
        if (!cancelled) {
          ;(async () => {
            try {
              const state = useStore.getState()
              if (state.userProfile?.birthDate) {
                const { calculateBazi, getDailyGuidance } = await import('./utils/astrology')
                const bazi = calculateBazi(new Date(state.userProfile.birthDate), state.userProfile.birthHour ?? null, state.userProfile.gender ?? null)
                if (bazi) {
                  const today = new Date()
                  const guidance = getDailyGuidance(today, bazi, state.todos)
                  if (guidance.huangli && guidance.advice) {
                    // If AI is enabled, pre-warm the AI guidance cache
                    if (useStore.getState().aiEnabled) {
                      const { getAIGuidance } = await import('./utils/astrology/ai-guidance')
                      getAIGuidance(today, guidance.huangli, bazi, state.todos.filter(t => !t.done), undefined, getLocalAIConfig()).catch((err: unknown) => {
                        console.error('[astro] AI guidance pre-warm failed:', err)
                      })
                    }
                  }
                }
              }
            } catch (err) { console.debug('[astro] cache pre-warm error:', err) }
          })()
        }
      } catch (e: unknown) {
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : String(e)
          console.error('[MainApp] init failed:', e)
          useStore.setState({ bootPhase: 'failed', bootError: msg })
        }
      }
    }

    return () => {
      cancelled = true
      cleanupFns.forEach(fn => fn())
    }
  }, [])

  // Plugin init: runs once after bootPhase is ready (both Electron and browser)
  useEffect(() => {
    if (useStore.getState().bootPhase !== 'ready') return
    registerBuiltinPlugins()
    pluginManager.runHook('onAppStart')
  }, [bootPhase])

  // Sync recent notes to main process for tray menu (Electron only)
  useEffect(() => {
    if (!isElectron) return
    const update = () => {
      const notes = useStore.getState().floatingNotes
        .filter(n => !n.archived)
        .sort((a, b) => (b.updatedAt || b.createdAt || 0) - (a.updatedAt || a.createdAt || 0))
        .slice(0, 8)
        .map(n => ({ id: n.id, title: n.title }))
      ei?.updateRecentNotes?.(notes)
    }
    update()
    const unsub = useStore.subscribe(update)
    return unsub
  }, [])

  // Auto backup (Feature 2)
  useEffect(() => {
    return scheduleAutoBackup(
      () => useStore.getState(),
      (meta) => setLastBackupTime(meta.timestamp)
    )
  }, [setLastBackupTime])

  // Plugin lifecycle hooks �?subscribe to note create/save/delete
  useEffect(() => {
    let prevNotes = useStore.getState().floatingNotes
    const unsub = useStore.subscribe((state) => {
      const currNotes = state.floatingNotes
      // Detect additions
      for (const note of currNotes) {
        if (!prevNotes.find(n => n.id === note.id)) {
          pluginManager.runHook('onNoteCreated', note)
        }
      }
      // Detect deletions
      for (const note of prevNotes) {
        if (!currNotes.find(n => n.id === note.id)) {
          pluginManager.runHook('onNoteDeleted', note)
        }
      }
      // Detect saves (content/todos changes)
      for (const curr of currNotes) {
        const prev = prevNotes.find(n => n.id === curr.id)
        if (prev && (prev.content !== curr.content || JSON.stringify(prev.todos) !== JSON.stringify(curr.todos))) {
          pluginManager.runHook('onNoteSaved', curr)
          // Auto-version: create snapshot if 2+ min since last snapshot
          addAutoSnapshot(curr.id, curr.title, curr.content || '', curr.todos)
        }
      }
      prevNotes = currNotes
    })
    return unsub
  }, [])

  // Browser mode shortcuts (Electron handles its own via main.cjs)
  // B2：迁入注册中心，handler 内做平台守卫（Electron 下不触发）
  const openQuickTodo = useCallback(() => setQuickTodoOpen(true), [])
  const openQuickCapture = useCallback(() => setQuickCaptureOpen(true), [])
  useEffect(() => {
    const g = t('shortcuts.global')
    const offs = [
      registerShortcut({
        id: 'global.quickTodo', combo: 'alt+t', label: t('shortcuts.quickTodo'), group: g,
        handler: () => { if (!isElectron) openQuickTodo() },
      }),
      registerShortcut({
        id: 'global.quickCapture', combo: 'alt+shift+c', label: t('shortcuts.quickCapture'), group: g,
        handler: () => { if (!isElectron) openQuickCapture() },
      }),
      registerShortcut({
        id: 'global.shortcutHint', combo: 'alt+n', label: t('ux.shortcutHintBrowser'), group: g,
        handler: () => { if (isElectron) return; errorStore.info(t('electron.shortcutHint')) },
      }),
      registerShortcut({
        id: 'global.shortcutHint.alt', combo: 'alt+shift+h', label: t('ux.shortcutHintBrowser'), group: g,
        handler: () => { if (isElectron) return; errorStore.info(t('electron.shortcutHint')) },
      }),
    ]
    return () => { for (const off of offs) off() }
  }, [openQuickTodo, openQuickCapture, t])

  // Auto-scan reference links count
  useEffect(() => {
    const scanRefs = () => {
      const allNotes = useStore.getState().floatingNotes
      const refCounts = new Map<string, number>()
      for (const n of allNotes) {
        const content = n.content || ''
        const todoTexts = (n.todos || []).map(t => t.text).join(' ')
        const combined = content + ' ' + todoTexts
        let m
        PROTOCOL_LINK_REGEX.lastIndex = 0
        while ((m = PROTOCOL_LINK_REGEX.exec(combined)) !== null) {
          refCounts.set(m[1], (refCounts.get(m[1]) || 0) + 1)
        }
        // Scan [[Title]] wiki links
        WIKI_LINK_REGEX.lastIndex = 0
        while ((m = WIKI_LINK_REGEX.exec(combined)) !== null) {
          const linkTitle = m[1].trim()
          const target = allNotes.find(
            n => n.title === linkTitle || (n.title || '').toLowerCase() === linkTitle.toLowerCase()
          )
          if (target && target.id !== n.id) {
            refCounts.set(target.id, (refCounts.get(target.id) || 0) + 1)
          }
        }
      }
      // Update ref counts
      let changed = false
      for (const n of allNotes) {
        const count = refCounts.get(n.id) || 0
        if ((n.refCount || 0) !== count) {
          changed = true
          break
        }
      }
      if (changed) {
        for (const n of allNotes) {
          const count = refCounts.get(n.id) || 0
          useStore.getState().updateFloatingNote(n.id, { refCount: count })
        }
      }
    }
    scanRefs()
    const interval = setInterval(scanRefs, 10000)
    return () => clearInterval(interval)
  }, [])

  // Cross-window sync for floated notes
  useEffect(() => {
    if (!ei?.onNoteSynced) return
    const syncedRecently = new Set<string>()
    const syncTimers = new Map<string, ReturnType<typeof setTimeout>>()
    return _ei.onNoteSynced((data: { id: string }) => {
      if (syncedRecently.has(data.id)) return
      syncedRecently.add(data.id)
      syncTimers.set(data.id, setTimeout(() => {
        syncedRecently.delete(data.id)
        syncTimers.delete(data.id)
      }, 2000))

      ei?.fileStore?.readNote(data.id).then((noteData: Record<string, unknown> | null) => {
        if (noteData) {
          // Guard: skip if local copy is newer than file (debounced saves may lag)
          const current = useStore.getState().floatingNotes.find(n => n.id === data.id)
          if (current?.updatedAt && typeof noteData.updatedAt === 'number' && noteData.updatedAt <= current.updatedAt) {
            return
          }
          useStore.getState().updateFloatingNote(data.id, noteData)
        }
      }).catch((e: unknown) => console.error('[App] note sync error:', e))
    })
  }, [])

  // Cross-window deletion sync: remove note from store when deleted in another window
  useEffect(() => {
    if (!ei?.onNoteDeleted) return
    return _ei.onNoteDeleted((data: { id: string }) => {
      const note = useStore.getState().floatingNotes.find(n => n.id === data.id)
      if (note) {
        // 跨窗口缓存失效：源窗口已在后端硬删，这里 localOnly=true 仅抹内存条目，
        // 避免对已消失的笔记重复发 permanent_delete RPC（会撞 S0-2 的假失败提示）
        useStore.getState().permanentlyDeleteNote(data.id, true)
      }
    })
  }, [])

  // Auto-lock on visibility change (browser tab switch / Electron minimize)
  useEffect(() => {
    if (!appPin) return
    const handler = () => {
      if (document.visibilityState === 'hidden') {
        lockApp()
      }
    }
    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
  }, [appPin, lockApp])

  // Capture PWA install prompt
  useEffect(() => {
    if (isElectron) return
    const handler = (e: Event) => {
      e.preventDefault()
      setInstallPrompt(e as BeforeInstallPromptEvent)
    }
    window.addEventListener('beforeinstallprompt', handler)
    return () => window.removeEventListener('beforeinstallprompt', handler)
  }, [])

  // Undo/Redo + Quick search keyboard shortcuts
  // B2：迁入统一快捷键注册中心（冲突可发现、ShortcutsModal 单一事实源）
  useEffect(() => {
    const g = t('shortcuts.global')
    const offs = [
      registerShortcut({ id: 'global.undo', combo: 'ctrl+z', label: t('shortcuts.undo'), group: g, handler: () => undoManager.undo() }),
      registerShortcut({ id: 'global.redo', combo: 'ctrl+shift+z', label: t('shortcuts.redo'), group: g, handler: () => undoManager.redo() }),
      registerShortcut({ id: 'global.quickSearch', combo: 'ctrl+k', label: t('shortcuts.quickSearch'), group: g, handler: () => setSearchModalOpen(true) }),
      registerShortcut({ id: 'global.lock', combo: 'ctrl+shift+l', label: t('shortcuts.lock'), group: g, handler: () => { if (appPin) lockApp() } }),
      registerShortcut({ id: 'global.shortcuts', combo: 'ctrl+?', label: t('shortcuts.title'), group: g, handler: () => setShortcutsOpen(true) }),
      registerShortcut({ id: 'global.shortcuts.shift', combo: 'ctrl+shift+?', label: t('shortcuts.title'), group: g, handler: () => setShortcutsOpen(true) }),
    ]
    const offListener = installGlobalShortcutListener()
    return () => {
      for (const off of offs) off()
      offListener()
    }
  }, [appPin, lockApp, setShortcutsOpen, t])

  const rootBg = backgroundMode === 'pure-white' ? 'var(--bg)'
    : backgroundMode === 'pure-black' ? 'var(--bg)'
    : isElectron ? 'transparent'
    : 'var(--bg)'

  // Boot state machine: show loading/error before main UI
  if (bootPhase === 'booting') {
    return (
      <div className="w-screen h-screen flex flex-col items-center justify-center select-none"
        style={{ background: 'var(--bg)' }}
      >
        <div className="flex flex-col items-center gap-3">
          <div className="w-5 h-5 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
          <span className="text-xs text-white/48">{t('common.loading')}</span>
        </div>
      </div>
    )
  }

  if (bootPhase === 'failed') {
    return (
      <div className="w-screen h-screen flex flex-col items-center justify-center select-none gap-3 px-6"
        style={{ background: 'var(--bg)' }}
      >
        <Icon icon="fa-solid fa-circle-exclamation text-lg text-red-400/60" />
        <span className="text-xs text-text-secondary text-center max-w-[280px] leading-relaxed">
          {bootError || t('common.error')}
        </span>
        <button
          onClick={() => window.location.reload()}
          className="px-3 py-1.5 rounded-lg text-xs transition-all"
          style={{ background: 'var(--brand)', color: '#fff' }}
        >
          {t('common.retry')}
        </button>
      </div>
    )
  }

  return (
    <div className="relative w-full h-full overflow-hidden pwa-safe-top" style={{ background: rootBg, borderRadius: isElectron ? 12 : 0 }}>
      {/* Desktop gradient �?hidden in solid modes */}
      {backgroundMode === 'acrylic' && (
        <div className="absolute inset-0 z-0 pointer-events-none">
          <div className="absolute top-0 right-0 w-[60%] h-[40%] bg-gradient-to-br from-fluent-blue/[0.02] to-transparent rounded-full blur-[120px]" />
          <div className="absolute bottom-0 left-0 w-[40%] h-[30%] bg-gradient-to-tr from-fluent-purple/[0.02] to-transparent rounded-full blur-[100px]" />
        </div>
      )}

      {/* Floating notes layer — hidden in Electron (notes use OS windows) */}
      {!isElectron && <NotesLayer />}

      {/* PWA provider with offline banner + SW update notification */}
      <PWAProvider>

      {/* Main window with nav sidebar + content router */}
      <MainWindow />

      {/* B-2: 全局审批收件箱 — R4 pending 在主窗口任何视图可见可批，
          修复"审批卡只挂在旧聊天界面"导致的 provider.add 死锁 */}
      <ApprovalInbox />

      {/* Browser-mode restore button (appears when MainWindow is hidden) */}
      {!isElectron && <MainWindowRestoreButton />}

      {/* Undo/Redo toast */}
      <UndoToast />

      {/* Error notifications */}
      <ErrorToast />

      {/* Permission request gate (bottom-right floating cards) */}
      <PermissionGate />

      {/* A3: resumable tasks banner (task.resumable event) */}
      <ResumableTasksBanner />

      {/* A5: morning brief card (proactive.triggered trigger=morning_brief) */}
      <MorningBriefCard />

      {/* Auto-update notifier (Electron only) */}
      <UpdateNotifier />

      {/* Global quick search */}
      <Suspense fallback={null}>
        <QuickSearchModal isOpen={searchModalOpen} onClose={() => setSearchModalOpen(false)} />
      </Suspense>

      {/* Quick todo input (Alt+T) */}
      <Suspense fallback={null}>
        <QuickTodoInput isOpen={quickTodoOpen} onClose={() => setQuickTodoOpen(false)} />
      </Suspense>

      {/* Quick capture (Alt+Shift+C) */}
      <Suspense fallback={null}>
        <QuickCapture isOpen={quickCaptureOpen} initialText={clipboardText} onClose={() => { setQuickCaptureOpen(false); setClipboardText('') }} />
      </Suspense>

      {/* Keyboard shortcuts modal (Ctrl+?) */}
      <Suspense fallback={null}>
        <ShortcutsModal isOpen={shortcutsOpen} onClose={() => setShortcutsOpen(false)} />
      </Suspense>

      {/* First-run setup wizard (desktop-only convenience) */}
      <SetupWizard />

      {/* Lock screen */}
      {appLocked && <LockScreen />}

      {/* PWA install prompt banner */}
      {!isElectron && <InstallPrompt prompt={installPrompt} onClose={() => setInstallPrompt(null)} />}
      </PWAProvider>
    </div>
  )
}

/** Floating pill to restore the main panel after browser-mode minimize/close */
const MainWindowRestoreButton: React.FC = () => {
  const { t } = useTranslation()
  const visible = useStore(s => s.panel.visible)
  if (visible) return null
  return (
    <button
      onClick={() => useStore.getState().setPanelVisible(true)}
      className="fixed bottom-6 left-1/2 -translate-x-1/2 z-[99999] flex items-center gap-2 px-4 py-2 rounded-xl border border-white/[0.08] shadow-2xl text-xs text-text-secondary backdrop-blur-xl hover:bg-white/[0.06] transition-all"
      style={{ background: 'var(--panel-bg-solid)' }}
    >
      <Icon icon="fa-solid fa-chevron-up" />
      {t('ux.expandPanel')}
    </button>
  )
}

const App: React.FC = () => {
  if (hash.startsWith('#note=')) {
    return (
      <ErrorBoundary label="StandaloneNote">
        <StandaloneNote />
      </ErrorBoundary>
    )
  }
  if (hash.startsWith('#view=notes')) {
    return (
      <ErrorBoundary label="StandaloneNotesView">
        <StandaloneNotesView />
      </ErrorBoundary>
    )
  }
  if (hash.startsWith('#view=todo')) {
    return (
      <ErrorBoundary label="StandaloneTodoView">
        <StandaloneTodoView />
      </ErrorBoundary>
    )
  }
  if (hash.startsWith('#view=chat')) {
    return (
      <ErrorBoundary label="StandaloneChatView">
        <StandaloneChatView />
      </ErrorBoundary>
    )
  }
  if (hash.startsWith('#view=info')) {
    return (
      <ErrorBoundary label="StandaloneInfoView">
        <StandaloneInfoView />
      </ErrorBoundary>
    )
  }
  if (hash.startsWith('#view=knowledge-graph')) {
    const StandaloneKnowledgeGraphView = React.lazy(() => import('./components/Modules/StandaloneKnowledgeGraphView'))
    return (
      <ErrorBoundary label="StandaloneKnowledgeGraphView">
        <React.Suspense fallback={null}>
          <StandaloneKnowledgeGraphView />
        </React.Suspense>
      </ErrorBoundary>
    )
  }
  if (hash.startsWith('#view=transfer')) {
    const TransferView = React.lazy(() => import('./components/Modules/TransferView'))
    return (
      <ErrorBoundary label="StandaloneTransferView">
        <TransferView />
      </ErrorBoundary>
    )
  }
  // Combined orb-chat (legacy: replaced by floating-orb + floating-chat dual-window)
  if (hash.startsWith('#view=orb-chat')) {
    return (
      <ErrorBoundary label="FloatingOrbApp">
        <FloatingOrbApp />
      </ErrorBoundary>
    )
  }
  // Clipboard action popup — small window at cursor position
  if (hash.startsWith('#view=clipboard-popup')) {
    return (
      <ErrorBoundary label="ClipboardPopupApp">
        <ClipboardPopupApp />
      </ErrorBoundary>
    )
  }
  // Floating orb — new 80×80 standalone orb window
  if (hash.startsWith('#view=floating-orb')) {
    return (
      <ErrorBoundary label="FloatingOrbApp">
        <FloatingOrbApp />
      </ErrorBoundary>
    )
  }
  // Floating chat — new 320×420 standalone chat window
  if (hash.startsWith('#view=floating-chat')) {
    return (
      <ErrorBoundary label="FloatingChatApp">
        <FloatingChatApp />
      </ErrorBoundary>
    )
  }
  // Legacy floating route — redirect to orb
  if (hash.startsWith('#floating') || hash.startsWith('#view=floating')) {
    return (
      <ErrorBoundary label="FloatingBubble">
        <FloatingBubbleApp />
      </ErrorBoundary>
    )
  }
  return (
    <ErrorBoundary label="MainApp">
      <MainApp />
    </ErrorBoundary>
  )
}

export default App
