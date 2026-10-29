import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react'

import { useStore } from '../../store/useStore'
import { getAIProvider, getWsClient, DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL } from '../../utils/ai'
import { loadBackendConversations } from '../../utils/chatSync'
import { useTranslation } from '../../i18n'
import { WsAIProvider, type ModelInfo, type StreamChunkType } from '@desk-notes/shared'
import { KERNEL_DEEP, buildKernelSendParam } from '@desk-notes/shared/utils/kernelVocabulary'
import { useSandbox } from '../../FloatingBubble/useSandbox'

import {
  type ChatMessage, type StudySubject,
  getConversationTitle, _chunkTypeIcon, groupByDate,
  getStudySubjects, usePreferredModel,
} from './chatHelpers'
import { ChatHistorySidebar } from './ChatHistorySidebar'
import { ChatToolbar } from './ChatToolbar'
import { ChatContextPanel } from './ChatContextPanel'
import { ChatMessageList } from './ChatMessageList'
import { ChatInput } from './ChatInput'

// ── Component ─────────────────────────────────────────────────────────────

const ChatModule: React.FC = () => {
  const aiEnabled = useStore(s => s.aiEnabled)
  const conversations = useStore(s => s.conversations)
  const activeConversationId = useStore(s => s.activeConversationId)
  const createConversation = useStore(s => s.createConversation)
  const deleteConversation = useStore(s => s.deleteConversation)
  const setActiveConversation = useStore(s => s.setActiveConversation)
  const addMessage = useStore(s => s.addMessage)
  const clearConversation = useStore(s => s.clearConversation)
  const setConversationTitle = useStore(s => s.setConversationTitle)
  const { t, locale } = useTranslation()
  const studySubjects = useMemo(() => getStudySubjects(), [locale])

  const [input, setInput] = useState(() => sessionStorage.getItem('siyi-chat-draft-desktop') || '')
  const [loading, setLoading] = useState(false)
  const [streamingContent, setStreamingContent] = useState('')
  const [showHistory, setShowHistory] = useState(false)
  const [showMorningBrief, setShowMorningBrief] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [backendConnected, setBackendConnected] = useState(false)
  const [ollamaOnline, setOllamaOnline] = useState(true) // optimistic default
  const [lastUserMessage, setLastUserMessage] = useState('')
  const [selectedModel, setSelectedModel] = useState<ModelInfo | null>(null)
  const preferredModel = usePreferredModel()
  const setPreferredModel = useStore(s => s.setPreferredModel)
  // ── Multi-Agent & tool-calling progress ──
  // agentStatus collects metadata chunks (tool_progress / planner / subagent_*
  // / multi_agent_summary) emitted by the backend during a stream. Each entry
  // is rendered as a small inline status line below the streaming bubble.
  const [agentStatus, setAgentStatus] = useState<string[]>([])
  // multiAgentMode toggles whether chat.send is dispatched with mode="multi_agent"
  // (Planner + Worker orchestration). Off by default; user opts in per session.
  const [multiAgentMode, setMultiAgentMode] = useState(false)
  // deepMode 强制本次发送走 DSH 深度档（params.kernel=deep，无内核时后端自动降级），词汇见 kernelVocabulary
  const [deepMode, setDeepMode] = useState(false)

  // Initialize selectedModel from persistent default once on mount
  const initRef = useRef(false)
  useEffect(() => {
    if (!initRef.current && preferredModel) {
      setSelectedModel(preferredModel)
      initRef.current = true
    }
  }, [preferredModel])

  const handleModelSelect = useCallback((model: ModelInfo | null) => {
    setSelectedModel(model)
    setPreferredModel(model?.provider ?? null, model?.model ?? null)
  }, [setPreferredModel])
  const [searchMode, setSearchMode] = useState(false)
  const [studySubject, setStudySubject] = useState<StudySubject>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const prevConvIdRef = useRef<string | null>(null)
  const lastSavedCountRef = useRef<number>(0)
  const anchorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const sandbox = useSandbox()
  const [showSandbox, setShowSandbox] = useState(false)

  useEffect(() => {
    const client = getWsClient()
    setBackendConnected(client.status === 'connected')
    return client.onStatusChange((s) => setBackendConnected(s === 'connected'))
  }, [])

  // ── Ollama health check ──
  useEffect(() => {
    let cancelled = false
    const check = async () => {
      try {
        const res = await fetch(`${DEFAULT_LOCAL_API_URL}/api/tags`, { signal: AbortSignal.timeout(3000) })
        if (!cancelled) setOllamaOnline(res.ok)
      } catch {
        if (!cancelled) setOllamaOnline(false)
      }
    }
    check()
    // Re-check every 30s
    const timer = setInterval(check, 30000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [])

  // Sync chat history from backend when connected
  useEffect(() => {
    if (!backendConnected) return
    const syncHistory = async () => {
      const existingIds = new Set(conversations.map(c => c.id))
      const imported = await loadBackendConversations(getWsClient(), conversations)
      for (const conv of imported) {
        if (!existingIds.has(conv.id)) {
          // Add to store — use store.setState to batch
          useStore.setState(s => ({
            conversations: [...s.conversations, conv],
          }))
        }
      }
    }
    syncHistory()
  }, [backendConnected, conversations])

  // Save conversation anchor on switch or 5min idle
  const saveConversationAnchor = useCallback((convId: string, msgs: ChatMessage[]) => {
    if (!backendConnected || msgs.length < 2) return
    if (prevConvIdRef.current === convId) return
    prevConvIdRef.current = convId
    const client = getWsClient()
    client.call('memory.save_conversation_anchor', {
      messages: msgs.slice(-20), session_id: convId,
    }).catch(() => {})
  }, [backendConnected])

  // Detect conversation switch → save previous conversation
  useEffect(() => {
    const prevId = prevConvIdRef.current
    const currentId = activeConversationId
    if (prevId && prevId !== currentId) {
      const prevConv = conversations.find(c => c.id === prevId)
      if (prevConv && prevConv.messages.length >= 2) {
        saveConversationAnchor(prevId, prevConv.messages)
      }
    }
    if (currentId) prevConvIdRef.current = currentId
  }, [activeConversationId, conversations, saveConversationAnchor])

  // 5min idle timer — save if no new messages
  useEffect(() => {
    if (!activeConversationId) return
    const conv = conversations.find(c => c.id === activeConversationId)
    if (!conv || conv.messages.length < 2) return
    if (anchorTimerRef.current) clearTimeout(anchorTimerRef.current)
    anchorTimerRef.current = setTimeout(() => {
      saveConversationAnchor(activeConversationId, conv.messages)
    }, 300000) // 5 min
    return () => { if (anchorTimerRef.current) clearTimeout(anchorTimerRef.current) }
  }, [activeConversationId, conversations, saveConversationAnchor])

  // Save on unmount
  useEffect(() => {
    return () => {
      const id = activeConversationId
      if (id && prevConvIdRef.current === id) {
        const conv = useStore.getState().conversations.find(c => c.id === id)
        if (conv && conv.messages.length >= 2) {
          const client = getWsClient()
          client.call('memory.save_conversation_anchor', {
            messages: conv.messages.slice(-20), session_id: id,
          }).catch(() => {})
        }
      }
    }
  }, [activeConversationId])

  const activeConv = useMemo(
    () => conversations.find(c => c.id === activeConversationId) || null,
    [conversations, activeConversationId]
  )

  const messages = activeConv?.messages || []

  const hasMessages = messages.length > 0 || streamingContent.length > 0

  // Auto-create conversation if none active
  useEffect(() => {
    if (!activeConversationId && conversations.length === 0) {
      const id = createConversation()
      setActiveConversation(id)
    } else if (!activeConversationId && conversations.length > 0) {
      setActiveConversation(conversations[conversations.length - 1].id)
    }
  }, [])

  useEffect(() => {
    if (!activeConv && conversations.length > 0) {
      setActiveConversation(conversations[conversations.length - 1].id)
    }
  }, [activeConv, conversations, setActiveConversation])

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => { scrollToBottom() }, [messages, streamingContent, scrollToBottom])

  const handleInputChange = (val: string) => {
    setInput(val)
    sessionStorage.setItem('siyi-chat-draft-desktop', val)
    // 自动增高：经 ChatInput 转成 string 后，文本框元素经由 inputRef 访问
    const el = inputRef.current
    if (el) {
      el.style.height = 'auto'
      el.style.height = Math.min(el.scrollHeight, 120) + 'px'
    }
  }

  const handleSelectMemory = useCallback((summary: string) => {
    setInput(prev => {
      const chip = t('chatModule.memoryChipFmt', { summary })
      const next = prev ? `${prev} ${chip}` : chip
      sessionStorage.setItem('siyi-chat-draft-desktop', next)
      return next
    })
    inputRef.current?.focus()
  }, [t])

  const ensureConversation = useCallback(() => {
    let id = activeConversationId
    if (!id) {
      id = createConversation()
    }
    return id
  }, [activeConversationId, createConversation])

  const handleSend = async () => {
    const text = input.trim()
    if (!text || loading) return
    sessionStorage.removeItem('siyi-chat-draft-desktop')
    setInput('')
    setShowMorningBrief(false)
    setLastUserMessage(text)

    const convId = ensureConversation()
    addMessage(text, 'user')

    setLoading(true)
    setStreamingContent('')
    setAgentStatus([])

    abortRef.current = new AbortController()
    const signal = abortRef.current.signal

    // ── URL auto-learning (after loading state, non-blocking) ──
    let urlLearningContext = ''
    const urlMatch = text.match(/https?:\/\/[^\s]+/)
    if (urlMatch && text.trim().length < 200) {
      try {
        const res = await getWsClient().call<{ title?: string; summary?: string; key_points?: string[] }>('url_learning.learn', { url: urlMatch[0], timeout: 30 })
        if (!signal.aborted && res?.title) {
          const summaryPart = res.summary ? t('chatModule.urlLearningSummaryFmt', { summary: res.summary }) : ''
          const keyPointsPart = res.key_points?.length
            ? t('chatModule.urlLearningKeyPointsHeader') +
              res.key_points.map((p: string) => t('chatModule.urlLearningKeyPointPrefix') + p).join('\n')
            : ''
          urlLearningContext = t('chatModule.urlLearningContextFmt', {
            title: res.title,
            summary: summaryPart,
            keyPoints: keyPointsPart,
          })
        }
      } catch (e) { console.warn('[chat] url_learning.learn failed:', e) }
    }
    // Get current messages from store for context
    const currentMessages = useStore.getState().conversations.find(c => c.id === convId)?.messages || []
    const contextMessages = currentMessages.slice(-8)

    // ── Extended context for backend compression ──
    // Send a wider window of messages as separate context for backend to compress
    // into a summary, giving the model more background without bloating the prompt.
    const extendedContext = backendConnected && currentMessages.length > 8
      ? currentMessages.slice(-20).map(m => ({ role: m.role, content: m.content }))
      : undefined

    try {
      const effectiveConfig = backendConnected
        ? { enabled: true, apiUrl: DEFAULT_LOCAL_API_URL, model: DEFAULT_LOCAL_MODEL, protocol: 'ollama' as const, autoTagging: false, strategy: 'ws' as const }
        : { enabled: aiEnabled, apiUrl: DEFAULT_LOCAL_API_URL, model: DEFAULT_LOCAL_MODEL, protocol: 'ollama' as const, autoTagging: false }
      const provider = getAIProvider(effectiveConfig)

      // Pass preferred model to WS provider for backend routing
      const effectiveModel = selectedModel ?? preferredModel
      if (provider instanceof WsAIProvider && effectiveModel) {
        provider.preferredProvider = effectiveModel.provider
        provider.preferredModel = effectiveModel.model
      }

      // ── Extended context for backend compression ──
      if (provider instanceof WsAIProvider && extendedContext) {
        provider.extraParams = { context_messages: extendedContext, context_max_tokens: 4000 }
      }

      // ── Multi-Agent mode ──
      // When toggled on, dispatch chat.send with mode="multi_agent" so the
      // backend routes through MultiAgentOrchestrator (Planner + Worker).
      if (provider instanceof WsAIProvider && multiAgentMode) {
        provider.extraParams = {
          ...(provider.extraParams || {}),
          mode: 'multi_agent',
        }
      }

      // ── Deep kernel mode ──
      // 强制本次 chat.send 走 DSH 深度档（§2.5.1 成本档：params.kernel=deep → siyi-deep profile）；
      // 当无网关内核时后端 _get_gateway 返回 None，run_gateway_path 返回 False，
      // 自动降级到 butler，符合四级降级链设计。
      // F 项：深度模式发送成本分级词汇 deep（后端经 resolve_dsh_profile 映射
      // siyi-deep profile），替代旧的布尔 kernel:true（保持 L1 强制语义不变）。
      // A1②：词汇取自共享模块 kernelVocabulary，与主窗 ChatView selector 单一来源一致。
      if (provider instanceof WsAIProvider && deepMode) {
        provider.extraParams = {
          ...(provider.extraParams || {}),
          ...buildKernelSendParam(KERNEL_DEEP),
        }
      }

      // ── Web search mode ──
      // When backend is connected, delegate search to backend (runs concurrently with classify_intent)
      // When disconnected, do inline search before building prompt
      let searchContext = ''
      if (searchMode && text.trim()) {
        if (backendConnected && provider instanceof WsAIProvider) {
          provider.extraParams = {
            ...provider.extraParams,
            search_query: text.trim(),
          }
        } else {
          try {
            const ws = getWsClient()
            const searchRes = await ws.call<{ results?: { title: string; snippet?: string; content?: string }[] }>('web.search', { query: text.trim() })
            const results = searchRes?.results || []
            if (results.length > 0) {
              searchContext = t('chatModule.searchContextHeader') +
                results.slice(0, 5).map((r, i) =>
                  `[${i + 1}] ${r.title}\n${r.snippet || r.content || ''}`
                ).join('\n\n') +
                t('chatModule.searchContextFooter')
            }
          } catch (e) {
            console.warn('[chat] web.search failed:', e)
          }
        }
      }

      // ── Context-aware hint ──
      const buildContextHint = (): string => {
        const st = useStore.getState()
        const { currentModule, selectedNoteId, selectedTodoId } = st
        const allTodos = st.todos

        if (currentModule === 'notes' && selectedNoteId) {
          const note = st.floatingNotes.find(n => n.id === selectedNoteId)
          if (note) {
            const content = (note.content || '').slice(0, 800)
            return t('chatModule.contextHintNoteFmt', { title: note.title, content })
          }
        }

        if (currentModule === 'todo' || currentModule === 'calendar') {
          const undone = allTodos.filter(t => !t.done).slice(0, 15)
          if (selectedTodoId) {
            const todo = allTodos.find(t => t.id === selectedTodoId)
            if (todo) return t('chatModule.contextHintTodoSelectedFmt', { text: todo.text })
          }
          if (undone.length > 0) {
            return t('chatModule.contextHintTodoListHeader') +
              undone.map(td => `- ${td.text}${td.dueDate ? t('chatModule.contextHintTodoDueFmt', { date: new Date(td.dueDate).toLocaleDateString() }) : ''}`).join('\n') +
              t('chatModule.contextHintTodoListFooter')
          }
        }

        if (currentModule === 'info') return t('chatModule.contextHintInfo')
        if (currentModule === 'knowledge-graph') return t('chatModule.contextHintKnowledgeGraph')
        return ''
      }

      const contextHint = buildContextHint()
      const studyPrompt = studySubject ? studySubjects[studySubject]?.prompt : ''
      const systemPrompt = `${t('chatModule.systemPrompt')}${studyPrompt ? '\n\n' + studyPrompt : ''}`
      const context = contextMessages.map(m => `${m.role === 'user' ? t('chatModule.roleUser') : t('chatModule.roleAssistant')}：${m.content}`).join('\n')
      const fullPrompt = `${systemPrompt}\n\n${contextHint}${urlLearningContext}${searchContext}${t('chatModule.historyLabel')}\n${context}\n\n${t('chatModule.userMessageOpen')}\n${text}\n${t('chatModule.userMessageClose')}\n${t('chatModule.assistantOpen')}`

      const fullResponse = await provider.processText(
        'chat',
        fullPrompt,
        (chunk: string) => {
          setStreamingContent(chunk)
        },
        abortRef.current.signal,
        // onTypedChunk: receive metadata chunks (tool_progress / planner /
        // subagent_start / subagent_done / multi_agent_summary) from the
        // backend and append them to the agentStatus panel below the bubble.
        (type: StreamChunkType, data: string) => {
          if (!data) return
          // Prefix with an icon based on chunk type for visual scanning.
          const icon = _chunkTypeIcon(type)
          setAgentStatus(prev => [...prev, `${icon} ${data}`])
        },
      )
      // Guard: empty response from all AI backends
      if (!fullResponse) {
        addMessage(t('ai.askFailed'), 'assistant')
      } else {
        addMessage(fullResponse, 'assistant')
      }
      setStreamingContent('')
      setAgentStatus([])
    } catch (err: any) {
      if (err?.name === 'AbortError') {
        addMessage(t('chatModule.stopped'), 'assistant')
        setStreamingContent('')
        setAgentStatus([])
        return
      }
      addMessage(t('ai.askFailed'), 'assistant')
      setStreamingContent('')
      setAgentStatus([])
    } finally {
      setSearchMode(false)
      setLoading(false)
      abortRef.current = null
      // Auto-title from first user message
      const msgs = useStore.getState().conversations.find(c => c.id === convId)?.messages || []
      if (msgs.length === 2) {
        setConversationTitle(convId, getConversationTitle(msgs))
      }
      // Sync conversation to backend (skip if no new messages since last save)
      if (backendConnected) {
        const conv = useStore.getState().conversations.find(c => c.id === convId)
        if (conv && conv.messages.length !== lastSavedCountRef.current) {
          lastSavedCountRef.current = conv.messages.length
          getWsClient().call('chat.save_messages', {
            session_id: convId,
            title: conv.title || getConversationTitle(conv.messages),
            messages: conv.messages.map(m => ({ role: m.role, content: m.content, timestamp: m.timestamp })),
          }).catch((e: unknown) => console.warn('[sync] chat.save_messages', e instanceof Error ? e.message : e))
        }
      }
    }
  }

  // Re-run title update when streaming completes
  useEffect(() => {
    if (!loading && streamingContent && activeConversationId) {
      const msgs = useStore.getState().conversations.find(c => c.id === activeConversationId)?.messages || []
      if (msgs.filter(m => m.role === 'assistant').length === 1) {
        setConversationTitle(activeConversationId, getConversationTitle(msgs))
      }
    }
  }, [loading, streamingContent, activeConversationId])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleStop = () => {
    abortRef.current?.abort()
  }

  const handleNewChat = () => {
    createConversation()
    setShowHistory(false)
    setShowMorningBrief(true)
  }

  const handleSelectConversation = (id: string) => {
    setActiveConversation(id)
    setShowHistory(false)
    setShowMorningBrief(false)
  }

  const handleDeleteConversation = (id: string) => {
    import('../../utils/chatSync').then(m => m.markConversationDeleted(id)).catch(() => {})
    getWsClient()?.call('chat.delete_conversation', { session_id: id }).catch(() => {})
    deleteConversation(id)
  }

  const handleExportJSON = () => {
    if (!activeConv) return
    const blob = new Blob([JSON.stringify(activeConv, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `${activeConv.title.replace(/[^a-zA-Z0-9一-龥]/g, '_')}.json`
    a.click(); URL.revokeObjectURL(url)
  }

  const handleExportText = () => {
    if (!activeConv) return
    const text = activeConv.messages.map(m =>
      `${m.role === 'user' ? t('chatModule.exportUser') : t('chatModule.exportAssistant')}\n${m.content}`
    ).join('\n\n---\n\n')
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `${activeConv.title.replace(/[^a-zA-Z0-9一-龥]/g, '_')}.txt`
    a.click(); URL.revokeObjectURL(url)
  }

  const handleToggleStudySubject = (k: string) => {
    if (k === '__none__') {
      setStudySubject(null)
    } else {
      setStudySubject(studySubject === (k as StudySubject) ? null : (k as StudySubject))
    }
  }

  const filteredConversations = useMemo(() => {
    if (!searchQuery.trim()) return conversations
    const q = searchQuery.toLowerCase()
    return conversations.filter(c =>
      (c.title || '').toLowerCase().includes(q) ||
      c.messages.some(m => (m.content || '').toLowerCase().includes(q))
    )
  }, [conversations, searchQuery])

  const grouped = useMemo(() => groupByDate(filteredConversations), [filteredConversations])

  const displayMessages = hasMessages ? messages : []
  const displayStream = streamingContent

  return (
    <div className="flex-1 flex min-h-0 overflow-hidden relative">
      <ChatHistorySidebar
        show={showHistory}
        onClose={() => setShowHistory(false)}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        grouped={grouped}
        filteredCount={filteredConversations.length}
        activeConversationId={activeConversationId}
        onNewChat={handleNewChat}
        onSelect={handleSelectConversation}
        onDelete={handleDeleteConversation}
      />

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <ChatToolbar
          showHistory={showHistory}
          onToggleHistory={() => setShowHistory(v => !v)}
          onNewChat={handleNewChat}
          backendConnected={backendConnected}
          selectedModel={selectedModel}
          onModelSelect={handleModelSelect}
          loading={loading}
          activeConv={activeConv}
          hasMessages={hasMessages}
          onExportJSON={handleExportJSON}
          onExportText={handleExportText}
          onClear={() => { if (activeConv) clearConversation(activeConv.id) }}
        />

        <ChatContextPanel
          showHistory={showHistory}
          studySubject={studySubject}
          onToggleStudySubject={handleToggleStudySubject}
          studySubjects={studySubjects}
          backendConnected={backendConnected}
          ollamaOnline={ollamaOnline}
          lastUserMessage={lastUserMessage}
          input={input}
          onSelectMemory={handleSelectMemory}
          sandbox={sandbox}
          showSandbox={showSandbox}
          onToggleSandbox={() => setShowSandbox(v => !v)}
        />

        <ChatMessageList
          showMorningBrief={showMorningBrief}
          hasMessages={hasMessages}
          showHistory={showHistory}
          messages={displayMessages}
          loading={loading}
          streamingContent={displayStream}
          agentStatus={agentStatus}
          messagesEndRef={messagesEndRef}
        />

        <ChatInput
          input={input}
          onInputChange={handleInputChange}
          onKeyDown={handleKeyDown}
          loading={loading}
          onStop={handleStop}
          onSend={handleSend}
          searchMode={searchMode}
          onToggleSearch={() => setSearchMode(v => !v)}
          multiAgentMode={multiAgentMode}
          onToggleMultiAgent={() => setMultiAgentMode(v => !v)}
          deepMode={deepMode}
          onToggleDeep={() => setDeepMode(v => !v)}
          inputRef={inputRef}
        />
      </div>
    </div>
  )
}

export default ChatModule
