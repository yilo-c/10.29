/**
 * 流式帧 → 消息列表的分流规则（纯函数，可单测）。
 *
 * 为什么单独抽出来：
 *   2026-09-15 悬浮窗出现"输出复读膨胀"（用户实测："当前您有 N 篇笔记…" 逐字符
 *   自我叠加）。根因是 FloatingChatApp 用 `client.stream<string>()` —— 该接口只
 *   yield data、丢弃帧 type（见 shared/ws-client/ws-client.ts onChunk），而
 *   ws_server 下行帧实为 `{type, data}`，type ∈ chunk|done|error|tool_progress|
 *   tool_result|planner…（shared/ws-client/types.ts）。于是 notice / tool_progress
 *   等**元数据帧（携带人类可读状态文本）被无条件当正文追加** → 复读膨胀。
 *
 * 规则（唯一正确行为）：
 *   - `chunk`               → 追加进正文（唯一的正文来源）
 *   - `error`               → 追加错误段（有正文则换行接续，无正文则独立成段）
 *   - `tool_progress` / `tool_result`
 *                           → **不写入正文**，但放行到执行流状态区（人可见「内核在跑什么」）
 *   - 其余（done / notice / planner / …）
 *                           → **不写入正文**（应走状态区/忽略）
 *
 * 抽成纯函数的目的：让这条规则可被机器断言锁死，避免将来再次误改。
 */

/** 最小消息形态（与 FloatingBubble 的 ChatMessage 兼容：只依赖 role/content）。 */
export interface RoutedMessage {
  role: string
  content: string
}

/** 最小帧形态（结构化类型，避免依赖具体导出路径）。 */
export interface RoutedFrame {
  type: string
  data: string
}

/**
 * ── 帧类型白名单（唯一真源）────────────────────────────────────────────
 *
 * F-P0（2026-09-17）：悬浮助手是 chat 的另一形态（全功能管理面），内核真跑了
 * 工具用户必须看得见。此前 `applyFrameToMessages` 用 `!== 'chunk' && !== 'error'`
 * 的反向判定把**所有**元数据帧一律丢弃 —— tool_progress / tool_result 既没进正文
 * （正确）也**没进任何状态区**（缺陷，执行全盲）。
 *
 * 因此把「哪些帧允许去哪」显式列成白名单常量：
 *  - `BODY_FRAME_TYPES`      → 唯一的正文来源，写入消息列表；
 *  - `EXECUTION_FRAME_TYPES` → 允许进入悬浮窗「执行流」状态区（只渲染、不写正文）。
 *
 * 白名单加一项，`tests/floating-channel-contract.test.ts` 必须同步加一项断言
 * （防回退：本项目有过「隐藏导航≠删路由」的静默失效教训，17ecc90）。
 */
export const BODY_FRAME_TYPES = ['chunk', 'error'] as const

/** 内核工具执行帧：放行到执行流状态区（来源见 shared/ws-client/types.ts 的 StreamChunkType）。 */
export const EXECUTION_FRAME_TYPES = ['tool_progress', 'tool_result'] as const

export type BodyFrameType = (typeof BODY_FRAME_TYPES)[number]
export type ExecutionFrameType = (typeof EXECUTION_FRAME_TYPES)[number]

/**
 * 把一帧流式数据应用到消息列表，返回新数组（不可变）。
 *
 * 入参非法（空帧 / 空 data / 列表为空 / 末条非 assistant）时**原样返回**，
 * 不产生任何副作用——保证调用方可以无条件调用。
 */
export function applyFrameToMessages<T extends RoutedMessage>(
  messages: T[],
  frame: RoutedFrame | null | undefined,
): T[] {
  if (!frame || !frame.data) return messages
  if (!isBodyFrame(frame)) return messages
  if (messages.length === 0) return messages

  const last = messages[messages.length - 1]
  if (!last || last.role !== 'assistant') return messages

  const copy = [...messages] as T[]
  const nextContent =
    frame.type === 'chunk'
      ? last.content + frame.data
      : last.content
        ? `${last.content}\n[错误] ${frame.data}`
        : `[错误] ${frame.data}`
  copy[copy.length - 1] = { ...last, content: nextContent } as T
  return copy
}

/** 该帧是否应写入正文（供 UI 分流状态区与正文时复用同一判定）。 */
export function isBodyFrame(frame: RoutedFrame | null | undefined): boolean {
  return !!frame && !!frame.data && (BODY_FRAME_TYPES as readonly string[]).includes(frame.type)
}

/**
 * 该帧是否属于「执行流」——内核工具调用帧，应进入悬浮窗状态区而非正文。
 * 判定与 `isBodyFrame` 互斥（两个白名单不相交），调用方可无条件顺序调用。
 */
export function isExecutionFrame(
  frame: RoutedFrame | null | undefined,
): frame is RoutedFrame & { type: ExecutionFrameType } {
  return !!frame && !!frame.data && (EXECUTION_FRAME_TYPES as readonly string[]).includes(frame.type)
}
