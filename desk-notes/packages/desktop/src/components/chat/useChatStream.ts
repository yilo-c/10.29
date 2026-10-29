import { useCallback } from 'react'
import type { StreamChunkType } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'
import { trackUsage } from '../../utils/trackUsage'
import { useStore } from '../../store/useStore'
import { normalizeChatArtifacts, type ChatArtifact } from './artifacts'

export interface ChatHistoryItem { role: 'user' | 'assistant'; content: string; artifacts?: ChatArtifact[]; timestamp?: number; agentEvents?: Array<{ type: string; data: string }>; durationSec?: number }
export interface ChatModelSelection { provider?: string | null; model?: string | null }
export interface ChatStreamEvent { type: Exclude<StreamChunkType, 'chunk' | 'done' | 'error'>; data: string }
export interface ChatSendResult { text: string; artifacts: ChatArtifact[] }

/** 从流事件中提取 Artifact：tool_result / multi_agent_summary 可能含结构化产物。 */
function extractArtifacts(events: ChatStreamEvent[]): ChatArtifact[] {
  const candidates: unknown[] = []
  for (const event of events) {
    if (event.type === 'tool_result' || event.type === 'multi_agent_summary') {
      try {
        const parsed: unknown = JSON.parse(event.data)
        if (parsed && typeof parsed === 'object') {
          const record = parsed as Record<string, unknown>
          if (Array.isArray(record.artifacts)) candidates.push(...record.artifacts)
          else if (Array.isArray(record.output)) candidates.push(...record.output)
          else if (typeof record.artifact === 'object' && record.artifact) candidates.push(record.artifact)
          else if (typeof record.result === 'object' && record.result) candidates.push(record.result)
        }
      } catch { /* 非结构化事件，无 artifact */ }
    }
  }
  if (candidates.length === 0) return []
  return normalizeChatArtifacts(candidates)
}

export interface ChatSendOptions {
  searchMode?: boolean
  multiAgentMode?: boolean
  kernel?: string
  /** Task #9 对话附件（后端 chat_paths/attachments.py 消化：文本内联/图片 vision 转描述） */
  attachments?: import('./chatAttachments').WireAttachment[]
}

// ── 模块级流运行器（P3 D2）──────────────────────────────────────
// 流循环与 AbortController 放在模块级：组件卸载（切换页面）后流继续跑，
// 进度实时写入全局 store；回到页面重新挂载时从 store 恢复显示。
const _controllers = new Map<string, AbortController>()

/**
 * 停止指定会话的流（含后端 chat.cancel）。
 *
 * bug①（e2e 会话实测登记）：旧实现 `abort()` 后立即 `_controllers.delete()`，
 * 使 sendChatStream 的 finally 守卫 `_controllers.get(id) === controller` 永不成立
 * → chatSessionEnd 不被调用 → 后端以 done 干净收尾时 isStreaming 停在 true、
 * 输入框永久卡死（见 e2e/chat.spec.ts:365-370 的「刻意不断言」注释）。
 * 修法：在此显式收尾会话态（chatSessionEnd 幂等：error 传 undefined 时保留原值），
 * 守卫条件同时放宽到「条目已被摘除」，两条路径都能复位。
 *
 * bug③：chat.cancel 带 session_id 定向取消——后端 `_chat_memory.py:504-515` 已支持
 * session_id/stream_id 定向；不带参数会取消**全部**在途流，旧实现停 A 会话会误伤 B 会话。
 */
export function stopChatStream(conversationId: string): void {
  const controller = _controllers.get(conversationId)
  if (controller) {
    controller.abort()
    _controllers.delete(conversationId)
    useStore.getState().chatSessionEnd(conversationId)
  }
  try {
    getWsClient()?.call('chat.cancel', { session_id: conversationId }).catch(() => {})
  } catch { /* ignore */ }
}

// 流式空闲看门狗：后端异常挂起（无任何 chunk/事件）时终止流，
// 避免 UI 永远停在「思考中」。tool_progress 等事件会重置计时。
const CHAT_STREAM_IDLE_TIMEOUT_MS = 120_000

/**
 * 收尾一轮流：仅当该会话仍由本次 controller 持有、或条目已被 stopChatStream 摘除
 * （undefined）时才复位会话态。若已被新一轮 send 替换，则不得覆盖新流状态。
 * bug①：旧守卫只认 `=== controller`，被 stopChatStream 提前 delete 后永不成立。
 */
function endStreamSession(conversationId: string, controller: AbortController): void {
  const current = _controllers.get(conversationId)
  if (current !== controller && current !== undefined) return
  // error 传 undefined：保留 catch 中已设置的错误
  useStore.getState().chatSessionEnd(conversationId)
  if (current === controller) _controllers.delete(conversationId)
}

