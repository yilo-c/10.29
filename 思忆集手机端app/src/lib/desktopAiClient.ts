/**
 * 桌面端 AI 客户端 / Desktop AI client
 *
 * ⚠️ 历史 bug（2026-10-30 修复）：本文件此前请求 `POST {baseUrl}/agent/ask`，
 * 请求体是 `{message, session_id}`，响应读 `{reply, session_id}`。
 * 但这条路径**在真实后端上根本不存在**：
 *
 *   1. 主后端 `思忆集app/backend/ws_server.py` 只注册了
 *      `/ws` `/health` `/stats` `/metrics` `/rpc` `/auth` `/discover`，
 *      **没有 `/agent/ask`** → 换任何真实桌面端都是 404。
 *   2. `/agent/ask` 只存在于 `automation_hub`（FastAPI，端口 8000），
 *      而 AGENTS.md 明确标注 automation_hub 已边缘化、非现行真源。
 *      且它那里的契约是 `AskRequest{query}` → `AskResponse{summary}`，
 *      字段名也和这里的 `message`/`reply` 对不上。
 *
 * 结果：**桌面端 AI 通道从未真正可用**。又因为 chatOrchestrator 的
 * tryDesktop 用空 catch 吞掉异常，用户只看到「AI 服务暂时不可用，已排队」，
 * 完全无从判断。
 *
 * 正确的桌面端对话通道是 WS JSON-RPC 的 `chat.send`
 * （`思忆集app/backend/handlers/_chat_memory.py:518`）：
 *   - 参数 `message`（与手机端一致）
 *   - 流式返回（AsyncIterator），在 WS 上以 chunk 推送
 *   - 后端侧完整支持：PydanticAI 管家循环、上下文压缩、记忆锚点、工具调用
 *   - 配套 `chat.cancel` 可真正中止
 *
 * 故本客户端改为经 `rpcClient.callRpcStream('chat.send', ...)` 调用。
 * 保留 `sendMessage` 的非流式入口（内部消费完流后返回完整文本），
 * 让上层调用方无需改动。
 */

import { callRpcStream, isDesktopConnected, configureWs, configureWsAuthToken } from './rpcClient'
import { tokenManager } from './tokenManager'

export interface DesktopAiResponse {
  reply: string
  sessionId?: string
}

/**
 * 桌面端连接错误的友好提示。
 *
 * 与云端不同：这里连的是用户自己的电脑。401 通常是配对令牌过期，
 * 未连接则多半是后端没启动或不在同一网络。
 * 若沿用云端那套话术，会把用户误导去改 API Key —— 桌面端没有这个概念。
 */
function friendlyDesktopError(status: number, rawBody: string): string {
  const body = (rawBody || '').slice(0, 160)
  switch (status) {
    case 401:
    case 403:
      return '桌面端认证失败，配对令牌可能已失效。请在手机上重新配对桌面端。'
    case 404:
      return '桌面端没有这个接口，可能后端未启动或版本过旧。请确认电脑端思忆集后端已运行并升级到最新版。'
    case 502:
    case 503:
    case 504:
      return '桌面端暂时无响应。请确认电脑上的思忆集后端仍在运行、手机与电脑在同一网络。'
    default:
      return `桌面端返回错误（HTTP ${status}）：${body || '无详细原因'}`
  }
}

/** 流式 chunk 的形状（后端 chat.send 的 chunk 类型） */
interface ChatStreamChunk {
  type?: string
  content?: string
  delta?: string
  text?: string
  session_id?: string
  [k: string]: unknown
}

function extractDelta(chunk: ChatStreamChunk): string {
  // 后端可能用 content / delta / text 三种键之一承载增量，按优先级取
  if (typeof chunk.delta === 'string') return chunk.delta
  if (typeof chunk.content === 'string') return chunk.content
  if (typeof chunk.text === 'string') return chunk.text
  return ''
}

