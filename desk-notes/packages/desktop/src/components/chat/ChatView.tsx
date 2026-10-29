import { useEffect, useMemo, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { motion } from 'framer-motion'
import Icon from '../Common/Icon'

import { ChatComposer, type ComposerModelOption } from './ChatComposer'
import { ChatMessage } from './ChatMessage'
import { AiAvatar } from './AiAvatar'
import { ArtifactRenderer } from './ArtifactRenderer'
import { normalizeChatArtifacts, type ChatArtifact } from './artifacts'
import {
  DEFAULT_KERNEL_SELECTION,
  KERNEL_STORAGE_KEY,
  buildKernelSendParam,
  isKernelSelection,
  normalizeKernelSelection,
} from '@desk-notes/shared/utils/kernelVocabulary'
import { useChatStream, resumeChatStream, type ChatHistoryItem, type ChatModelSelection, type ChatStreamEvent } from './useChatStream'
import { getWsClient } from '../../utils/ai'
import { kernelSupportsResume } from '../../utils/kernelCapabilities'
import { formatConversationLabel } from '@desk-notes/shared'
import { describeToolError } from '../../utils/toolErrorHints'
import { useStore } from '../../store/useStore'
import { AgentContextPanel } from './AgentContextPanel'
import { ConversationSidebar } from './ConversationSidebar'
import { IdeaPanel } from './IdeaPanel'
import { normalizeAgentEvents } from './agentEvents'
import { ToolTimeline } from './ToolTimeline'
import { ChatNoticeRow, NoticeTechnical, parseStreamNotice } from './StreamNotice'
import { WaitingStatus } from './WaitingStatus'
import { useWaitingStage } from './useWaitingStage'
import { ApprovalCard } from './ApprovalCard'
import { useSandboxApprovals } from '../../hooks/useSandboxApprovals'
import { loadBackendConversations } from '../../utils/chatSync'
import { useTranslation } from '../../i18n'

interface ModelProvider {
  provider: string
  models?: unknown
  enabled?: boolean
}

function normalizeModelOptions(providers: ModelProvider[]): ComposerModelOption[] {
  return providers.flatMap((provider) => {
    if (!provider || typeof provider.provider !== 'string' || provider.enabled === false || !Array.isArray(provider.models)) return []
    return provider.models
      .filter((model): model is string => typeof model === 'string' && model.trim().length > 0)
      .map((model) => ({ provider: provider.provider, model, label: `${provider.provider} / ${model}` }))
  })
}

// C3：会话列表时间标签统一走 datetime 工具层（今天 HH:mm / 今年 M/D HH:mm / 跨年 YYYY/M/D）
function formatConversationTime(ts: number): string {
  if (!ts) return ''
  return formatConversationLabel(ts)
}

/** 会话标题派生：取第一条用户消息前 30 字 */
function deriveConversationTitle(messages: Array<{ role: string; content: string }>): string {
  for (const m of messages) {
    if (m.role === 'user' && m.content.trim()) {
      const text = m.content.trim()
      return text.length > 30 ? text.slice(0, 30) + '…' : text
    }
  }
  return '新对话'
}

/** 危险操作确认卡片数据 */
interface PendingConfirmation {
  confirmation_id: string
  action_id: string
  args: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected'
  /** 发起来源：butler（批准后可 checkpoint 续跑）| mcp:kernel（批准后需内核重新下发） */
  source?: string
}

/** P1c 空状态能力快捷入口：点击仅填入输入框，由用户确认后发送（不自动发送） */
const QUICK_PROMPTS = [
  { label: '总结今日资讯', prompt: '请帮我总结今天的资讯要点，并按主题归类梳理。' },
  { label: '整理最新笔记', prompt: '帮我整理最新的笔记，合并同类项并提炼出重点。' },
  { label: '列出待办并排序', prompt: '列出当前所有待办事项，并按优先级排序。' },
  { label: '生成本周工作回顾', prompt: '请生成本周工作回顾，包含已完成事项、成果与待跟进项。' },
]

export function ChatView() {
  const [messages, setMessages] = useState<ChatHistoryItem[]>(() => {
    const store = useStore.getState()
    const conv = store.conversations.find(c => c.id === store.activeConversationId)
    if (!conv?.messages.length) return []
    return [...conv.messages]
      .sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))
      .map(({ role, content, timestamp, agentEvents, durationSec }) => ({ role, content, timestamp, agentEvents, durationSec }))
  })
  const [models, setModels] = useState<ComposerModelOption[]>([])
  const [selection, setSelection] = useState<ChatModelSelection>(() => {
    try {
      const saved = localStorage.getItem('siyi-chat-model')
      return saved ? JSON.parse(saved) : {}
    } catch { return {} }
  })
  const updateSelection = (s: ChatModelSelection) => {
    setSelection(s)
    try { localStorage.setItem('siyi-chat-model', JSON.stringify(s)) } catch { /* 忽略本地存储写入失败 */ }
  }
  const [kernel, setKernel] = useState<string>(() => {
    try { return normalizeKernelSelection(localStorage.getItem(KERNEL_STORAGE_KEY)) } catch { return DEFAULT_KERNEL_SELECTION }
  })
  const updateKernel = (k: string) => {
    const next = isKernelSelection(k) ? k : DEFAULT_KERNEL_SELECTION
    setKernel(next)
    try { localStorage.setItem(KERNEL_STORAGE_KEY, next) } catch { /* 忽略本地存储写入失败 */ }
  }
  const [modelError, setModelError] = useState<string | null>(null)
  const conversations = useStore((state) => state.conversations)
  const activeConversationId = useStore((state) => state.activeConversationId)
  const { send, stop, isStreaming, streamedText, streamEvents, error } = useChatStream(activeConversationId)
  /**
   * bug②（e2e 会话实测登记）：runGeneration 在 `await send()` 之后读的是**渲染闭包**里
   * 的 error（发起那一轮捕获的值，恒为 null），失败的轮次因此被写成「（已停止）」而非
   * 「生成失败：xxx」。这里以 store 即时快照为准、ref 兜底（会话条目被 clear 时）。
   */
  const errorRef = useRef<string | null>(error)
  errorRef.current = error
  const { t } = useTranslation()
  const createConversation = useStore((state) => state.createConversation)
  const importConversations = useStore((state) => state.importConversations)
  const deleteConversation = useStore((state) => state.deleteConversation)
  const handleDelete = (id: string) => {
    import('../../utils/chatSync').then(m => m.markConversationDeleted(id)).catch(() => {})
    // 同步删除后端副本，避免后端残留导致换设备/重装后重新导入
    getWsClient()?.call('chat.delete_conversation', { session_id: id }).catch(() => {})
    deleteConversation(id)
  }
  const setActiveConversation = useStore((state) => state.setActiveConversation)
  const addMessageToConversation = useStore((state) => state.addMessageToConversation)
  const replaceConversationMessages = useStore((state) => state.replaceConversationMessages)
  const setConversationTitle = useStore((state) => state.setConversationTitle)
  const sessionStartedAt = useStore((state) => (activeConversationId ? state.chatSessions[activeConversationId]?.startedAt : undefined))

  /** 把会话写回后端长期存储。新对话页此前只存本地 store.json，
   *  多窗口写入竞争/存储清理都会导致记录丢失；写入后端后可随时重新导入。 */
  const persistConversationToBackend = (conversationId: string) => {
    const conv = useStore.getState().conversations.find((c) => c.id === conversationId)
    if (!conv || conv.messages.length === 0) return
    const title = conv.title && conv.title !== '新对话' ? conv.title : deriveConversationTitle(conv.messages)
    if (title !== conv.title) setConversationTitle(conversationId, title)
    const client = getWsClient()
    if (!client || client.status !== 'connected') return
    client.call('chat.save_messages', {
      session_id: conversationId,
      title,
      messages: conv.messages.map((m) => ({ role: m.role, content: m.content, timestamp: m.timestamp })),
    }).catch(() => { /* 后端暂不可用时静默失败，本地 store 仍保留 */ })
  }
  /**
   * P2：把本次生成产生的 agent 事件（工具/子任务/计划）持久化到该会话最后一条 AI 消息。
   * 写到本地 conversations（随 store.json 持久化），不进入 chat.save_messages 后端字段，
   * 避免改动后端会话写入契约；刷新后本地 store 仍可回溯，后端重导则逐条加载本地消息。
   */
  const attachAgentEventsToLastMessage = (conversationId: string) => {
    const session = useStore.getState().chatSessions[conversationId]
    const events = session?.events ?? []
    if (events.length === 0) return
    const durationSec = session?.startedAt ? Math.max(0, (Date.now() - session.startedAt) / 1000) : undefined
    useStore.setState((state) => {
      const conversations = state.conversations.map((c) => {
        if (c.id !== conversationId || c.messages.length === 0) return c
        const msgs = [...c.messages]
        const last = msgs[msgs.length - 1]
        if (!last || last.role !== 'assistant') return c
        msgs[msgs.length - 1] = { ...last, agentEvents: events, durationSec }
        return { ...c, messages: msgs }
      })
      return { conversations }
    })
  }
  const [connected, setConnected] = useState(false)
  const initializedRef = useRef(false)

  useEffect(() => {
    if (!initializedRef.current && conversations.length === 0) {
      initializedRef.current = true
      createConversation()
    }
  }, [conversations.length, createConversation])

  useEffect(() => {
    const conversation = conversations.find((item) => item.id === activeConversationId)
    const msgs = conversation?.messages ?? []
    setMessages([...msgs].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0)).map(({ role, content, timestamp, agentEvents, durationSec }) => ({ role, content, timestamp, agentEvents, durationSec })))
  }, [activeConversationId, conversations])

  useEffect(() => {
    const client = getWsClient()
    setConnected(client.status === 'connected')
    return client.onStatusChange((status) => setConnected(status === 'connected'))
  }, [])

  // ── 危险操作确认卡片 + 工具产物（截图等）订阅 ──
  const [confirmations, setConfirmations] = useState<PendingConfirmation[]>([])
  const [artifactItems, setArtifactItems] = useState<ChatArtifact[]>([])
  // H 项：审批理由（前端必填）——confirmation_id → 理由文本
  const [reasonInputs, setReasonInputs] = useState<Record<string, string>>({})
  // M2.2 ①：聊天内嵌审批卡——sandbox.pending_added 事件驱动的 R4 待批队列
  const approvals = useSandboxApprovals()
  // P1b 浮出审批卡：参数 JSON 折叠展开态（confirmation_id → 是否展开）
  const [showArgs, setShowArgs] = useState<Record<string, boolean>>({})
  useEffect(() => {
    const client = getWsClient()
    const offReq = client.on('action.confirm_required', (ev) => {
      const p = ev.payload || {}
      const confirmation_id = String(p.confirmation_id || '')
      const action_id = String(p.action_id || '')
      const args = (p.args as Record<string, unknown>) || {}
      const argsKey = JSON.stringify(args)
      setConfirmations((items) => [
        // 去重：内核重试会对同一 (action_id, args) 重复发起，只保留最新一条 pending
        ...items.filter((c) => c.status !== 'pending' || c.action_id !== action_id || JSON.stringify(c.args || {}) !== argsKey),
        { confirmation_id, action_id, args, status: 'pending', source: String(p.source || '') },
      ])
    })
    const offRes = client.on('action.confirm_resolved', (ev) => {
      const p = ev.payload || {}
      const id = String(p.confirmation_id || '')
      const approved = p.approved === true
      setConfirmations((items) => items.map((c) => c.confirmation_id === id
        ? { ...c, status: approved ? 'approved' : 'rejected' }
        : c))
    })
    const offArt = client.on('chat.artifact', (ev) => {
      const p = ev.payload || {}
      const arts = normalizeChatArtifacts(Array.isArray(p.artifacts) ? p.artifacts : [])
      if (arts.length > 0) setArtifactItems((items) => [...items, ...arts])
    })
    return () => { offReq(); offRes(); offArt() }
  }, [])
  const resolveConfirmation = async (confirmation_id: string, approved: boolean, source = '', reason = '', alwaysAllow = false) => {
    const client = getWsClient()
    if (!client) return
    // H 项：审批理由必填——批准操作带理由才能提交（前端必填，后端也会校验）
    if (approved && !reason.trim()) return
    try {
      // B2（2026-09-11）：alwaysAllow=true 时后端把该能力写入
      // assistant_confirm_always_allow 名单，下次起不再触发审批。
      await client.call('confirmation.resolve', { confirmation_id, approved, reason, always_allow: alwaysAllow })
    } catch { /* 后端暂不可用时静默 */ }
    // 提交成功后清掉该卡片的理由输入
    setReasonInputs((r) => { const next = { ...r }; delete next[confirmation_id]; return next })
    if (!approved || !activeConversationId) return
    // 批5（2026-09-18）单一真源：内核分支与 butler 分支的唯一差别是"当前内核是否
    // 声明了 resume 能力"——声明了就同样走 chat.resume 真续跑；只有未声明（如
    // DSH / Codex 默认值）才回落到"重发原消息"的兜底。此前前端硬编码
    // source==='mcp:kernel' 即等于"不能续跑"，与后端 gateway_path 的说法相反；
    // 现在两端都读 capabilities().resume（后端 protocol.supports_resume）。
    const useKernelResend = source === 'mcp:kernel' && !(await kernelSupportsResume(client))
    if (useKernelResend) {
      await autoContinueKernel()
      return
    }
    // butler（M2 断点续跑）/ 已声明 resume 能力的内核：批准后从 checkpoint 恢复。
    try {
      const result = await resumeChatStream(activeConversationId)
      const text = result.text.trim()
      if (text) {
        addMessageToConversation(activeConversationId, result.text, 'assistant')
        persistConversationToBackend(activeConversationId)
      }
    } catch (err) {
      addMessageToConversation(activeConversationId, '恢复任务失败，请重试或重新发起', 'assistant')
      persistConversationToBackend(activeConversationId)
    }
  }

  useEffect(() => {
    let active = true
    const client = getWsClient()
    loadBackendConversations(client, useStore.getState().conversations).then((imported) => {
      if (!active || imported.length === 0) return
      importConversations(imported)
    })
    return () => { active = false }
  }, [importConversations])

  useEffect(() => {
    let active = true
    getWsClient()?.call<ModelProvider[]>('model.list', {}).then((result) => {
      if (!active || !Array.isArray(result)) return
      setModelError(null)
      const safeModels = normalizeModelOptions(result)
      setModels(safeModels)
      // Only auto-select if user hasn't already chosen (respects persisted preference)
      if (safeModels[0] && !selection.provider) updateSelection({ provider: safeModels[0].provider, model: safeModels[0].model })
    }).catch(() => { if (active) setModelError('模型列表暂时不可用，将使用后端自动选择') })
    return () => { active = false }
  }, [])

  /** 执行一轮生成并把结果写入会话（handleSend/重新生成/编辑重发共用） */
  const runGeneration = async (
    conversationId: string,
    content: string,
    history: ChatHistoryItem[],
    attachments?: import('./chatAttachments').WireAttachment[],
  ) => {
    let result: { text: string; artifacts: any[] }
    try {
      result = await send(content, history, selection, { searchMode, multiAgentMode, ...buildKernelSendParam(kernel), ...(attachments?.length ? { attachments } : {}) })
    } catch (err) {
      const fallback = '生成失败：后端连接异常'
      addMessageToConversation(conversationId, fallback, 'assistant')
      persistConversationToBackend(conversationId)
      return
    }
    const text = result.text.trim()
    if (text || result.artifacts.length > 0) {
      addMessageToConversation(conversationId, result.text, 'assistant')
    } else {
      // CRITICAL FIX: always persist an assistant placeholder on abort/empty/error
      const liveError = useStore.getState().chatSessions[conversationId]?.error ?? errorRef.current
      const fallback = liveError ? `生成失败：${liveError}` : '（已停止）'
      addMessageToConversation(conversationId, fallback, 'assistant')
    }
    attachAgentEventsToLastMessage(conversationId)
    persistConversationToBackend(conversationId)
  }

  const handleSend = async (
    content: string,
    attachments?: import('./chatAttachments').WireAttachment[],
  ) => {
    let conversationId = activeConversationId
    if (!conversationId) {
      conversationId = createConversation()
    }
    // 附件名并入展示/历史文本（后端本轮注入的是解析内容，这里标注的是"带过什么文件"，
    // 让后续轮次的上下文也能知道曾经附带过附件）
    const display = attachments?.length
      ? `${content}\n📎 ${attachments.map((a) => a.name).join('、')}`
      : content
    const nextHistory = [...messages, { role: 'user' as const, content: display, timestamp: Date.now() }]
    addMessageToConversation(conversationId, display, 'user')
    await runGeneration(conversationId, content, nextHistory, attachments)
  }

  /** 内核审批批准后的**兜底**重发：仅当内核未声明 resume 能力时使用。
   *
   * 批5（2026-09-18）单一真源：是否走这里由 `kernelSupportsResume`（读
   * `kernel.status.details.capabilities.resume`，真源在 backend
   * `services/agent_gateway/protocol.py::BaseGateway.capabilities`）决定，
   * 不再由 `source === 'mcp:kernel'` 直接推定——那会把"来源"当成"能力"，
   * 与后端 gateway_path 的判断各算一遍。
   *
   * 兜底原理（方案 A，2026-09-09）：`confirmation.resolve` 已在批准时真正执行
   * 工具并把结果缓存；未声明 resume 的内核（DSH 无 checkpoint）重发最后一条
   * 用户消息重新触发一轮生成，内核重跑时经后端 `consume_approved_shell` 命中
   * 缓存结果继续收尾，无需重复审批。
   */
  const autoContinueKernel = async () => {
    if (!activeConversationId || isStreaming) return
    let lastUserIdx = -1
    let lastUser = ''
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'user') { lastUserIdx = i; lastUser = messages[i].content; break }
    }
    if (!lastUser || lastUserIdx < 0) return
    // 到该用户消息为止作为历史（不新增重复的用户消息），直接请求新一轮生成
    await runGeneration(activeConversationId, lastUser, messages.slice(0, lastUserIdx + 1))
  }

  /** 重新生成：丢弃 index 处的旧 AI 回复，用其前文重新请求 */
  const handleRegenerate = async (index: number) => {
    if (!activeConversationId || isStreaming) return
    const upto = messages.slice(0, index)
    let lastUser = ''
    for (let i = upto.length - 1; i >= 0; i--) {
      if (upto[i].role === 'user') { lastUser = upto[i].content; break }
    }
    if (!lastUser) return
    replaceConversationMessages(activeConversationId, upto)
    await runGeneration(activeConversationId, lastUser, upto)
  }

  /** 编辑重发：替换 index 处的用户消息，丢弃其后内容重新请求 */
  const handleEditResend = async (index: number, newText: string) => {
    if (!activeConversationId || isStreaming) return
    const upto: ChatHistoryItem[] = [...messages.slice(0, index), { role: 'user' as const, content: newText, timestamp: Date.now() }]
    replaceConversationMessages(activeConversationId, upto)
    await runGeneration(activeConversationId, newText, upto)
  }

  /** 删除单条消息 */
  const handleDeleteMessage = (index: number) => {
    if (!activeConversationId) return
    const next = messages.filter((_, i) => i !== index)
    replaceConversationMessages(activeConversationId, next)
    persistConversationToBackend(activeConversationId)
  }

  /** 反馈写入后端学习闭环（chat.feedback.set） */
  const handleRate = (index: number, rating: 'up' | 'down') => {
    const aiMsg = messages[index]
    let originalUserMsg = ''
    for (let i = index - 1; i >= 0; i--) {
      if (messages[i].role === 'user') { originalUserMsg = messages[i].content; break }
    }
    getWsClient()?.call('chat.feedback.set', {
      session_id: activeConversationId || '',
      message_index: index,
      rating,
      original_user_msg: originalUserMsg,
      original_ai_msg: aiMsg?.content || '',
    }).catch(() => { /* 反馈上报失败静默 */ })
  }

  // ── 流式渲染节流（t4）：逐 chunk 写 store 会触发 ChatMessage 全量重算 markdown，
  // │  用 rAF 把高密 chunks 合并成一帧渲染，长文档生成时显著降低 CPU（观感几乎无差）。
  // │  流结束时立即同步最终态，保证结果精确。
  const [throttledText, setThrottledText] = useState('')
  const rafRef = useRef<number | null>(null)
  useEffect(() => {
    if (!isStreaming) {
      setThrottledText(streamedText)
      if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null }
      return
    }
    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null
      setThrottledText(streamedText)
    })
    return () => { if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = null } }
  }, [streamedText, isStreaming])

  // ── 消息列表虚拟化：长对话只渲染视口内的消息，滚动时动态测量高度。
  // │  流式消息/确认卡/工具产物作为普通流式元素排布在虚拟列表之后，避免反复重测。
  const listParentRef = useRef<HTMLDivElement | null>(null)
  const virtualizer = useVirtualizer({
    count: messages.length,
    getScrollElement: () => listParentRef.current,
    estimateSize: () => 64,
    overscan: 8,
  })

  const liveMessage: ChatHistoryItem | null = isStreaming && throttledText ? { role: 'assistant', content: throttledText } : null
  const agentEvents = useMemo(() => normalizeAgentEvents(streamEvents), [streamEvents])
  // bug④/U1：仅真正的降级 notice 才出顶部「内核降级」横幅；phase/等待类 notice
  // 已收口到 WaitingStatus，不再触发这条横幅（此前任意 notice 都会误报降级）。
  const kernelNotice = streamEvents.some((e) => e.type === 'notice' && parseStreamNotice(e.data).kind === 'kernel_degraded')
  // bug④：后端 notice 帧是结构化 JSON（chat_error 带 friendly/technical 双字段），
  // 旧前端只有一个 kernelNotice 布尔把全部内容压成一行灰字 → 分型细节被吞掉。
  const noticeInfos = useMemo(
    () => streamEvents.filter((e) => e.type === 'notice').map((e) => parseStreamNotice(e.data)),
    [streamEvents],
  )
  const errorNotice = noticeInfos.find((n) => n.kind === 'chat_error') ?? null
  // U1 等待收口（去重）：kernel_waiting / 带 phase 的等待类 notice 只进 WaitingStatus，
  // 从 otherNotices 行内条过滤掉，保证等待期一屏只有一条等待文案。
  const isWaitingNotice = (n: (typeof noticeInfos)[number]) => n.kind === 'kernel_waiting' || n.phase !== ''
  const otherNotices = noticeInfos.filter((n) => n.kind !== 'chat_error' && n.kind !== 'kernel_degraded' && !isWaitingNotice(n))
  // U1 等待状态阶段：由事件流（含新纳入的 tool/confirm）+ 前端 startedAt 逐秒派生。
  const waitingStage = useWaitingStage(streamEvents, isStreaming ? (sessionStartedAt ?? null) : null)
  // P1a：工具正在执行时 AI 头像切换到 alert（警示）mood
  const resolvingTool = isStreaming && agentEvents.some((e) => e.status === 'running')
  const conversationItems = useMemo(() => [...conversations]
    .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
    .map((conversation) => ({
      id: conversation.id,
      title: conversation.title,
      updatedAt: formatConversationTime(conversation.updatedAt),
    })), [conversations])

  /* 2026-09-01 UX 重构（用户拍板）：历史栏默认折叠、执行上下文改按需抽屉。
     agentOpen 取代原 agentCollapsed——默认关闭，任务执行中产生 Agent 事件时自动弹出。 */
  const [convCollapsed, setConvCollapsed] = useState(true)
  const [agentOpen, setAgentOpen] = useState(false)
  const [ideaCollapsed, setIdeaCollapsed] = useState(false)
  const [searchMode, setSearchMode] = useState(false)
  const [multiAgentMode, setMultiAgentMode] = useState(false)

  const agentEventCount = agentEvents.length
  useEffect(() => {
    // 仅在执行中自动弹出：用户手动收起后，本轮不再打扰，下轮任务重新弹出
    if (agentEventCount > 0 && isStreaming) setAgentOpen(true)
  }, [agentEventCount, isStreaming])

  /** AI 身份：当前模型 / 内核标签 / 已耗时长（只对 AI 消息与流式态展示） */
  const aiModelLabel = selection.provider && selection.model ? `${selection.provider}/${selection.model}` : (selection.model || '')
  const elapsedSecNow = sessionStartedAt ? Math.max(0, Math.floor((Date.now() - sessionStartedAt) / 1000)) : 0

  return <div className={`chat-workbench${convCollapsed ? ' chat-workbench--conv-collapsed' : ''}${ideaCollapsed ? ' chat-workbench--idea-collapsed' : ''}`} data-testid="chat-workbench">
    {!convCollapsed && <ConversationSidebar conversations={conversationItems} activeId={activeConversationId} onSelect={setActiveConversation} onNew={createConversation} onDelete={handleDelete} onRename={(id, title) => { setConversationTitle(id, title); persistConversationToBackend(id) }} />}
    <section className="chat-center-pane">
      <header className="chat-workbench-header">
        <div className="chat-workbench-title">
          <button onClick={() => setConvCollapsed(!convCollapsed)} className="w-6 h-6 flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-colors text-xs flex-shrink-0" title={convCollapsed ? '展开对话列表' : '折叠对话列表'}>
            <Icon icon={`fa-solid fa-chevron-${convCollapsed ? 'right' : 'left'}`} />
          </button>
          <span className="chat-eyebrow">小问 Agent</span>
          <h1>{conversations.find((item) => item.id === activeConversationId)?.title ?? '新对话'}</h1>
          <span className={`chat-connection${connected ? '' : ' chat-connection-off'}`}>{connected ? '后端已连接' : '等待连接'}</span>
          {/* D4：会话导出 Markdown */}
          <button
            onClick={() => {
              const conv = conversations.find((c) => c.id === activeConversationId)
              if (!conv || conv.messages.length === 0) return
              const title = conv.title && conv.title !== '新对话' ? conv.title : '对话记录'
              const lines = [`# ${title}`, '']
              for (const m of [...conv.messages].sort((a, b) => (a.timestamp || 0) - (b.timestamp || 0))) {
                lines.push(`## ${m.role === 'user' ? '我' : '小问'}`, '', m.content, '')
              }
              const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
              const url = URL.createObjectURL(blob)
              const a = document.createElement('a')
              a.href = url
              a.download = `${title.slice(0, 30)}.md`
              a.click()
              URL.revokeObjectURL(url)
            }}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-colors text-xs flex-shrink-0"
            title="导出会话为 Markdown"
          >
            <Icon icon="fa-solid fa-file-export" />
          </button>
        </div>
        {!agentOpen && (
          <button
            onClick={() => setAgentOpen(true)}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text-secondary hover:text-text-primary hover:bg-hover-bg transition-colors text-xs flex-shrink-0"
            title="展开执行上下文"
          >
            <Icon icon="fa-solid fa-chevron-left" />
          </button>
        )}
      </header>
      {/* 执行上下文抽屉：默认隐藏，任务事件自动弹出；absolute 浮层不挤压聊天区 */}
      {agentOpen && <AgentContextPanel events={agentEvents} connected={connected} streaming={isStreaming} onCollapse={() => setAgentOpen(false)} />}
      {modelError && <p className="chat-inline-error" role="status">{modelError}</p>}
      {kernelNotice && (
        <p className="flex items-center gap-1 px-1 text-2xs text-text-dim" role="status">
          <Icon icon="fa-solid fa-circle-info" />
          <span>{t('chat.kernelDegradedNotice')}</span>
        </p>
      )}
      <main ref={listParentRef} className="chat-message-scroll"><div className="chat-message-column">
        {messages.length > 0 && (
          <div style={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
            {virtualizer.getVirtualItems().map(vItem => {
              const message = messages[vItem.index]
              const index = vItem.index
              const isLastAssistant = message.role === 'assistant' && !messages.slice(index + 1).some((m) => m.role === 'assistant')
              const isAi = message.role === 'assistant'
              return (
                <div
                  key={`${activeConversationId}-${message.role}-${index}`}
                  data-index={index}
                  ref={virtualizer.measureElement}
                  style={{
                    position: 'absolute',
                    top: 0,
                    left: 0,
                    width: '100%',
                    paddingBottom: 24,
                    transform: `translateY(${vItem.start}px)`,
                  }}
                >
                  <ChatMessage
                    message={message}
                    onRate={isAi ? (r) => handleRate(index, r) : undefined}
                    onRegenerate={isAi && isLastAssistant && !isStreaming ? () => handleRegenerate(index) : undefined}
                    onEdit={message.role === 'user' && !isStreaming ? (t) => handleEditResend(index, t) : undefined}
                    onDelete={!isStreaming ? () => handleDeleteMessage(index) : undefined}
                    aiModel={isAi ? aiModelLabel : undefined}
                    aiKernel={isAi ? kernel : undefined}
                    elapsedSec={isAi ? elapsedSecNow : undefined}
                    aiMood={isAi ? (resolvingTool ? 'alert' : undefined) : undefined}
                  />
                  {/* P2 可回溯时间线：已完成的 AI 消息旁展示其工具工作过程（持久化于会话消息） */}
                  {isAi && message.agentEvents && message.agentEvents.length > 0 && (
                    <ToolTimeline events={normalizeAgentEvents(message.agentEvents as ChatStreamEvent[])} durationSec={message.durationSec} />
                  )}
                </div>
              )
            })}
          </div>
        )}
        {/* Live streaming message */}
        {liveMessage && <ChatMessage message={liveMessage} streaming aiModel={isStreaming ? aiModelLabel : undefined} aiKernel={isStreaming ? kernel : undefined} aiMood={resolvingTool ? 'alert' : undefined} />}
        {/* Thinking indicator: streaming but no text yet — U1 收口为单行等待状态
            （后端 notice message 优先，否则阶段文案；计时前端 startedAt 派生） */}
        {isStreaming && !streamedText && (
          <div className="flex gap-3">
            <AiAvatar mood="thinking" size={28} streaming />
            <div className="rounded-md px-4 py-3 bg-surface border border-border text-sm text-text-muted">
              <WaitingStatus
                startedAt={sessionStartedAt ?? null}
                stage={waitingStage.stage}
                lastMessage={waitingStage.lastNoticeMessage}
              />
            </div>
          </div>
        )}
        {/* Agent tool events inline */}
        {agentEvents.slice(-3).map((ev, i) => (
          <div key={ev.id || i} className="flex gap-3">
            <div className="w-7 h-7 rounded-md bg-success-soft text-success flex items-center justify-center flex-shrink-0 text-2xs">
              <Icon icon={`fa-solid fa-${ev.kind === 'plan' ? 'clipboard-list' : ev.kind === 'tool' ? 'gear' : 'robot'}`} />
            </div>
            <div className="rounded-md px-3 py-1.5 bg-surface border border-border text-2xs text-text-secondary">
              <span className="font-medium text-text-primary">{ev.title}</span>
              {ev.detail ? <span className="ml-2">{ev.detail.slice(0, 80)}</span> : null}
              <span className={`ml-2 inline-block w-2 h-2 rounded-full ${ev.status === 'running' ? 'bg-brand animate-pulse' : ev.status === 'done' ? 'bg-success' : 'bg-warning'}`} />
            </div>
          </div>
        ))}
        {/* M2.2 ①：R4 待批操作在会话流内嵌成卡（批准/拒绝直连 sandbox.approve/reject） */}
        {approvals.pending.slice(0, 3).map((item) => (
          <div key={item.pending_id} className="flex gap-3 justify-end">
            <ApprovalCard item={item} onApprove={approvals.approve} onReject={approvals.reject} />
          </div>
        ))}
        {/* bug④：取消/内核等待等非错误类 notice 就地成行（chat_error 并入下方错误卡） */}
        {otherNotices.slice(-2).map((n, i) => (
          <div key={`notice-${i}`} className="flex gap-3 justify-end">
            <ChatNoticeRow notice={n} />
          </div>
        ))}
        {/* 审批执行结果（已批准/已拒绝）仍在消息流展示 */}
        {confirmations.filter((c) => c.status !== 'pending').slice(-2).map((c) => (
          <div key={`done-${c.confirmation_id}`} className="flex gap-3">
            <div className={`w-7 h-7 rounded-md flex items-center justify-center flex-shrink-0 text-2xs ${c.status === 'approved' ? 'bg-success-soft text-success' : 'bg-hover-bg text-text-muted'}`}>
              <Icon icon={`fa-solid fa-${c.status === 'approved' ? 'check' : 'xmark'}`} />
            </div>
            <div className="rounded-md px-3 py-1.5 bg-surface border border-border text-2xs text-text-muted">
              {c.action_id}：{c.status === 'approved' ? '已批准并执行' : '已拒绝'}
            </div>
          </div>
        ))}
        {/* 工具产物（截图等图片卡片） */}
        {artifactItems.length > 0 && (
          <div className="flex gap-3">
            <AiAvatar mood="done" size={28} />
            <div className="max-w-[72%] rounded-md px-4 py-3 bg-surface border border-border">
              <ArtifactRenderer content="" artifacts={artifactItems} />
            </div>
          </div>
        )}
        {error && (() => {
          // D4：流失败错误卡片 —— 友好化原因 + 一键重试
          const hint = describeToolError(error)
          const lastUser = [...messages].reverse().find((m) => m.role === 'user')
          return (
            <div role="alert" className="flex gap-3">
              <div className="w-7 h-7 rounded-md bg-danger-soft text-danger flex items-center justify-center flex-shrink-0 text-xs">
                <Icon icon="fa-solid fa-triangle-exclamation" />
              </div>
              <div className="max-w-[72%] rounded-md px-4 py-3 bg-surface border border-danger/40 text-sm">
                <div className="font-medium text-text-primary mb-1">{hint ? hint.title : '生成失败'}</div>
                {hint && <div className="text-2xs text-text-secondary leading-relaxed mb-1">{hint.advice}</div>}
                <div className="text-2xs text-text-muted font-mono truncate mb-2" title={error}>{error}</div>
                {/* bug④：配对的 notice 帧带来 technical 双字段 → 行内可展开排障细节 */}
                <NoticeTechnical technical={errorNotice?.technical ?? error} />
                {lastUser && !isStreaming && (
                  <button
                    onClick={() => handleSend(lastUser.content)}
                    className="px-3 py-1 rounded-lg text-2xs font-medium bg-brand text-white hover:opacity-90 transition-opacity"
                  >
                    重试上一条
                  </button>
                )}
              </div>
            </div>
          )
        })()}
        {messages.length === 0 && !isStreaming && (
          <div className="chat-empty-state">
            <strong>把复杂任务交给小问</strong>
            <span>计划、工具结果和风险确认会显示在右侧执行上下文。</span>
            {/* P1c 能力快捷入口 chips：点击仅填入输入框，由用户确认后发送（不自动发送） */}
            <div className="flex flex-wrap items-center justify-center gap-2 mt-1">
              {QUICK_PROMPTS.map((chip) => (
                <button
                  key={chip.label}
                  onClick={() => { if (chip.prompt.trim()) window.dispatchEvent(new CustomEvent('siyi:chat-draft', { detail: chip.prompt.trim() })) }}
                  className="px-3 py-1.5 rounded-full text-xs text-[var(--text-primary)] bg-[var(--panel-bg)] border border-[var(--panel-border)] hover:text-[var(--brand)] hover:border-[var(--brand)] transition-colors"
                >
                  {chip.label}
                </button>
              ))}
            </div>
          </div>
        )}
      </div></main>
      {/* P1b 审批浮出卡：常驻可见区（不随消息流滚动），首次出现带 pulse 入场动画 */}
      {confirmations.filter((c) => c.status === 'pending').map((c) => (
        <motion.div
          key={c.confirmation_id}
          initial={{ scale: 0.96, opacity: 0 }}
          animate={{ scale: 1, opacity: 1 }}
          transition={{ type: 'tween', duration: 0.28, ease: 'easeOut' }}
          className="mx-4 mb-1 rounded-xl border border-[var(--warning)]/60 bg-[var(--panel-bg)] p-3.5 text-sm z-10"
          style={{ boxShadow: '0 0 0 1px rgba(245,158,11,0.14), 0 0 18px rgba(245,158,11,0.16), 0 10px 28px rgba(0,0,0,0.28)' }}
        >
          <div className="flex items-start gap-2 mb-2">
            <div className="w-6 h-6 rounded-md bg-warning-soft text-warning flex items-center justify-center flex-shrink-0 text-xs mt-0.5">
              <Icon icon="fa-solid fa-shield-halved" />
            </div>
            <div className="min-w-0">
              <div className="font-medium text-text-primary text-sm">
                小问请求执行需要确认的操作：<code className="text-2xs px-1 py-0.5 rounded bg-hover-bg">{c.action_id}</code>
              </div>
              {/* 参数 JSON 可折叠：▸ 查看参数 */}
              {Object.keys(c.args ?? {}).length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowArgs((s) => ({ ...s, [c.confirmation_id]: !s[c.confirmation_id] }))}
                  className="mt-1 text-2xs text-text-muted hover:text-text-secondary transition-colors"
                >
                  {showArgs[c.confirmation_id] ? '▾ 收起参数' : '▸ 查看参数'}
                </button>
              )}
              {showArgs[c.confirmation_id] && (
                <pre className="text-2xs text-text-secondary whitespace-pre-wrap break-all max-h-28 overflow-y-auto mt-1 mb-1 rounded bg-hover-bg p-2">{JSON.stringify(c.args, null, 2)}</pre>
              )}
            </div>
          </div>
          {/* 审批理由（前端必填）——批准操作必须填写理由，不带理由被拦 */}
          <input
            type="text"
            value={reasonInputs[c.confirmation_id] ?? ''}
            onChange={(e) => setReasonInputs((r) => ({ ...r, [c.confirmation_id]: e.target.value }))}
            placeholder="批准理由（必填，将写入审计）"
            className="w-full mb-2 px-2 py-1.5 rounded-lg text-2xs bg-hover-bg border border-border text-text-primary outline-none focus:border-brand placeholder:text-text-muted"
            aria-label="批准理由"
          />
          <div className="flex gap-2">
            <button onClick={() => resolveConfirmation(c.confirmation_id, true, c.source, reasonInputs[c.confirmation_id] ?? '')}
              disabled={!((reasonInputs[c.confirmation_id] ?? '').trim())}
              className="px-3 py-1.5 rounded-lg text-2xs font-medium bg-brand text-white hover:opacity-90 transition-opacity disabled:opacity-40 disabled:cursor-not-allowed">允许执行</button>
            <button onClick={() => resolveConfirmation(c.confirmation_id, true, c.source, reasonInputs[c.confirmation_id] ?? '', true)}
              disabled={!((reasonInputs[c.confirmation_id] ?? '').trim())}
              title="批准本次执行，并把该能力加入「始终允许」名单——今后同类操作不再询问（仍受白名单与沙箱约束，且写入审计）"
              className="px-3 py-1.5 rounded-lg text-2xs font-medium border border-brand text-brand hover:bg-brand-soft transition-colors disabled:opacity-40 disabled:cursor-not-allowed">始终允许</button>
            <button onClick={() => resolveConfirmation(c.confirmation_id, false, c.source)}
              className="px-3 py-1.5 rounded-lg text-2xs font-medium border border-border text-text-secondary hover:bg-hover-bg transition-colors">拒绝</button>
          </div>
        </motion.div>
      ))}
      <footer className="chat-composer-wrap"><ChatComposer onSend={handleSend} onStop={stop} streaming={isStreaming} models={models} selection={selection} onModelChange={updateSelection} searchMode={searchMode} onToggleSearch={() => setSearchMode(v => !v)} multiAgentMode={multiAgentMode} onToggleMultiAgent={() => setMultiAgentMode(v => !v)} history={messages} kernel={kernel} onKernelChange={updateKernel} /></footer>
    </section>
    <IdeaPanel collapsed={ideaCollapsed} onToggleCollapse={() => setIdeaCollapsed(v => !v)} />
  </div>
}
