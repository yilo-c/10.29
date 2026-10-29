import React, { useState, useCallback, useRef, useEffect, useMemo } from 'react'
import { useStore } from '../store/useStore'
import { getAIProvider, useModels, DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL } from '@desk-notes/shared'
import ChatPanel, { type ChatMessage } from './ChatPanel'
import { usePetState } from './usePetState'
import { useBackendEvents, useBrowserEvents, type BackendEvent } from './useBackendEvents'
import { useEmitInteractionEvents } from './useInteractionEvents'
import { useProactiveEngine } from '../components/Proactive'
import SelectionBubble from './SelectionBubble'
import { applyFrameToMessages, isExecutionFrame } from './frameRouting'
import { resolveSharedSessionId, readSharedActiveConversationId, sharedAppendStart } from './sessionShare'
import type { ChatStreamEvent } from '../components/chat/useChatStream'
import { resumeChatStream } from '../components/chat/useChatStream'
import { useSandboxApprovals } from '../hooks/useSandboxApprovals'
import { useConfirmApprovals } from '../hooks/useConfirmApprovals'

const FLOATING_CONV_ID = '__floating_bubble__'

/** 空会话时的开场白（悬浮窗历史为空 / 接力到一条空会话时使用）。 */
const GREETING = '你好！我是小问，有什么可以帮助你的吗？'

/** Periodic auto-save interval (ms) */
const AUTO_SAVE_INTERVAL = 30_000

/** Fallback providers when backend is not connected or model.list fails */
const FALLBACK_PROVIDERS = [
  { id: 'auto', label: '自动' },
] as const

/** Convert a store ChatMessage (with timestamp) to local ChatMessage (without). */
function toLocal(m: { role: string; content: string }): ChatMessage {
  return { role: m.role as 'user' | 'assistant', content: m.content }
}

interface FloatingChatAppProps {
  /** When provided, close/minimize calls this instead of IPC hideFloatingChat */
  onClose?: () => void
}

/**
 * FloatingChatApp — standalone chat window (320×420).
 *
 * Renders ChatPanel with full message/state management.
 * Header drag uses Electron window-level IPC (main-process cursor polling).
 * Close/minimize → IPC to main process to hide the window (state preserved).
 */
