/**
 * F-P0：悬浮窗 ↔ 主窗 会话共享（激活接力语义）。
 *
 * 事实前提（已静态确证，勿凭猜改动）：
 *  - 悬浮窗是**独立 BrowserWindow / 独立渲染进程**（`App.tsx:808` 按
 *    `#view=floating-chat` 路由到 `FloatingChatApp`），因此两窗各有各的 zustand
 *    实例，**内存态不互通**。
 *  - 唯一跨进程可读的共享面是持久化层：`fileStorage`（`utils/fileStore.ts:38`）
 *    在 Electron 下把所有 persist 写入同一个 `store.json`（主进程 IPC）。
 *  - `activeConversationId` / `conversations` **不在** persist 的 TRANSIENT_KEYS 里
 *    （`shared/src/store/useStore.ts:155-166`），即会被持久化 → 可跨窗读。
 *
 * 语义（09-17 拍板：第一版激活接力，不做并发双写）：
 *  任一方发消息即成为活动会话；悬浮窗发消息时携带**与主窗同一个 `session_id`**，
 *  两窗消息流落到后端同一会话（`chat.get_conversation` / `chat.save_messages`）。
 *  刻意不做双窗实时推流/加锁——留待第二阶段。
 */

/** 从持久化 blob 里解出主窗当前活动会话 id（解析失败一律回 null，绝不抛）。 */
export function parsePersistedActiveConversationId(raw: string | null | undefined): string | null {
  if (!raw) return null
  try {
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    const record = parsed as Record<string, unknown>
    // zustand persist 的标准形态是 { state, version }；为兼容裸 state 再多取一层。
    const state = (record.state && typeof record.state === 'object' ? record.state : record) as Record<string, unknown>
    const id = state.activeConversationId
    return typeof id === 'string' && id.trim() ? id : null
  } catch {
    return null
  }
}

/**
 * 共享会话 id 决议：跟随主窗活动会话；主窗无活动会话时落回悬浮窗自己的常驻会话。
 * 纯函数，便于单测锁定「悬浮窗不得再凭空造一个孤立 session」。
 */
export function resolveSharedSessionId(
  activeId: string | null | undefined,
  fallback: string,
): string {
  return typeof activeId === 'string' && activeId.trim() ? activeId : fallback
}

/**
 * 计算「本地消息里尚未写进共享会话」的起点下标。
 *
 * 返回 `storeMessages.length` → 本地是共享会话的超集（前缀一致），可从该下标续写；
 * 返回 `-1` → 两者不是前缀关系，**禁止回写**。
 *
 * 为什么必须有这道闸（不是防御性冗余）：
 *  切换会话那一帧，React 先跑 `setSessionId(next)`，本地 `messages` 还挂着**上一个**
 *  会话的消息，而「回写」effect 同时被触发 —— 若按下标硬写，就会把旧会话的消息
 *  整段灌进新会话（跨会话串写，真实的记忆污染）。前缀判定天然拦下这种帧，
 *  同时也不与另一窗的并发写入打架（本地落后 → -1 → 跳过，符合「不做并发双写」）。
 */
export function sharedAppendStart(
  storeMessages: ReadonlyArray<{ role: string; content: string }>,
  localMessages: ReadonlyArray<{ role: string; content: string }>,
): number {
  if (localMessages.length < storeMessages.length) return -1
  for (let i = 0; i < storeMessages.length; i++) {
    const s = storeMessages[i]
    const l = localMessages[i]
    if (!l || s.role !== l.role || s.content !== l.content) return -1
  }
  return storeMessages.length
}

/**
 * 读取跨窗共享的会话状态源（Electron → store.json；浏览器 → localStorage）。
 * 任何失败/不可用都回 null，由调用方决定是否沿用内存值——**读取失败不得打断对话**。
 */
export async function readSharedActiveConversationId(): Promise<string | null> {
  try {
    const ei = window.electronAPI as ElectronAPI | undefined
    if (ei?.fileStore) {
      return parsePersistedActiveConversationId(await ei.fileStore.readFile('store.json'))
    }
    return parsePersistedActiveConversationId(localStorage.getItem('desk-notes-storage'))
  } catch {
    return null
  }
}
