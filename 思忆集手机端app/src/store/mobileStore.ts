import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { tokenManager } from '../lib/tokenManager'
import { ApiClient, type ConnectionState } from '../lib/apiClient'
import { AuthRepository } from '../lib/authRepository'
import { DesktopAiClient } from '../lib/desktopAiClient'
import { CloudAiClient } from '../lib/cloudAiClient'
import { ToolRegistry } from '../lib/toolRegistry'
import { a11yTools } from '../lib/a11yTools'
import { runAgent, looksLikeMultiStepTask } from '../lib/agentLoop'
import { describeToolCall } from '../components/ui/ApprovalDialog'
import { persistMessages, getCurrentSessionId, newSessionId, getSession, listSessions, deleteSession, type StoredSession } from '../lib/sessionStore'
import { ChatOrchestrator } from '../lib/chatOrchestrator'
import { OfflineAiFallback } from '../lib/offlineFallback'
import { useStore, getHuangli } from '@desk-notes/shared'
import type { PendingApproval } from '../lib/rpcClient'
import { listPendingApprovals, approveApproval, rejectApproval } from '../lib/rpcClient'

/* ── Chat types ── */

/*
 * 高风险操作审批的 resolver。
 *
 * Agent 循环 await 一个 Promise，审批弹窗通过 resolveApproval 兑现它。
 * 用模块级变量而不是 store state：Promise 的 resolve 函数无法序列化，
 * 放进 state 会污染 persist 且浏览器可能拒绝结构化克隆。
 */
let approvalResolver: ((d: { approved: boolean; reason?: string }) => void) | null = null

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

/* ── Store types ── */

export type SyncStatus = 'idle' | 'syncing' | 'success' | 'error'

interface MobileStore {
  /* Pairing */
  pairing: {
    serverUrl: string
    isPaired: boolean
    deviceName: string | null
    isLoading: boolean
    error: string | null
  }
  pair: (token: string, serverUrl: string, deviceName: string) => Promise<void>
  unpair: () => void

  /* AI routing */
  ai: {
    enabled: boolean
    preferDesktop: boolean
    requireConfirm: boolean
  }
  setAiConfig: (config: Partial<MobileStore['ai']>) => void

  /* Sync */
  sync: {
    lastSyncTime: string | null
    pendingOps: number
    status: SyncStatus
  }
  setSyncStatus: (status: SyncStatus) => void
  setPendingOps: (count: number) => void
  setLastSyncTime: (time: string) => void

  /* Connection */
  connection: ConnectionState
  setConnection: (state: ConnectionState) => void

  /* Chat */
  chat: {
    messages: ChatMessage[]
    sessionId: string | null
    streaming: boolean
  }
  sendMessage: (text: string) => Promise<void>
  clearMessages: () => void
  setSessionId: (id: string) => void
  setStreaming: (v: boolean) => void

  /* 会话持久化（IndexedDB） */
  /** 启动时恢复上次会话 */
  restoreLastSession: () => Promise<void>
  /** 列出所有历史会话 */
  listChatSessions: () => Promise<StoredSession[]>
  /** 打开指定会话 */
  openChatSession: (id: string) => Promise<void>
  /** 删除会话 */
  removeChatSession: (id: string) => Promise<void>
  /** 新建会话 */
  startNewSession: () => void

  /* 高风险操作审批（C 项） */
  /** 待审批请求；null 表示无 */
  pendingApproval: {
    id: string
    toolLabel: string
    toolId: string
    summary: string
    details: Array<{ label: string; value: string }>
    reason?: string
  } | null
  /** 提交审批结果（由弹窗调用） */
  resolveApproval: (approved: boolean, reason?: string) => void