const FloatingChatApp: React.FC<FloatingChatAppProps> = ({ onClose }) => {
  const ei = window.electronAPI as ElectronAPI | undefined
  const { mood, setMood } = usePetState()
  const storeConversations = useStore(s => s.conversations)
  const storeAddMessageToConversation = useStore(s => s.addMessageToConversation)
  const storeSetActiveConversation = useStore(s => s.setActiveConversation)
  const proactiveBrief = useStore(s => s.proactiveBrief)
  const proactiveSuggestions = useStore(s => s.proactiveSuggestions)
  useProactiveEngine()
  // 第二档 #5：监听聊天窗内的划词 / 焦点变化，经 event.emit RPC 上报后端，触发管家消息
  useEmitInteractionEvents()

  // 第二档 #6：跨窗口接收悬浮球语音转写结果，注入聊天输入框（siyi:chat-draft）。
  // onVoiceDraft 覆盖"聊天窗已开"的即时场景；getPendingVoiceDraft 兜底"先开聊天窗、
  // 语音草稿尚未到达"的竞态（主进程会暂存 pending 直到聊天窗拉取）。
  useEffect(() => {
    const handler = (text: string) => {
      if (text && text.trim()) {
        window.dispatchEvent(new CustomEvent('siyi:chat-draft', { detail: text }))
      }
    }
    ei?.onVoiceDraft?.(handler)
    ei?.getPendingVoiceDraft?.()
      .then((t: string | null) => {
        if (t && t.trim()) window.dispatchEvent(new CustomEvent('siyi:chat-draft', { detail: t }))
      })
      .catch(() => {})
  }, [ei])

  const sendingRef = useRef(false)
  const abortRef = useRef<AbortController | null>(null)

  // Abort pending request on unmount
  useEffect(() => {
    return () => { abortRef.current?.abort() }
  }, [])

  // ── F-P0 会话共享（T-3）──
  // 悬浮窗不再凭空造一个孤立 session：默认跟随主窗的活动会话（激活接力），
  // 无活动会话时才落回自己的常驻会话 FLOATING_CONV_ID。
  const activeConversationId = useStore(s => s.activeConversationId)
  const [sessionId, setSessionId] = useState<string>(
    () => resolveSharedSessionId(useStore.getState().activeConversationId, FLOATING_CONV_ID),
  )

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

  // 跨窗共享面只有持久化层（两窗是独立渲染进程，内存态不互通）：
  // 挂载 + 窗口重新获得焦点时重读一次共享的活动会话 id —— 用户在主窗切换会话后，
  // 回到悬浮窗发消息就落到同一个 session（激活接力）。读取失败沿用当前值。
  useEffect(() => {
    let cancelled = false
    const sync = async () => {
      const shared = await readSharedActiveConversationId()
      if (cancelled || !shared) return
      setSessionId(prev => (prev === shared ? prev : shared))
    }
    void sync()
    window.addEventListener('focus', sync)
    return () => { cancelled = true; window.removeEventListener('focus', sync) }
  }, [])

  // Load messages from store conversation
  const [messages, setMessages] = useState<ChatMessage[]>(() => {
    const conv = useStore.getState().conversations.find(c => c.id === sessionId)
    if (conv && conv.messages.length > 0) {
      return conv.messages.map(toLocal)
    }
    return [{ role: 'assistant' as const, content: GREETING }]
  })

  // 会话 id 变更（主窗切了会话 → 经内存态/共享面接力）→ 载入该会话历史：
  // 两窗看到同一份消息流。本窗正在流式输出时不打断（避免把在途内容冲掉）。
  const loadedSessionRef = useRef(sessionId)
  useEffect(() => {
    if (loadedSessionRef.current === sessionId) return
    loadedSessionRef.current = sessionId
    if (sendingRef.current) return
    const conv = useStore.getState().conversations.find(c => c.id === sessionId)
    setMessages(conv && conv.messages.length > 0
      ? conv.messages.map(toLocal)
      : [{ role: 'assistant' as const, content: GREETING }])
  }, [sessionId])

  // 本窗 store 里的活动会话被外部改掉（同进程内的会话侧栏操作）→ 同步 sessionId。
  useEffect(() => {
    if (activeConversationId && activeConversationId !== sessionId) {
      setSessionId(activeConversationId)
    }
  }, [activeConversationId, sessionId])

  // F-P0：放行的元数据帧（tool_progress/tool_result）+ 本轮流起始时刻。
  // streamStartedAt 为 null 即非流式（WaitingStatus 不占位）。
  const [streamEvents, setStreamEvents] = useState<ChatStreamEvent[]>([])
  const [streamStartedAt, setStreamStartedAt] = useState<number | null>(null)

  // F-P0（T-2）：R4 审批队列（队列1）。高危操作走后端 sandbox 审批队列，悬浮窗此前无审批 UI
  // → 从悬浮窗发起的指令永远 pending（管理链死锁）。这里复用主窗同一数据层：
  // sandbox.list_pending 快照 + sandbox.pending_added/approved/rejected 事件驱动。
  const approvals = useSandboxApprovals()

  // F-P1-1（队列2，butler CONFIRM）：订阅 action.confirm_required → ConfirmationCard，
  // 批准 → confirmation.resolve {confirmation_id, approved, reason, always_allow}
  // （必带共享 session_id 的 chat.resume 续跑）。与队列1 是两套独立队列，
  // id 空间不互通（跨用 → expired_or_not_found 假象）——见 hooks/useConfirmApprovals。
  const confirmations = useConfirmApprovals({
    onApprovedResume: async (source) => {
      if (source === 'mcp:kernel') {
        // DSH 内核确认单（主窗内核选择器场景）：后端批准时已真实执行并缓存结果，
        // 但「批准即自动重发」需要携带原任务消息重新 submit（主窗 autoContinueKernel），
        // 悬浮窗无内核选择器、agent 路径产生的确认单 source 恒为 butler，
        // 故此处只如实提示，不重发（避免重复用户气泡与会话语义错乱）。
        setMessages((prev) => [...prev, {
          role: 'assistant' as const,
          content: '✅ 已批准该命令（后端已执行）。内核任务的自动收尾请在主对话窗继续。',
        }])
        return
      }
      // butler（M2 断点续跑）：批准后以**共享 session_id**（T-3）从 checkpoint
      // 恢复挂起任务，chunk 续跑到完成（FloatingChatApp 已传主窗活动会话 id）。
      const sid = sessionIdRef.current
      if (!sid) return
      const result = await resumeChatStream(sid)
      const text = result.text.trim()
      if (text) setMessages((prev) => [...prev, { role: 'assistant' as const, content: result.text }])
    },
  })

  // Sync messages back to store whenever they change
  const messagesRef = useRef(messages)
  messagesRef.current = messages
  useEffect(() => {
    // F-P0（T-3）：写回**共享会话**而非悬浮窗专属会话 —— 「激活接力」：
    // 悬浮窗一发消息，该会话即成活动会话，两窗消息流落到同一份历史。
    storeSetActiveConversation(sessionId)
    const conv = useStore.getState().conversations.find(c => c.id === sessionId)
    if (!conv) return
    // 前缀闸：切会话的当帧本地还挂着旧会话消息，按下标硬写会跨会话串写（见 sessionShare）。
    const start = sharedAppendStart(conv.messages, messages)
    if (start < 0) return
    for (let i = start; i < messages.length; i++) {
      const m = messages[i]
      storeAddMessageToConversation(sessionId, m.content, m.role)
    }
  }, [sessionId, messages, storeAddMessageToConversation, storeSetActiveConversation])

  // ── Chat persistence: save to backend on close, restore on mount ──
  // F-P0（T-3）：save/restore 一律带**共享会话 id**，与主窗落在后端同一会话上。
  const sessionIdRef = useRef(sessionId)
  sessionIdRef.current = sessionId

  const saveConversation = useCallback(async () => {
    const msgs = messagesRef.current
    if (msgs.length <= 1) return // no meaningful conversation
    try {
      const { getWsClient } = await import('@desk-notes/shared')
      const client = getWsClient()
      if (client.status !== 'connected') return
      await client.call('chat.save_messages', {
        session_id: sessionIdRef.current,
        title: '浮动助手',
        messages: msgs.map(m => ({ role: m.role, content: m.content })),
      })
    } catch (e) {
      console.warn('[floating-chat] saveConversation failed:', e)
    }
  }, [])

  // Auto-save periodically
  useEffect(() => {
    const timer = setInterval(saveConversation, AUTO_SAVE_INTERVAL)
    return () => clearInterval(timer)
  }, [saveConversation])

  // Save on close / minimize
  const doClose = useCallback(() => {
    saveConversation()
    if (onClose) { onClose(); return }
    ei?.hideFloatingChat?.()
  }, [ei, onClose, saveConversation])

  // Restore conversation from backend on mount / on session switch (with one retry)
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const attemptRestore = async (): Promise<boolean> => {
        try {
          const { getWsClient } = await import('@desk-notes/shared')
          const client = getWsClient()
          if (client.status !== 'connected') return false
          const conv = await client.call<{ title?: string; messages?: Array<{ role: string; content: string }> } | null>('chat.get_conversation', { session_id: sessionId })
          if (cancelled) return true
          if (conv?.messages && conv.messages.length > 0) {
            setMessages(prev => {
              if (prev.length > 1) return prev
              return conv.messages!.map(m => ({ role: m.role as 'user' | 'assistant', content: m.content }))
            })
          }
          return true
        } catch (e) {
          console.warn('[floating-chat] restoreConversation failed:', e)
          return false
        }
      }
      if (!(await attemptRestore())) {
        await new Promise<void>(resolve => { setTimeout(resolve, 1500) })
        if (!cancelled) await attemptRestore()
      }
    })()
    return () => { cancelled = true }
  }, [sessionId])

  const [theme, setTheme] = useState<'dark' | 'light'>('dark')

  // Chat panel size (resizable)
  const [chatSize, setChatSize] = useState({ w: 320, h: 420 })
  const chatSizeRef = useRef(chatSize)

  // Model/provider selection — init from persistent default
  const preferredModelProvider = useStore(s => s.preferredModelProvider)
  const preferredModelName = useStore(s => s.preferredModelName)
  const setPreferredModel = useStore(s => s.setPreferredModel)
  const [selectedProvider, setSelectedProvider] = useState(preferredModelProvider || 'auto')
  const [selectedModelName, setSelectedModelName] = useState<string | null>(preferredModelName)

  // ── Dynamic provider list from backend ──
  const { models: availableModels } = useModels()

  // Persist provider/model changes to store
  const handleProviderChange = useCallback((provider: string) => {
    setSelectedProvider(provider)
    if (provider === 'auto') {
      setPreferredModel(null, null)
    } else {
      const m = availableModels.find(m => m.provider === provider)?.model ?? null
      setPreferredModel(provider, m)
      setSelectedModelName(m)
    }
  }, [availableModels, setPreferredModel])

  // Build provider dropdown entries from real backend data
  const providerOptions = useMemo(() => {
    const entries: { id: string; label: string }[] = [{ id: 'auto', label: '自动' }]

    // Add each unique provider from backend, sorted: local first, then cloud
    const seen = new Set<string>()
    const localProvs: { id: string; label: string }[] = []
    const cloudProvs: { id: string; label: string }[] = []

    for (const m of availableModels) {
      if (seen.has(m.provider)) continue
      seen.add(m.provider)
      const entry = { id: m.provider, label: m.provider }
      // Check if this model's provider is local by looking at api_base
      if (m.api_base && (m.api_base.includes('11434') || m.api_base.includes('localhost'))) {
        localProvs.push(entry)
      } else {
        cloudProvs.push(entry)
      }
    }
    entries.push(...localProvs, ...cloudProvs)
    entries.push({ id: 'agent', label: 'Agent' })
    return entries
  }, [availableModels])

  // Get the first available model for a given provider name
  const getModelForProvider = useCallback((providerName: string): string | null => {
    const m = availableModels.find(m => m.provider === providerName)
    return m?.model ?? null
  }, [availableModels])

  // When selectedProvider changes, also set the model name
  useEffect(() => {
    if (selectedProvider === 'auto' || selectedProvider === 'agent') {
      setSelectedModelName(null)
    } else {
      setSelectedModelName(getModelForProvider(selectedProvider))
    }
  }, [selectedProvider, getModelForProvider])

  // ── Handlers ──

  const handleClose = useCallback(() => {
    doClose()
  }, [doClose])

  const handleMinimize = useCallback(() => {
    doClose()
  }, [doClose])

  // ── Backend events (WebSocket EventBridge) ──

  const { connected: backendConnected, lastEvent } = useBackendEvents()

  // Sync mood with backend mood.changed events
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

  // ── M1：browser.* 五事件订阅 → 注入小问气泡（悬浮层获得浏览器感知）──
  const lastBrowserEventRef = useRef<string>('')
  useBrowserEvents(useCallback((ev: BackendEvent) => {
    const p = ev.payload ?? {}
    const title = String(p.title ?? '').trim() || '网页'
    const url = String(p.url ?? '').trim()
    const key = `${ev.event_type}:${url}:${title}`
    if (lastBrowserEventRef.current === key) return
    lastBrowserEventRef.current = key
    const lines: Record<string, string> = {
      'browser.captured': `📥 网页已收件：《${title}》${url ? `\n${url}` : ''}`,
      'browser.summarized': `🌐 网页总结完成：《${title}》\n${String(p.summary ?? '').slice(0, 160)}`,
      'browser.note_created': `📝 笔记已沉淀：${title}`,
      'browser.task_created': `✅ 网页任务已创建：${title}`,
      'browser.reading_saved': `📚 已存到阅读列表：《${title}》`,
    }
    const content = lines[ev.event_type]
    if (!content) return
    setMessages(prev => (prev.some(m => m.content === content) ? prev : [...prev, { role: 'assistant' as const, content }]))
    setMood('happy')
    setTimeout(() => setMood('idle'), 4000)
  }, [setMood]))

  // ── F-4（M1⑤）：globalShortcut 注册冲突 → 悬浮层行内降级提示 ──
  useEffect(() => {
    if (!ei?.onGlobalShortcutConflict) return
    const cleanup = ei.onGlobalShortcutConflict((data) => {
      const failures = Array.isArray(data?.failures) ? data.failures : []
      if (failures.length === 0) return
      const lines = failures.map(f => `- ${f.accel}（${f.label}）未生效，可能被其他应用占用`).join('\n')
      setMessages(prev => [...prev, { role: 'assistant' as const, content: `⌨️ **快捷键降级提示**\n\n${lines}\n\n可在系统设置中释放占用后重启应用。` }])
      setMood('alert')
      setTimeout(() => setMood('idle'), 5000)
    })
    return () => { cleanup() }
  }, [ei, setMood])

  // Proactive suggestion → inject message when available
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

  // ── 单词到期提醒 → 由小问主动开口 ──
  const lastDueReminderRef = useRef<string>('')
  useEffect(() => {
    const ev = lastEvent('vocabulary.due_reminder')
    if (!ev?.payload) return
    const today = String(ev.payload.today ?? '')
    const dueCount = Number(ev.payload.due_count ?? 0)
    const message = String(ev.payload.message ?? '')
    if (!today || lastDueReminderRef.current === today || dueCount <= 0 || !message) return
    lastDueReminderRef.current = today
    setMood('alert')
    setTimeout(() => setMood('idle'), 5000)
    setMessages(prev => {
      if (prev.some(m => m.content.includes(message))) return prev
      return [...prev, { role: 'assistant' as const, content: `📚 ${message}` }]
    })
  }, [lastEvent, setMood])

  // ── 单词复习会话总结 → 由小问总结一句 ──
  const lastSessionSummaryRef = useRef<string>('')
  useEffect(() => {
    const ev = lastEvent('vocabulary.session.summary')
    if (!ev?.payload) return
    const total = Number(ev.payload.total ?? 0)
    const good = Number(ev.payload.good ?? 0)
    const forgot = Number(ev.payload.forgot ?? 0)
    const endedAt = String(ev.payload.ended_at ?? '')
    if (!endedAt || lastSessionSummaryRef.current === endedAt || total < 3) return
    lastSessionSummaryRef.current = endedAt
    const tone = forgot === 0
      ? `这一轮 ${total} 个全对，记忆很稳。`
      : forgot >= total / 2
        ? `这轮 ${total} 个里忘了 ${forgot} 个，明天再过一遍这部分？`
        : `这轮 ${total} 个，记得 ${good} 个、忘了 ${forgot} 个，节奏可以。`
    setMessages(prev => {
      if (prev.some(m => m.content.includes(tone))) return prev
      return [...prev, { role: 'assistant' as const, content: `🧠 ${tone}` }]
    })
  }, [lastEvent])

  // ── 阅读生词提议 → 注入候选词列表，让用户一键加入 ──
  const lastProposedArticleRef = useRef<string>('')
  useEffect(() => {
    const ev = lastEvent('reading.words_proposed')
    if (!ev?.payload) return
    const articleId = String(ev.payload.article_id ?? '')
    const title = String(ev.payload.title ?? '')
    const wordsRaw = (ev.payload.words ?? []) as Array<{ word: string; phonetic?: string; meaning?: string; context?: string }>
    if (!articleId || lastProposedArticleRef.current === articleId || !wordsRaw.length) return
    lastProposedArticleRef.current = articleId
    // 用 markdown 注入；用户在聊天里直接看到（点击"加入生词本"按钮路径仍走 ClipboardPopup 的 add_word 行为）
    const lines = wordsRaw.slice(0, 8).map(w => {
      const segs = [`- **${w.word}**`]
      if (w.phonetic) segs.push(`/${w.phonetic}/`)
      if (w.meaning) segs.push(`— ${w.meaning}`)
      return segs.join(' ')
    })
    const content = [
      `📖 刚保存的「${title.slice(0, 30)}${title.length > 30 ? '…' : ''}」里挑了 ${wordsRaw.length} 个值得收的词：`,
      ...lines,
      '',
      '需要我帮你一键加入生词本吗？回复"加入"或在词库面板里挑选。',
    ].join('\n')
    setMessages(prev => {
      if (prev.some(m => m.content.includes(`reading-words-${articleId}`))) return prev
      return [...prev, { role: 'assistant' as const, content: `${content}\n\n<!-- reading-words-${articleId} -->` }]
    })
  }, [lastEvent])

  // Inject pending suggestion as assistant message
  useEffect(() => {
    if (pendingSuggestion) {
      setMessages(prev => {
        if (prev.some(m => m.content.includes(pendingSuggestion.title))) return prev
        return [...prev, {
          role: 'assistant' as const,
          content: `💡 **${pendingSuggestion.title}**\n\n${pendingSuggestion.description}\n\n需要我详细说说吗？`,
        }]
      })
      setPendingSuggestion(null)
    }
  }, [pendingSuggestion])

  // Clipboard monitoring is handled by the separate popup window
  // (ClipboardPopupApp) which appears at cursor position with action buttons.

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

  // ── AI send ──

  const handleSend = useCallback(async (
    text: string,
    // Task #9 对话附件（ChatPanel 采集，wire 形态；仅 Agent(WS) 路径消化——
    // 文本内联/图片 vision 转描述都发生在后端 chat_paths/attachments.py）
    attachments?: import('../components/chat/chatAttachments').WireAttachment[],
  ) => {
    if (sendingRef.current) return
    sendingRef.current = true
    // F-P0：新一轮开始 → 清空上一轮执行流行（防陈旧行误导「内核正在跑」）。
    setStreamEvents([])
    try {
    const userMsg: ChatMessage = {
      role: 'user',
      content: attachments?.length ? `${text}\n📎 ${attachments.map((a) => a.name).join('、')}` : text,
    }
    setMessages(prev => [...prev, userMsg])

    // QWebChannel bridge path (when loaded in PyQt6 QWebEngineView)
    const bridge = window.bridge
    if (bridge?.sendMessage) {
      bridge.sendMessage(text)
      return
    }

    // Agent path: use WebSocket RPC for tool-calling capable response
    if (selectedProvider === 'agent') {
      setMessages(prev => [...prev, { role: 'assistant', content: '' }])
      // F-P0：开流即起表（前端派生计时，WaitingStatus 用）。
      setStreamStartedAt(Date.now())
      try {
        const { getWsClient } = await import('@desk-notes/shared')
        const client = getWsClient()
        if (client.status !== 'connected') {
          setMessages(prev => {
            const copy = [...prev]
            copy[copy.length - 1] = { role: 'assistant', content: '后端未连接，Agent 模式不可用。' }
            return copy
          })
          return
        }
        // V1 修复（2026-09-15）：改用 streamTyped 并按帧类型分流。
        // 原写法 `stream<string>` 只 yield data、丢弃 type（见 ws-client.ts:310 onChunk），
        // 导致 notice / tool_progress / planner 等元数据帧被当正文追加 →
        // 悬浮窗输出复读膨胀（用户实测："当前您有 N 篇笔记…" 逐字符自我叠加）。
        // 规则：只有 type==='chunk' 进正文；error 走错误态；其余元数据帧一律不写入正文。
        // F-P0（2026-09-17）：放行 tool_progress / tool_result 到执行流状态区——
        // 内核真跑了工具必须看得见（此前被静默丢弃 = 执行全盲）。
        // T-3：带上与主窗同一 session_id（激活接力，见 ./sessionShare.ts）。
        for await (const frame of client.streamTyped('chat.send', {
          session_id: sessionIdRef.current,
          message: text,
          provider: 'agent',
          // Task #9：附件走后端 enrich（与主窗 useChatStream 同参数面）
          ...(attachments?.length ? { attachments } : {}),
        })) {
          // 帧分流规则见 ./frameRouting.ts（纯函数 + 单测锁定）：
          // 仅 chunk/error 进正文；tool_progress/tool_result 进执行流；
          // notice / planner / done 等元数据帧一律不写入正文（此前未分流导致输出复读膨胀）。
          if (isExecutionFrame(frame)) {
            const event: ChatStreamEvent = { type: frame.type, data: frame.data }
            setStreamEvents(prev => [...prev, event])
            continue
          }
          setMessages(prev => applyFrameToMessages(prev, frame))
        }
      } catch (err: unknown) {
        setMessages(prev => {
          const copy = [...prev]
          copy[copy.length - 1] = {
            role: 'assistant',
            content: `[Agent 错误] ${err instanceof Error ? err.message : '请求失败'}`,
          }
          return copy
        })
      } finally {
        setStreamStartedAt(null)
      }
      return
    }

    const aiEnabled = useStore.getState().aiEnabled
    // Task #9：非 Agent（纯 prompt）路径不经后端 chat.send，无附件解析能力 ——
    // 诚实告知而不是静默丢。
    if (attachments?.length) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `📎 附件（${attachments.map((a) => a.name).join('、')}）仅在 Agent 模式下可被解析，本轮未发送。`,
      }])
    }
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
      // Route to preferred provider + model via ws-client
      const wsProvider = provider as { preferredProvider?: string; preferredModel?: string; extraParams?: Record<string, unknown> }
      // F-P0（T-3）：普通（非 Agent）路径的 chat.send 也带共享 session_id，
      // 否则同一句话在两条路径下落进不同会话，记忆依旧割裂。
      wsProvider.extraParams = { session_id: sessionIdRef.current }
      if (selectedProvider !== 'auto') {
        wsProvider.preferredProvider = selectedProvider
        if (selectedModelName) {
          wsProvider.preferredModel = selectedModelName
        }
      }
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

      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller
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
  }, [messages, backendConnected, selectedProvider, selectedModelName])

  // ── Apply theme ──
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    document.body.style.background = 'transparent'
    document.documentElement.style.background = 'transparent'
  }, [theme])

  return (
    <div className="w-screen h-screen flex flex-col" style={{ background: 'transparent', overflow: 'hidden' }}>
      <ChatPanel
        mood={mood}
        theme={theme}
        onThemeChange={setTheme}
        onMinimize={handleMinimize}
        onClose={handleClose}
        onSend={handleSend}
        messages={messages}
        windowDrag={true}
        selectedProvider={selectedProvider}
        onProviderChange={handleProviderChange}
        providers={backendConnected ? providerOptions : FALLBACK_PROVIDERS}
        initWidth={chatSizeRef.current.w}
        initHeight={chatSizeRef.current.h}
        onResize={(w, h) => {
          setChatSize({ w, h })
          chatSizeRef.current = { w, h }
          // Resize the actual BrowserWindow
          ei?.resizeWindow?.(w, h)
        }}
        streaming={streamStartedAt !== null}
        streamStartedAt={streamStartedAt}
        streamEvents={streamEvents}
        pendingApprovals={approvals.pending}
        onApproveApproval={approvals.approve}
        onRejectApproval={approvals.reject}
        pendingConfirmations={confirmations.pending}
        onApproveConfirmation={confirmations.approve}
        onRejectConfirmation={confirmations.reject}
      />
      {/* V1-7：划词 → 浮窗动作菜单（pattern.execute） */}
      <SelectionBubble />
    </div>
  )
}

export default FloatingChatApp
