import React, { useState, useCallback, useRef, useEffect } from 'react'
import { useStore } from '../store/useStore'
import { getAIProvider, DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL } from '../utils/ai'
import Orb from './Orb'
import ChatPanel, { type ChatMessage } from './ChatPanel'
import { usePetState } from './usePetState'
import { useBackendEvents } from './useBackendEvents'
import SelectionBubble from './SelectionBubble'
import { useProactiveEngine } from '../components/Proactive'

/** Phase of the floating bubble state machine */
type Phase = 'orb' | 'panel'

/** QWebChannel bridge — injected by PyQt6 at runtime */
interface BridgeAPI {
  pushMessage: (msg: ChatMessage) => void
  setMood: (mood: string) => void
  setTheme: (theme: 'dark' | 'light') => void
  setPinned: (pinned: boolean) => void
  setLocked: (locked: boolean) => void
  pushChunk: (chunk: string) => void
  streamDone: () => void
  showNotification: (data: { title: string; body: string; type: string }) => void
  showSubtitle: (text: string, durationMs: number) => void
}

const FLOATING_CONV_ID = '__floating_bubble__'

/** Convert a store ChatMessage (with timestamp) to local ChatMessage (without). */
function toLocal(m: { role: string; content: string }): ChatMessage {
  return { role: m.role as 'user' | 'assistant', content: m.content }
}

interface FloatingBubbleAppProps {
  /** When true, renders inside MainApp without full-screen wrapper or bridge API */
  embedded?: boolean
}

/**
 * FloatingBubbleApp — state machine that shows either the orb or the panel, never both.
 *
 *  orb (default, draggable)
 *    └─ click → panel  (orb hides)
 *         └─ close/minimize → orb  (panel hides, orb reappears at last position)
 *
 * In standalone mode (default): full-screen, exposes bridge API on window.__floatingBubble.
 * In embedded mode: fixed-position overlay inside MainApp, no bridge API, pointer-events pass through.
 *
 * Bridge API exposed on window.__floatingBubble for PyQt6 QWebChannel integration.
 * Messages are persisted via Zustand store (conversations[FLOATING_CONV_ID]).
 */