  /* 跨组件导航请求（悬浮球等外部入口触发） */
  /**
   * 请求切换到指定 Tab。
   * 悬浮球在 App 外悬停，点一下要能把用户带回对应页面，
   * 但球本身拿不到 App 的本地 state，所以通过 store 发信号，由 App.tsx 消费。
   */
  pendingTabRequest: string | null
  requestTab: (tab: string) => void
  consumeTabRequest: () => string | null
  /** 请求打开快速记录面板 */
  quickNoteRequested: boolean
  requestQuickNote: () => void
  consumeQuickNoteRequest: () => boolean

  /* Remote approvals */
  approvals: {
    pending: PendingApproval[]
    loading: boolean
    error: string | null
  }
  fetchApprovals: () => Promise<void>
  approvePending: (pendingId: string) => Promise<boolean>
  rejectPending: (pendingId: string) => Promise<boolean>
}

/* ── Tool Registry ── */

import { createNotesTool } from '../lib/ai-tools/notesTool'
import { createTasksTool } from '../lib/ai-tools/tasksTool'
import { createClipboardTool } from '../lib/ai-tools/clipboardTool'
import { createVoiceTool } from '../lib/ai-tools/voiceTool'
import { swallowError } from '../lib/swallowError'

const toolRegistry = new ToolRegistry()
let toolsRegistered = false
function ensureToolsRegistered() {
  if (toolsRegistered) return
  toolRegistry.registerAll([
    createNotesTool(async (text: string) => {
      const store = useStore.getState()
      const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
      store.addFloatingNote({ id, title: text.slice(0, 50), content: text, type: 'text', x: 0, y: 0, zIndex: 1, width: 320, height: 200, createdAt: Date.now(), updatedAt: Date.now(), tags: ['ai_created'], editMode: 'markdown' })
      return '笔记已创建'
    }),
    createTasksTool(async (text: string) => {
      const store = useStore.getState()
      store.addTodo(text)
      return '任务已创建'
    }),
    createClipboardTool(async () => {
      try {
        const text = await navigator.clipboard.readText()
        return text || ''
      } catch {
        return ''
      }
    }),
    createVoiceTool(() => {
      try {
        const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
        if (!SpeechRecognition) return
        const recognition = new SpeechRecognition()
        recognition.lang = 'zh-CN'
        recognition.continuous = false
        recognition.interimResults = false
        recognition.onresult = (event: any) => {
          const transcript = event.results[0][0].transcript
          if (transcript) {
            const store = useStore.getState()
            const id = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
            store.addFloatingNote({ id, title: transcript.slice(0, 50), content: transcript, type: 'text', x: 0, y: 0, zIndex: 1, width: 320, height: 200, createdAt: Date.now(), updatedAt: Date.now(), tags: ['voice_capture'], editMode: 'markdown' })
          }
        }
        recognition.start()
      } catch (e) {
        // Speech API not available — silently no-op
        swallowError('mobileStore:130', e)
      }
    }),
    /*
     * 无障碍自动化工具 / Accessibility automation
     *
     * 让 AI 能真的操作手机，而不只是回答问题：
     * 读屏 → 定位 → 点击/滑动 → 启动应用。
     *
     * 依赖用户在系统设置里手动开启「思忆便签」的无障碍服务。
     * 未开启时各工具返回明确错误（而非静默失败），
     * AI 会把原因转述给用户。
     */
    ...a11yTools,
  ])
  toolsRegistered = true
}

/* ── Natural Language Task Parser ── */