/** 发起流式对话；状态写入 store.chatSessions[conversationId]，组件卸载不影响执行。 */
export async function sendChatStream(
  conversationId: string,
  message: string,
  history: ChatHistoryItem[],
  selection?: ChatModelSelection,
  options?: ChatSendOptions,
): Promise<ChatSendResult> {
  stopChatStream(conversationId)
  const controller = new AbortController()
  _controllers.set(conversationId, controller)
  useStore.getState().chatSessionStart(conversationId)
  trackUsage('chat.send')

  const contextMessages = history.slice(-20).map((item) => ({ role: item.role, content: item.content })).filter((item) => item.content.trim())
  let full = ''
  const capturedEvents: ChatStreamEvent[] = []
  try {
    const ws = getWsClient()
    if (!ws || controller.signal.aborted) throw new Error('backend_not_connected')
    for await (const chunk of ws.streamTyped('chat.send', {
      // bug③：带当前会话 id —— 后端 _chat_memory.py:309 用它登记在途流，
      // chat.cancel 才能按 session_id 定向取消（否则只能取消全部在途流）。
      session_id: conversationId,
      message, context_messages: contextMessages,
      ...(selection?.provider && selection.model ? { provider: selection.provider, model: selection.model } : {}),
      ...(options?.searchMode ? { web_search: true } : {}),
      ...(options?.multiAgentMode ? { mode: 'multi_agent' } : {}),
      ...(options?.kernel ? { kernel: options.kernel } : {}),
      // Task #9：附件仅在"本轮"注入（后端解析进 message，不进 context_messages），
      // 避免 base64 堆积在会话历史里反复上送。
      ...(options?.attachments?.length ? { attachments: options.attachments } : {}),
      stream: true,
    }, { idleTimeoutMs: CHAT_STREAM_IDLE_TIMEOUT_MS })) {
      if (controller.signal.aborted) break
      if (chunk.type === 'chunk' && chunk.data) {
        full += chunk.data
        useStore.getState().chatSessionChunk(conversationId, chunk.data)
      } else if (isChatStreamEvent(chunk.type) && chunk.data) {
        const event: ChatStreamEvent = { type: chunk.type, data: chunk.data }
        capturedEvents.push(event)
        useStore.getState().chatSessionEvent(conversationId, event)
      }
    }
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      useStore.getState().chatSessionEnd(conversationId, err instanceof Error ? err.message : String(err))
    }
  } finally {
    endStreamSession(conversationId, controller)
  }
  const artifacts = extractArtifacts(capturedEvents)
  return { text: full, artifacts }
}

/**
 * M2 断点续跑：恢复挂起的任务流（chat.resume）。
 * 前端批准确认后调用——后端从 checkpoint 恢复，同一会话继续流式输出。
 * 无挂起任务时后端发 chat_resume_status 状态 chunk，这里转为事件展示。
 */
export async function resumeChatStream(conversationId: string): Promise<ChatSendResult> {
  stopChatStream(conversationId)
  const controller = new AbortController()
  _controllers.set(conversationId, controller)
  useStore.getState().chatSessionStart(conversationId)

  let full = ''
  const capturedEvents: ChatStreamEvent[] = []
  try {
    const ws = getWsClient()
    if (!ws || controller.signal.aborted) throw new Error('backend_not_connected')
    for await (const chunk of ws.streamTyped('chat.resume', {
      session_id: conversationId,
      stream: true,
    }, { idleTimeoutMs: CHAT_STREAM_IDLE_TIMEOUT_MS })) {
      if (controller.signal.aborted) break
      if (chunk.type === 'chunk' && chunk.data) {
        full += chunk.data
        useStore.getState().chatSessionChunk(conversationId, chunk.data)
      } else if (chunk.type === 'chat_resume_status') {
        const event: ChatStreamEvent = { type: 'tool_result', data: chunk.data }
        capturedEvents.push(event)
        useStore.getState().chatSessionEvent(conversationId, event)
      } else if (isChatStreamEvent(chunk.type) && chunk.data) {
        const event: ChatStreamEvent = { type: chunk.type, data: chunk.data }
        capturedEvents.push(event)
        useStore.getState().chatSessionEvent(conversationId, event)
      }
    }
  } catch (err) {
    if (!(err instanceof DOMException && err.name === 'AbortError')) {
      useStore.getState().chatSessionEnd(conversationId, err instanceof Error ? err.message : String(err))
    }
  } finally {
    endStreamSession(conversationId, controller)
  }
  const artifacts = extractArtifacts(capturedEvents)
  return { text: full, artifacts }
}

/** 会话流式状态 hook：从全局 store 读取，跨页面挂载保持。 */
export function useChatStream(conversationId: string | null) {
  const session = useStore((s) => (conversationId ? s.chatSessions[conversationId] : undefined))
  const isStreaming = session?.isStreaming ?? false
  const streamedText = session?.streamedText ?? ''
  const streamEvents = (session?.events ?? []) as ChatStreamEvent[]
  const error = session?.error ?? null

  const send = useCallback(
    (message: string, history: ChatHistoryItem[], selection?: ChatModelSelection, options?: ChatSendOptions): Promise<ChatSendResult> => {
      if (!conversationId) return Promise.reject(new Error('no active conversation'))
      return sendChatStream(conversationId, message, history, selection, options)
    },
    [conversationId],
  )

  const stop = useCallback(() => {
    if (conversationId) stopChatStream(conversationId)
  }, [conversationId])

  const clearError = useCallback(() => {
    if (conversationId) useStore.getState().chatSessionClearError(conversationId)
  }, [conversationId])

  return { send, stop, isStreaming, streamedText, streamEvents, error, clearError }
}

function isChatStreamEvent(type: StreamChunkType): type is ChatStreamEvent['type'] {
  return type === 'tool_progress'
    || type === 'tool_result'
    || type === 'planner'
    || type === 'subagent_start'
    || type === 'subagent_done'
    || type === 'multi_agent_summary'
    || type === 'tool'
    || type === 'confirm'
    || type === 'notice'
}