const FloatingBubbleApp: React.FC<FloatingBubbleAppProps> = ({ embedded = false }) => {
  const ei = window.electronAPI as ElectronAPI | undefined
  const { mood, setMood } = usePetState()
  const storeConversations = useStore(s => s.conversations)
  const storeAddMessage = useStore(s => s.addMessage)
  const storeSetActiveConversation = useStore(s => s.setActiveConversation)
  const proactiveBrief = useStore(s => s.proactiveBrief)
  const proactiveSuggestions = useStore(s => s.proactiveSuggestions)
  useProactiveEngine()

  const sendingRef = useRef(false)

  // Ensure a persistent conversation exists for the floating bubble
  useEffect(() => {
    const exists = storeConversations.find(c => c.id === FLOATING_CONV_ID)
    if (!exists) {
      useStore.setState(s => ({
        conversations: [
          ...s.conversations,
          { id: FLOATING_CONV_ID, title: '浮动助手', createdAt: Date.now(), updatedAt: Date.now(), messages: [] },
        ],
      }))
    }
  }, [storeConversations])

  // Load messages from store conversation
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    const conv = storeConversations.find(c => c.id === FLOATING_CONV_ID)
    if (conv && conv.messages.length > 0) {
      return conv.messages.map(toLocal)
    }
    return [{ role: 'assistant' as const, content: '你好！我是小问，有什么可以帮助你的吗？' }]
  })

  // Sync messages back to store whenever they change
  const messagesRef = useRef(messages)
  messagesRef.current = messages
  useEffect(() => {
    storeSetActiveConversation(FLOATING_CONV_ID)
    // Only sync new messages to avoid feedback loop
    const conv = useStore.getState().conversations.find(c => c.id === FLOATING_CONV_ID)
    if (!conv) return
    const localLen = messages.length
    const storeLen = conv.messages.length
    if (localLen > storeLen) {
      for (let i = storeLen; i < localLen; i++) {
        const m = messages[i]
        storeAddMessage(m.content, m.role)
      }
    }
  }, [messages, storeAddMessage, storeSetActiveConversation])

  // When loaded via #floating hash (QWebEngineView in Python), start in panel mode
  const [phase, setPhase] = useState<Phase>(
    window.location.hash.startsWith('#floating') ? 'panel' : 'orb',
  )
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  // Orb position — remembered across phase changes
  const [orbPos, setOrbPos] = useState(() => ({
    x: window.innerWidth - 120,
    y: window.innerHeight / 2 - 36,
  }))
  const orbPosRef = useRef(orbPos)

  // Chat panel position (independent from orb; draggable via header)
  const [chatPos, setChatPos] = useState(() => ({
    x: Math.max(8, window.innerWidth - 120 - 140),
    y: Math.max(8, window.innerHeight / 2 - 36 - 210),
  }))
  const chatPosRef = useRef(chatPos)

  // Chat panel size (resizable)
  const [chatSize, setChatSize] = useState({ w: 320, h: 420 })
  const chatSizeRef = useRef(chatSize)

  // ── Phase transitions ──

  const handleOrbClick = useCallback(() => {
    orbPosRef.current = orbPos
    // Position chat panel relative to orb
    const nx = Math.max(8, orbPos.x - 140)
    const ny = Math.max(8, orbPos.y - 210)
    setChatPos({ x: nx, y: ny })
    chatPosRef.current = { x: nx, y: ny }
    setPhase('panel')
  }, [orbPos])

  const handleClose = useCallback(() => {
    setPhase('orb')
  }, [])

  const handleMinimize = useCallback(() => {
    setPhase('orb')
  }, [])

  // ── Backend events (WebSocket EventBridge) ──

  const { connected: backendConnected, lastEvent } = useBackendEvents()

  // Sync orb mood with backend mood.changed events
  const prevMoodRef = useRef(mood)
  useEffect(() => {
    const ev = lastEvent('mood.changed')
    const newMood = ev?.payload?.mood as string | undefined
    if (newMood && ['idle', 'thinking', 'happy', 'alert'].includes(newMood) && newMood !== prevMoodRef.current) {
      setMood(newMood as typeof mood)
      prevMoodRef.current = newMood as typeof mood
      const timer = setTimeout(() => setMood('idle'), 6000)
      return () => clearTimeout(timer)
    }
  }, [lastEvent, setMood, mood])

  // Proactive suggestion → flash orb alert, inject message when panel opens
  const [pendingSuggestion, setPendingSuggestion] = useState<{ title: string; description: string } | null>(null)
  useEffect(() => {
    const ev = lastEvent('proactive.suggestion')
    if (ev?.payload) {
      setPendingSuggestion({
        title: String(ev.payload.title ?? ''),
        description: String(ev.payload.description ?? ''),
      })
      setMood('alert')
      setTimeout(() => setMood('idle'), 5000)
    }
  }, [lastEvent, setMood])

  // When panel opens, inject pending suggestion as an assistant message
  useEffect(() => {
    if (phase === 'panel' && pendingSuggestion) {
      setMessages(prev => {
        // Avoid duplicate if already injected
        if (prev.some(m => m.content.includes(pendingSuggestion.title))) return prev
        return [...prev, {
          role: 'assistant' as const,
          content: `💡 **${pendingSuggestion.title}**\n\n${pendingSuggestion.description}\n\n需要我详细说说吗？`,
        }]
      })
      setPendingSuggestion(null)
    }
  }, [phase, pendingSuggestion])

  // ── Local proactive engine → inject as assistant messages ──

  const injectedDigests = useRef(new Set<string>())

  // Morning briefing
  useEffect(() => {
    if (!proactiveBrief) return
    const key = `brief:${proactiveBrief}`
    if (injectedDigests.current.has(key)) return
    injectedDigests.current.add(key)
    setMessages(prev => {
      if (prev.some(m => m.content.includes(proactiveBrief))) return prev
      return [...prev, { role: 'assistant', content: `🌅 ${proactiveBrief}` }]
    })
  }, [proactiveBrief])

  // Proactive suggestions (deduplicated by title|description)
  useEffect(() => {
    const newOnes = proactiveSuggestions.filter(s => {
      const key = `sug:${s.title}|${s.description}`
      if (injectedDigests.current.has(key)) return false
      injectedDigests.current.add(key)
      return true
    })
    if (newOnes.length === 0) return
    const content = newOnes.map(s => `💡 ${s.title}\n${s.description}`).join('\n\n')
    setMessages(prev => [...prev, { role: 'assistant', content }])
  }, [proactiveSuggestions])

  // ── Handlers ──

  const handleSend = useCallback(async (text: string) => {
    if (sendingRef.current) return
    sendingRef.current = true
    try {
    const userMsg: ChatMessage = { role: 'user', content: text }
    setMessages(prev => [...prev, userMsg])

    // QWebChannel bridge path (when loaded in PyQt6 QWebEngineView)
    const bridge = window.bridge
    if (bridge?.sendMessage) {
      bridge.sendMessage(text)
      return
    }

    const aiEnabled = useStore.getState().aiEnabled

    let provider
    if (backendConnected) {
      provider = getAIProvider({
        enabled: true,
        apiUrl: DEFAULT_LOCAL_API_URL,
        model: DEFAULT_LOCAL_MODEL,
        protocol: 'ollama',
        autoTagging: false,
        strategy: 'ws',
      })
    } else if (aiEnabled) {
      provider = getAIProvider({
        enabled: true,
        apiUrl: DEFAULT_LOCAL_API_URL,
        model: DEFAULT_LOCAL_MODEL,
        protocol: 'ollama',
        autoTagging: false,
      })
    }

    if (!provider) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: backendConnected
          ? 'AI 功能未启用。请在「设置」→「AI」中配置 API。'
          : '后端未连接，AI 功能不可用。请先启动思忆集后端。',
      }])
      return
    }

    // Add empty assistant message for streaming
    setMessages(prev => [...prev, { role: 'assistant', content: '' }])

    try {
      const conversationContext = messages
        .filter(m => m.role === 'user' || (m.role === 'assistant' && m.content))
        .slice(-8)
        .map(m => `${m.role === 'user' ? '用户' : '助手'}：${m.content}`)
        .join('\n')

      const prompt = `你是一个友好的 AI 助手，名叫小问。请用中文回答用户的问题。

${conversationContext ? `以下是对话历史：\n${conversationContext}\n\n` : ''}用户：${text}
助手：`

      const controller = new AbortController()
      await provider.processText(
        'chat',
        prompt,
        (chunk) => {
          setMessages(prev => {
            const copy = [...prev]
            const last = copy[copy.length - 1]
            if (last && last.role === 'assistant') {
              copy[copy.length - 1] = { role: 'assistant', content: last.content + chunk }
            }
            return copy
          })
        },
        controller.signal,
      )
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      setMessages(prev => {
        const copy = [...prev]
        copy[copy.length - 1] = {
          role: 'assistant',
          content: `[错误] ${err instanceof Error ? err.message : 'AI 回复失败，请检查网络连接和 API 配置。'}`,
        }
        return copy
      })
    }
    } finally {
      sendingRef.current = false
    }
  }, [messages, backendConnected])

  // ── Drag (orb phase only) ──

  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({ startX: 0, startY: 0, origX: 0, origY: 0 })

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (phase !== 'orb') return
    e.preventDefault()

    // Standalone Electron mode → drag via main-process cursor polling
    // (mousemove stops firing when cursor leaves transparent window pixels)
    if (!embedded && ei?.dragStart) {
      const offsetX = window.screenX - e.screenX
      const offsetY = window.screenY - e.screenY
      ei.dragStart(offsetX, offsetY)
      setDragging(true)

      const onUp = () => {
        setDragging(false)
        ei.dragEnd()
        window.removeEventListener('mouseup', onUp)
      }
      window.addEventListener('mouseup', onUp)
      return
    }

    // Embedded / browser mode → drag the DOM element
    dragRef.current = { startX: e.clientX, startY: e.clientY, origX: orbPos.x, origY: orbPos.y }
    setDragging(true)

    const onMove = (ev: MouseEvent) => {
      setOrbPos({
        x: dragRef.current.origX + ev.clientX - dragRef.current.startX,
        y: dragRef.current.origY + ev.clientY - dragRef.current.startY,
      })
    }
    const onUp = () => {
      setDragging(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [phase, orbPos, embedded, ei])

  // ── Expose bridge API for PyQt6 — skip when embedded in MainApp ──

  useEffect(() => {
    if (embedded) return
    const bridge: BridgeAPI = {
      pushMessage: (msg: ChatMessage) => {
        setMessages(prev => [...prev, msg])
      },
      pushChunk: (chunk: string) => {
        setMessages(prev => {
          const copy = [...prev]
          const last = copy[copy.length - 1]
          if (last && last.role === 'assistant') {
            copy[copy.length - 1] = { ...last, content: last.content + chunk }
          } else {
            copy.push({ role: 'assistant', content: chunk })
          }
          return copy
        })
      },
      streamDone: () => {
        // Assistant message is already in the messages array — nothing extra needed
      },
      showNotification: (data: { title: string; body: string; type: string }) => {
        // TODO: implement inline notification toast in Phase 3
        console.info('[Bridge] notification:', data)
      },
      showSubtitle: (text: string, durationMs: number) => {
        // TODO: implement subtitle display in Phase 4
        console.info('[Bridge] subtitle:', text, durationMs)
      },
      setMood: (m: string) => {
        if (['idle', 'thinking', 'happy', 'alert'].includes(m)) {
          setMood(m as typeof mood)
        }
      },
      setTheme: (t) => setTheme(t),
      setPinned: (_pinned: boolean) => useStore.getState().togglePanelPin(),
      setLocked: (_locked: boolean) => useStore.getState().togglePanelLock(),
    }
    window.__floatingBubble = bridge
    return () => { delete window.__floatingBubble }
  }, [setMood, embedded])

  // ── Apply theme ──
  useEffect(() => {
    if (embedded) return // MainApp handles theme
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme, embedded])

  return (
    <div
      className={embedded ? '' : 'w-screen h-screen'}
      style={{
        background: 'transparent',
        // In embedded mode, use fixed overlay with pointerEvents none
        // so clicks pass through to MainApp content beneath
        ...(embedded ? { position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 50 } : {}),
      }}
    >
      {/* ── Orb phase ── */}
      {phase === 'orb' && (
        <div
          className="fixed z-50"
          style={{
            left: orbPos.x,
            top: orbPos.y,
            cursor: dragging ? 'grabbing' : 'grab',
            pointerEvents: 'auto',
          }}
          onMouseDown={handleMouseDown}
        >
          {/* Connection status ring */}
          <div
            className="absolute rounded-full transition-all duration-300"
            style={{
              inset: -3,
              border: '2px solid',
              borderColor: !backendConnected
                ? 'rgba(100,100,100,0.3)'
                : backendConnected
                  ? 'rgba(34,197,94,0.5)'
                  : 'rgba(245,158,11,0.5)',
              boxShadow: backendConnected ? '0 0 8px rgba(34,197,94,0.2)' : 'none',
              borderRadius: '50%',
            }}
          />
          <Orb
            mood={mood}
            onClick={handleOrbClick}
            size={embedded ? 48 : 72}
          />
        </div>
      )}

      {/* ── Panel phase ── */}
      {phase === 'panel' && (
        <div
          className="fixed z-50"
          style={{
            left: chatPos.x,
            top: chatPos.y,
            pointerEvents: 'auto',
          }}
        >
          <ChatPanel
            mood={mood}
            theme={theme}
            onThemeChange={setTheme}
            onMinimize={handleMinimize}
            onClose={handleClose}
            onSend={handleSend}
            messages={messages}
            onMovePanel={(x, y) => {
              setChatPos({ x, y })
              chatPosRef.current = { x, y }
            }}
            initWidth={chatSizeRef.current.w}
            initHeight={chatSizeRef.current.h}
            onResize={(w, h) => {
              setChatSize({ w, h })
              chatSizeRef.current = { w, h }
            }}
          />
        </div>
      )}
      {/* V1-7：划词 → 浮窗动作菜单（pattern.execute） */}
      <SelectionBubble />
    </div>
  )
}

export default FloatingBubbleApp