// Build AI context from current user state
function buildAIContext(): string {
  const store = useStore.getState()
  const parts: string[] = []

  // Pending tasks
  const pending = store.todos.filter(t => !t.done)
  if (pending.length > 0) {
    const taskLines = pending.slice(0, 8).map(t => {
      let info = t.text
      if (t.dueDate) {
        const now = Date.now()
        const diff = t.dueDate - now
        if (diff < 0) info += '（已逾期）'
        else if (diff < 86400000) info += '（今天截止）'
        else if (diff < 172800000) info += '（明天截止）'
        else info += `（${new Date(t.dueDate).toLocaleDateString('zh-CN')}截止）`
      }
      if (t.priority && t.priority > 1) info += ' [高优]'
      return info
    })
    parts.push(`当前待办(${pending.length}项未完成):\n${taskLines.join('\n')}`)
  }

  // Today's almanac
  try {
    const today = new Date()
    const hl = getHuangli(today)
    if (hl) {
      const yi = hl.yiJi.yi || []
      const ji = hl.yiJi.ji || []
      if (yi.length > 0) parts.push(`今日宜: ${yi.join('、')}`)
      if (ji.length > 0) parts.push(`今日忌: ${ji.join('、')}`)
    }
  } catch (e) { /* almanac unavailable */ swallowError('mobileStore:174', e) }

  /*
   * 手机操作能力说明。
   *
   * 不写这段，AI 不会知道「读屏 → 定位 → 点击」这套流程该怎么串，
   * 会退化成只会聊天。这里显式给出操作范式与安全边界。
   */
  parts.push(
    '【手机操作能力】\n' +
    '你可以直接操作这台手机，可用工具：\n' +
    '- phone.read_screen：读当前屏幕所有文字（先读再动，不要盲点）\n' +
    '- phone.list_apps / phone.launch_app：查应用、启动应用\n' +
    '- phone.tap / phone.swipe：按坐标点击、滑动\n' +
    '- phone.nav_back / nav_home / nav_recents：系统导航键\n' +
    '\n操作范式：任何要动屏幕的任务，先 read_screen 看当前在哪、有哪些元素，\n' +
    '再决定点哪里；操作后再次 read_screen 确认结果。不要在没有读屏的情况下猜测坐标。\n' +
    '\n安全边界：涉及付款、删除、发送消息、修改账号等不可逆操作时，\n' +
    '先向用户复述你打算做什么并等待确认，不要自行执行。'
  )

  /*
   * 屏幕尺寸：tap/swipe 用的是物理像素坐标，
   * 不告诉模型分辨率，它只能瞎猜（点不中的概率极高）。
   * 这里在上下文里给出宽高与常见区域参考。
   */
  try {
    const w = typeof window !== 'undefined' ? window.screen?.width : 0
    const h = typeof window !== 'undefined' ? window.screen?.height : 0
    const dpr = typeof window !== 'undefined' ? (window.devicePixelRatio || 1) : 1
    if (w && h) {
      const pw = Math.round(w * dpr)
      const ph = Math.round(h * dpr)
      parts.push(
        `【屏幕坐标参考】\n` +
        `屏幕物理像素：${pw} × ${ph}（CSS ${w} × ${h}，像素比 ${dpr}）\n` +
        `坐标原点在左上角。屏幕中心约为 (${Math.round(pw/2)}, ${Math.round(ph/2)})。\n` +
        `底部导航栏区域约在 y > ${Math.round(ph*0.94)}，状态栏约在 y < ${Math.round(ph*0.04)}，\n` +
        `点击这两个区域容易触发系统操作，除非用户明确要求，否则避免。`
      )
    }
  } catch { /* 取不到尺寸就不给，不影响其他上下文 */ }

  return parts.join('\n')
}

// Augment user message with context for AI calls
function contextualizeMessage(text: string): string {
  const context = buildAIContext()
  if (!context) return text
  return `[当前上下文]\n${context}\n\n用户消息: ${text}`
}

// Quick heuristic: does the text look like it's asking to do something?
function looksLikeTaskRequest(text: string): boolean {
  const taskVerbs = /^(买|做|去|取|拿|交|送|还|修|换|洗|刷|扫|擦|整理|收拾|清理|写|发|打|叫|约|订|看|读|学|练|跑|检查|准备|完成|处理|联系|问|告诉|通知|提醒|记得|需要|想要|打算|计划)/
  const taskPattern = /^(我要|我想|帮我|请|需要|记得|提醒|别忘)/
  return taskVerbs.test(text.trim()) || (taskPattern.test(text.trim()) && text.length < 50)
}

