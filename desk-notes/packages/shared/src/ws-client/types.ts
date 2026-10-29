/* Type definitions for WebSocket JSON-RPC protocol. */

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected'

export interface WsClientOptions {
  url?: string
  /** P1-4: 真实主 token（远端设备持有）。非空且 WS 地址为远端时，连接URL自动附带
   *  `?token=<authToken>`（浏览器 WS 无法带 header/cookie，URL 是唯一可行通道）。
   *  loopback（本机，如 127.0.0.1/localhost）由服务端直接信任，无需此字段。 */
  authToken?: string
  reconnectInterval?: number
  maxReconnectAttempts?: number
  /**
   * P2-10: 心跳间隔（毫秒）。每隔此时长向后端发送一次 `ping` JSON-RPC。
   * `<= 0` 关闭心跳；默认 25_000（与典型代理 30s 空闲断开留 5s 余量）。
   */
  heartbeatIntervalMs?: number
  /**
   * P2-10: 心跳超时（毫秒）。发送 ping 后若超过此时长未收到 pong，
   * 判定连接已死并主动关闭、触发重连。`<= 0` 关闭超时检测；默认 10_000。
   */
  heartbeatTimeoutMs?: number
  /**
   * M2.2 第0步：客户端版本声明。后端 M2.1 起，未声明 client_version 的连接被
   * 判为 legacy_client，其 R4 破坏性方法（notes.delete 等）改回显式
   * -32010 CLIENT_UPGRADE_REQUIRED。默认取 resolveClientVersion()。
   */
  clientVersion?: string
}

export interface JsonRpcRequest {
  jsonrpc: '2.0'
  id: number
  method: string
  params?: Record<string, unknown>
  stream?: boolean
  /** M2.2 第0步：帧级版本声明（后端 ws_server.py:1153-1158 按帧升级连接标记）。 */
  client_version?: string
}

export interface JsonRpcResponse<T = unknown> {
  jsonrpc: '2.0'
  id: number
  result?: T
  error?: {
    code: number
    message: string
    data?: unknown
  }
}

/**
 * Stream chunk types sent by the backend.
 *
 * - `chunk`: normal AI text delta (appended to assistant message)
 * - `done`: stream terminated normally
 * - `error`: terminal error — UI should NOT append to chat history
 * - `tool_progress`: tool calling progress (e.g. "[正在调用工具: browser.navigate...]")
 * - `tool_result`: tool execution result summary
 * - `planner`: Multi-Agent Planner progress (task decomposition / aggregation)
 * - `subagent_start`: a SubAgent started executing
 * - `subagent_done`: a SubAgent finished (success or failure)
 * - `multi_agent_summary`: final aggregated summary from Multi-Agent workflow
 * - `tool`: 内核工具调用步骤（gateway_path.py 结构化 payload JSON）
 * - `confirm`: 内核请求用户确认（危险操作审批提示，payload JSON）
 *
 * Non-`chunk`/`done` types are metadata: UI may render them as inline status
 * hints but should not append to the persisted chat history.
 */
export type StreamChunkType =
  | 'chunk'
  | 'done'
  | 'error'
  | 'tool_progress'
  | 'tool_result'
  | 'planner'
  | 'subagent_start'
  | 'subagent_done'
  | 'multi_agent_summary'
  | 'chat_resume_status'
  | 'tool'
  | 'confirm'
  | 'notice'

export interface JsonRpcStreamChunk {
  jsonrpc: '2.0'
  id: number
  result: {
    type: StreamChunkType
    data?: string
  }
}

/** Typed chunk emitted by `WsClient.streamTyped()`. */
export interface TypedStreamChunk {
  type: StreamChunkType
  data: string
}

export interface ServerPushEvent {
  type: 'event'
  event_type: string
  payload: Record<string, unknown>
  source?: string
}