export class DesktopAiClient {
  /*
   * 这两个字段保留是为了兼容既有调用方（mobileStore 里仍会调用
   * `configure(baseUrl, token)`）。但实际连接参数由 rpcClient 统一管理
   * （configureWs / configureWsAuthToken），本类不再重复持有。
   * 保留空实现 + 字段，避免改动调用方签名造成额外变更面。
   */
  private baseUrl = ''
  private authToken: string | null = null

  configure(baseUrl: string, token: string | null): void {
    this.baseUrl = (baseUrl || '').replace(/\/+$/, '')
    this.authToken = token
    /*
     * 把连接参数同步给 rpcClient —— 真正建立 WS 的是它。
     * 本类只负责「把对话请求翻译成 chat.send 流式调用」，
     * 不再自己维护一条 HTTP 通道（那条 /agent/ask 从来不存在）。
     */
    if (this.baseUrl) {
      configureWs(this.baseUrl)
      configureWsAuthToken(this.authToken)
    }
  }

  /**
   * 单轮对话（非流式对外表现）。
   *
   * 内部走流式接口把内容累积完整再返回：桌面端没有一次性问答的 RPC，
   * 只有 `chat.send` 这个流式方法。对外保持同步语义，调用方无需改造。
   */
  async sendMessage(message: string, sessionId?: string): Promise<DesktopAiResponse> {
    let full = ''
    const res = await this.sendMessageStreaming(message, (c) => { full += c }, sessionId)
    return { reply: res.reply || full, sessionId: res.sessionId }
  }

  /**
   * 流式对话。走 WS JSON-RPC `chat.send`。
   *
   * @param signal 取消信号。被 abort 时抛出 AbortError，由上层区分「用户主动停止」
   *               与「真正失败」。后端另有 `chat.cancel` 可彻底终止服务端生成，
   *               此处先做客户端侧中断（足够让 UI 立即恢复可输入）。
   */
  async sendMessageStreaming(
    message: string,
    onChunk: (chunk: string) => void,
    sessionId?: string,
    signal?: AbortSignal,
  ): Promise<DesktopAiResponse> {
    if (!isDesktopConnected()) {
      /*
       * 用真实的 WS 连接状态判断，而不是「已配对 && baseUrl 非空」。
       * 后者在电脑关机/掉线时仍返回 true，会让每次请求先打一次必失败的调用，
       * 用户白等十几秒才降级。
       */
      throw new Error(
        this.baseUrl
          ? '桌面端未连接。请确认电脑上的思忆集后端已启动，且手机与电脑在同一网络。'
          : '尚未配对该桌面端。请先在 设置 → 设备配对 中扫码连接电脑。'
      )
    }

    let full = ''
    let newSessionId: string | undefined

    try {
      for await (const raw of callRpcStream('chat.send', {
        message,
        session_id: sessionId ?? null,
      })) {
        if (signal?.aborted) {
          const err = new Error('已取消')
          err.name = 'AbortError'
          throw err
        }
        const chunk = raw as unknown as ChatStreamChunk
        // 后端错误型 chunk：明确抛出，避免把错误文案当正文渲染
        if (chunk.type === 'error' || chunk.type === 'notice_error') {
          throw new Error(extractDelta(chunk) || '桌面端返回错误')
        }
        if (typeof chunk.session_id === 'string' && chunk.session_id) {
          newSessionId = chunk.session_id
        }
        const delta = extractDelta(chunk)
        if (delta) {
          full += delta
          onChunk(delta)
        }
      }
    } catch (e) {
      // abort 原样抛出，让上层按「用户取消」处理
      if (e instanceof Error && e.name === 'AbortError') throw e
      const msg = e instanceof Error ? e.message : String(e)
      // WS 未连接类错误给出可执行指引
      if (/WebSocket|not connected/i.test(msg)) {
        throw new Error('桌面端连接已断开。请检查电脑端后端是否仍在运行。')
      }
      throw new Error(`桌面端对话失败：${msg}`)
    }

    if (!full) {
      throw new Error('桌面端未返回内容。可能是后端模型未配置，请在电脑端检查 AI 配置。')
    }

    return {
      reply: full,
      sessionId: newSessionId ?? sessionId ?? tokenManager.deviceId ?? undefined,
    }
  }
}

export { friendlyDesktopError }