// Try to parse a natural language task request using the available AI
async function tryAutoCreateTask(text: string): Promise<string | null> {
  if (!looksLikeTaskRequest(text)) return null

  const extractPrompt = `从以下文字中提取任务信息，只返回 JSON 格式，不要其他文字：
{
  "title": "任务标题（必填，简洁完整）",
  "dueDate": "ISO 日期时间（如 2026-05-17T15:00，可选）",
  "priority": "high/medium/low（可选，默认 medium）",
  "reminderTime": "ISO 日期时间（可选）"
}

文字：${text}`

  try {
    let jsonText = ''

    if (tokenManager.isPaired && tokenManager.baseUrl) {
      const client = new DesktopAiClient()
      client.configure(tokenManager.baseUrl, tokenManager.apiToken)
      const res = await client.sendMessage(extractPrompt)
      jsonText = res.reply
    } else if (tokenManager.cloudAiEnabled && (tokenManager.cloudApiKey || useStore.getState().activeProfileId)) {
      const client = new CloudAiClient()
      const profiles = useStore.getState().aiProfiles
      const activeProfile = profiles.find(p => p.id === useStore.getState().activeProfileId)
      const overrides = activeProfile?.protocol === 'openai'
        ? { apiKey: tokenManager.cloudApiKey || undefined, baseUrl: activeProfile.apiUrl, model: activeProfile.model }
        : undefined
      const res = await client.sendMessage(extractPrompt, undefined, overrides)
      jsonText = res.reply
    } else {
      return null
    }

    const jsonMatch = jsonText.match(/\{[\s\S]*\}/)
    if (!jsonMatch) return null

    const data = JSON.parse(jsonMatch[0])
    if (!data.title || typeof data.title !== 'string') return null

    const store = useStore.getState()

    // Create task
    store.addTodo(data.title.trim())

    // Get the newly created task's ID (addTodo prepends to the list)
    const newTodo = store.todos[0]
    if (!newTodo) return null

    if (data.priority && data.priority !== 'medium') {
      const level = data.priority === 'high' ? 2 as const : 0 as const
      store.setPriority(newTodo.id, level)
    }
    if (data.dueDate) {
      store.setDueDate(newTodo.id, new Date(data.dueDate).getTime())
    }
    if (data.reminderTime) {
      store.setReminderTime(newTodo.id, new Date(data.reminderTime).getTime())
    }

    const parts = [`已创建任务: ${data.title.trim()}`]
    if (data.dueDate) parts.push(`截止 ${new Date(data.dueDate).toLocaleString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}`)
    if (data.priority && data.priority !== 'medium') parts.push(`优先级: ${data.priority === 'high' ? '高' : '低'}`)

    return parts.join(' | ')
  } catch {
    return null
  }
}

export const useMobileStore = create<MobileStore>()(
  persist(
    (set, get) => ({
      /* ── Pairing ── */
      pairing: {
        serverUrl: tokenManager.baseUrl,
        isPaired: tokenManager.isPaired,
        deviceName: tokenManager.deviceName,
        isLoading: false,
        error: null,
      },

      pair: async (token: string, serverUrl: string, deviceName: string) => {
        set(s => ({ pairing: { ...s.pairing, isLoading: true, error: null } }))
        const api = new ApiClient()
        const authRepo = new AuthRepository(api)
        const result = await authRepo.validateAndPair(token, serverUrl, deviceName)
        if (result.isPaired) {
          set(s => ({
            pairing: {
              ...s.pairing,
              isLoading: false,
              isPaired: true,
              serverUrl,
              deviceName,
              error: null,
            },
          }))
        } else {
          set(s => ({
            pairing: {
              ...s.pairing,
              isLoading: false,
              error: result.error ?? '配对失败',
            },
          }))
        }
      },

      unpair: () => {
        const api = new ApiClient()
        const authRepo = new AuthRepository(api)
        authRepo.clearPairing()
        set(s => ({
          pairing: {
            ...s.pairing,
            isPaired: false,
            serverUrl: '',
            deviceName: null,
            error: null,
          },
          connection: 'disconnected' as ConnectionState,
        }))
      },

      /* ── AI routing ── */
      ai: {
        enabled: tokenManager.cloudAiEnabled,
        preferDesktop: tokenManager.preferDesktopAi,
        requireConfirm: tokenManager.promptBeforeCloud,
      },

      setAiConfig: (config: Partial<MobileStore['ai']>) => {
        if (config.enabled !== undefined) tokenManager.cloudAiEnabled = config.enabled
        if (config.preferDesktop !== undefined) tokenManager.preferDesktopAi = config.preferDesktop
        if (config.requireConfirm !== undefined) tokenManager.promptBeforeCloud = config.requireConfirm
        set(s => ({ ai: { ...s.ai, ...config } }))
      },

      /* ── Sync ── */
      sync: {
        lastSyncTime: null,
        pendingOps: 0,
        status: 'idle',
      },

      setSyncStatus: (status: SyncStatus) => set(s => ({ sync: { ...s.sync, status } })),
      setPendingOps: (pendingOps: number) => set(s => ({ sync: { ...s.sync, pendingOps } })),
      setLastSyncTime: (lastSyncTime: string) => set(s => ({ sync: { ...s.sync, lastSyncTime } })),

      /* ── Connection ── */
      connection: 'disconnected' as ConnectionState,
      setConnection: (connection: ConnectionState) => set({ connection }),

      /* ── Chat ── */
      chat: {
        messages: [],
        sessionId: null,
        streaming: false,
      },

      sendMessage: async (text: string) => {
        const userMsg: ChatMessage = {
          id: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
          role: 'user',
          content: text,
          timestamp: Date.now(),
        }
        set(s => ({ chat: { ...s.chat, messages: [...(s.chat.messages || []), userMsg], streaming: true } }))

        ensureToolsRegistered()

        /* 自然语言建任务：确定性规则，先于 AI 尝试，命中就零成本解决 */
        const nluResult = await tryAutoCreateTask(text)
        if (nluResult) {
          const reply: ChatMessage = {
            id: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`,
            role: 'assistant',
            content: nluResult,
            timestamp: Date.now(),
          }
          set(s => ({ chat: { ...s.chat, messages: [...(s.chat.messages || []), reply], streaming: false } }))
          return
        }

        /* 流式占位气泡 */
        const streamMsgId = crypto.randomUUID?.() ?? `${Date.now()}-stream`
        set(s => ({
          chat: {
            ...s.chat,
            messages: [...(s.chat.messages || []), { id: streamMsgId, role: 'assistant', content: '', timestamp: Date.now() }],
          },
        }))

        const onChunk = (chunk: string) => {
          set(s => {
            const msgs = (s.chat.messages || []).map(m =>
              m.id === streamMsgId ? { ...m, content: m.content + chunk } : m
            )
            return { chat: { ...s.chat, messages: msgs } }
          })
        }

        const setStreamContent = (content: string) => {
          set(s => {
            const msgs = (s.chat.messages || []).map(m =>
              m.id === streamMsgId ? { ...m, content } : m
            )
            return { chat: { ...s.chat, messages: msgs, streaming: false } }
          })
        }

        /*
         * 追加最终答案，保留已写入的工具轨迹。
         *
         * 为什么需要单独一个方法：Agent 循环执行时 onStep 会往气泡里追加
         * 「⚙️ 思考中 / ✓ 工具名」，如果用 setStreamContent 整体替换，
         * 这些轨迹会被抹掉，用户看不到 AI 到底做了什么。
         */
        const appendFinalContent = (content: string) => {
          set(s => {
            const msgs = (s.chat.messages || []).map(m =>
              m.id === streamMsgId
                ? { ...m, content: m.content ? `${m.content}\n${content}` : content }
                : m
            )
            return { chat: { ...s.chat, messages: msgs, streaming: false } }
          })
        }

        try {
          const cloudConfigured = tokenManager.cloudAiEnabled
            && !!(tokenManager.cloudApiKey || useStore.getState().activeProfileId)
          const desktopConnected = !!(tokenManager.isPaired && tokenManager.baseUrl)

          /*
           * 多步任务分流 / Route multi-step tasks to the agent loop
           *
           * 单步请求（"记一条笔记"）走原来的 orchestrator：快、省 token。
           * 多步请求（"看看屏幕上有什么，然后点第二个按钮"）必须走 Agent 循环，
           * 否则只会执行第一个工具就结束。
           *
           * 判定用 looksLikeMultiStepTask（序数/递进词 或 多个动作动词），
           * 而非一律走循环 —— 全走循环会让简单请求也变慢、变贵。
           */
          if (cloudConfigured && looksLikeMultiStepTask(text)) {
            const agent = await runAgent(text, toolRegistry, {
              systemContext: buildAIContext(),
              /*
               * R3 高风险操作审批（C 项）
               *
               * tap/swipe 的能力边界等于"用户的手指"，
               * 点错可能误付款/误删/误发。执行前必须让用户看到具体要做什么。
               * 用户拒绝时，会把原因回灌给 AI，让它知道被拒而非重试。
               */
              requestApproval: async ({ tool, args }) => {
                const desc = describeToolCall(tool.id, args)
                const decision = await requestUserApproval({
                  toolId: tool.id,
                  toolLabel: desc.label,
                  summary: desc.summary,
                  details: desc.details,
                })
                return decision
              },
              onStep: (step) => {
                // 把工具轨迹实时拼进气泡，用户能看到 AI 在做什么
                set(s => {
                  const msgs = (s.chat.messages || []).map(m => {
                    if (m.id !== streamMsgId) return m
                    const line = step.type === 'tool_call'
                      ? `⚙️ ${step.text ?? ''}\n`
                      : step.type === 'tool_result'
                        ? `✓ ${step.toolId}${step.ms ? ` (${step.ms}ms)` : ''}\n`
                        : step.type === 'rejected'
                          ? `⛔ ${step.text ?? '已拒绝'}\n`
                          : step.type === 'error'
                            ? `⚠️ ${step.text ?? '出错'}\n`
                            : ''
                    return line ? { ...m, content: m.content + line } : m
                  })
                  return { chat: { ...s.chat, messages: msgs } }
                })
              },
            })

            const finalText = agent.text || (agent.error ?? '任务未完成')
            appendFinalContent(finalText + (agent.usedTools.length > 0
              ? `\n\n—— 用到 ${agent.usedTools.length} 个工具 · ${agent.steps} 步`
              : ''))
            return
          }

          const orchestrator = new ChatOrchestrator(toolRegistry, new OfflineAiFallback())

          const result = await orchestrator.run(
            { text, sessionId: get().chat.sessionId ?? undefined },
            {
              isDesktopConnected: desktopConnected,
              cloudConfigured,
              userPref: {
                cloudAiEnabled: tokenManager.cloudAiEnabled,
                promptBeforeCloud: tokenManager.promptBeforeCloud,
                preferDesktopAi: tokenManager.preferDesktopAi,
              },
              onChunk,
              sendCloud: async (msg, chunk, sessionId) => {
                const cloudAi = new CloudAiClient()
                const profiles = useStore.getState().aiProfiles
                const activeProfile = profiles.find(p => p.id === useStore.getState().activeProfileId)
                const overrides = activeProfile?.protocol === 'openai'
                  ? { apiKey: tokenManager.cloudApiKey || undefined, baseUrl: activeProfile.apiUrl, model: activeProfile.model }
                  : undefined
                await cloudAi.sendMessageStreaming(
                  contextualizeMessage(msg), chunk, sessionId, overrides,
                )
                return msg
              },
              sendDesktop: async (msg, chunk, sessionId) => {
                const desktopAi = new DesktopAiClient()
                desktopAi.configure(tokenManager.baseUrl, tokenManager.apiToken)
                const response = await desktopAi.sendMessageStreaming(
                  contextualizeMessage(msg), chunk, sessionId,
                )
                if (response.sessionId) {
                  const sid = response.sessionId
                  set(s => ({ chat: { ...s.chat, sessionId: sid } }))
                }
                return response.reply
              },
            },
          )

          /* 分流处理结果：工具/缓存/AI 已经把内容写进气泡，这里只补提示或错误 */
          if (result.text) {
            setStreamContent(result.text)
          } else if (result.channel === 'blocked') {
            setStreamContent(`🔒 ${result.error ?? '该内容按隐私设置未上传'}`)
          } else if (result.queued) {
            setStreamContent(`⏳ ${result.error ?? '已排队，联网后自动重试'}`)
          } else {
            setStreamContent(result.error ?? '');
            if (!result.error) {
              setStreamContent('AI 未返回内容，请重试');
            }
          }
        } catch (err) {
          const errMsg = err instanceof Error ? err.message : 'AI 服务暂不可用，请稍后重试'
          setStreamContent(errMsg)
        }
      },

      clearMessages: () => {
        const sid = get().chat.sessionId
        set(s => ({ chat: { ...s.chat, messages: [], sessionId: null } }))
        // 清空即开启新会话，旧会话保留在历史里
        void (sid ? deleteSession(sid) : Promise.resolve())
      },
      setSessionId: (sessionId: string) => set(s => ({ chat: { ...s.chat, sessionId } })),

      /* ── 会话持久化 ──
       *
       * 落盘时机：每次消息数组变化后防抖 800ms 保存。
       * 不用 zustand persist 是因为聊天历史带工具轨迹后体积大，
       * localStorage 5MB 容易写满并静默失败（表现为"记录消失"）。
       */
      restoreLastSession: async () => {
        try {
          const sid = getCurrentSessionId()
          if (!sid) return
          const sess = await getSession(sid)
          if (!sess || sess.messages.length === 0) return
          set(s => ({
            chat: { ...s.chat, messages: sess.messages, sessionId: sess.id },
          }))
        } catch { /* 恢复失败不影响新对话 */ }
      },

      listChatSessions: async () => {
        try { return await listSessions() } catch { return [] }
      },

      openChatSession: async (id: string) => {
        try {
          const sess = await getSession(id)
          if (!sess) return
          set(s => ({
            chat: { ...s.chat, messages: sess.messages, sessionId: sess.id, streaming: false },
          }))
        } catch { /* ignore */ }
      },

      removeChatSession: async (id: string) => {
        try {
          await deleteSession(id)
          if (get().chat.sessionId === id) {
            set(s => ({ chat: { ...s.chat, messages: [], sessionId: null } }))
          }
        } catch { /* ignore */ }
      },

      startNewSession: () => {
        set(s => ({ chat: { ...s.chat, messages: [], sessionId: null, streaming: false } }))
      },

      /* ── 高风险操作审批 ── */
      pendingApproval: null,
      resolveApproval: (approved: boolean, reason?: string) => {
        const resolver = approvalResolver
        approvalResolver = null
        set({ pendingApproval: null })
        resolver?.({ approved, reason })
      },
      setStreaming: (streaming: boolean) => set(s => ({ chat: { ...s.chat, streaming } })),

      /* ── 跨组件导航请求（悬浮球等外部入口） ── */
      pendingTabRequest: null,
      requestTab: (tab: string) => set({ pendingTabRequest: tab }),
      consumeTabRequest: () => {
        const tab = get().pendingTabRequest
        if (tab) set({ pendingTabRequest: null })
        return tab
      },
      quickNoteRequested: false,
      requestQuickNote: () => set({ quickNoteRequested: true }),
      consumeQuickNoteRequest: () => {
        const want = get().quickNoteRequested
        if (want) set({ quickNoteRequested: false })
        return want
      },

      /* ── Remote approvals ── */
      approvals: { pending: [], loading: false, error: null },

      fetchApprovals: async () => {
        if (!tokenManager.isPaired) return
        set(s => ({ approvals: { ...s.approvals, loading: true, error: null } }))
        try {
          const list = await listPendingApprovals()
          set(s => ({ approvals: { ...s.approvals, pending: list, loading: false } }))
        } catch (err) {
          set(s => ({ approvals: { ...s.approvals, loading: false, error: err instanceof Error ? err.message : '获取待审批列表失败' } }))
        }
      },

      approvePending: async (pendingId: string) => {
        try {
          const result = await approveApproval(pendingId)
          if (result.error) return false
          set(s => ({
            approvals: {
              ...s.approvals,
              pending: s.approvals.pending.filter(a => a.pending_id !== pendingId),
            },
          }))
          return true
        } catch {
          return false
        }
      },

      rejectPending: async (pendingId: string) => {
        try {
          const result = await rejectApproval(pendingId)
          if (result.error) return false
          set(s => ({
            approvals: {
              ...s.approvals,
              pending: s.approvals.pending.filter(a => a.pending_id !== pendingId),
            },
          }))
          return true
        } catch {
          return false
        }
      },
    }),
    {
      name: 'siyi-mobile-storage',
      partialize: state => ({
        pairing: { serverUrl: state.pairing.serverUrl, isPaired: state.pairing.isPaired, deviceName: state.pairing.deviceName },
        ai: state.ai,
      }),
    }
  )
)

/*
 * 发起审批，返回用户决定。供 Agent 循环的 requestApproval 调用。
 * 弹窗出现 → 用户点允许/拒绝 → resolveApproval 兑现此 Promise。
 */
function requestUserApproval(info: {
  toolId: string
  toolLabel: string
  summary: string
  details: Array<{ label: string; value: string }>
  reason?: string
}): Promise<{ approved: boolean; reason?: string }> {
  return new Promise(resolve => {
    approvalResolver = resolve
    useMobileStore.setState({
      pendingApproval: {
        id: `ap_${Date.now()}`,
        toolId: info.toolId,
        toolLabel: info.toolLabel,
        summary: info.summary,
        details: info.details,
        reason: info.reason,
      },
    })
  })
}

/*
 * 自动落盘 / Auto-persist
 *
 * 订阅 chat.messages 变化，防抖 800ms 后写入 IndexedDB。
 * 防抖是必要的：流式输出时每个 chunk 都会改 messages，
 * 不防抖会导致每秒几十次写库。
 *
 * 不用 zustand persist 的理由：聊天带工具轨迹后体积大，
 * localStorage 5MB 容易写满且静默失败（表现就是"记录消失"）。
 */
let persistTimer: ReturnType<typeof setTimeout> | null = null
if (typeof window !== 'undefined') {
  useMobileStore.subscribe((state, prev) => {
    if (state.chat.messages === prev.chat.messages) return
    if (state.chat.streaming) return   // 流式过程中不落盘，等结束

    const msgs = state.chat.messages
    if (msgs.length === 0) return

    if (persistTimer) clearTimeout(persistTimer)
    persistTimer = setTimeout(() => {
      const sid = state.chat.sessionId ?? newSessionId()
      if (!state.chat.sessionId) {
        useMobileStore.setState(s => ({ chat: { ...s.chat, sessionId: sid } }))
      }
      void persistMessages(sid, msgs)
    }, 800)
  })
}
