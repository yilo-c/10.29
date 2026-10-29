/**
 * C3（前后端对齐规划）：统一时间格式化工具层。
 *
 * 背景：时间格式化手写散落十余处（ChatView/CloudSyncPanel/NotesBrowser/
 * Dashboard 等），聊天记录跨天消息只显示 HH:MM 缺日期。本层提供
 * 单一实现，所有新代码必须从这里取；旧代码随迭代逐步迁入。
 *
 * 全部函数接受可选 now 参数（测试注入固定基准，避免时间炸弹）。
 */

function toDate(ts: number | string | Date): Date {
  return ts instanceof Date ? ts : new Date(ts)
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

/** HH:mm */
export function formatTimeHM(ts: number | string | Date): string {
  const d = toDate(ts)
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** YYYY-MM-DD HH:mm */
export function formatDateTime(ts: number | string | Date): string {
  const d = toDate(ts)
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/** MM-DD */
export function formatDateMD(ts: number | string | Date): string {
  const d = toDate(ts)
  return `${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
}

/**
 * 消息时间戳（聊天气泡）：
 * 今天 → HH:mm；昨天 → 昨天 HH:mm；今年 → MM-DD HH:mm；跨年 → YYYY-MM-DD HH:mm。
 * 修复旧实现跨天消息只显示时分、缺日期的问题。
 */
export function formatMessageTimestamp(ts: number | string | Date, now: Date = new Date()): string {
  const d = toDate(ts)
  if (Number.isNaN(d.getTime())) return ''
  if (isSameDay(d, now)) return formatTimeHM(d)
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (isSameDay(d, yesterday)) return `昨天 ${formatTimeHM(d)}`
  if (d.getFullYear() === now.getFullYear()) return `${formatDateMD(d)} ${formatTimeHM(d)}`
  return formatDateTime(d)
}

/**
 * 相对时间（通知/动态流）：
 * <1min 刚刚；<1h X分钟前；<24h X小时前；昨天；今年 MM-DD；跨年 YYYY-MM-DD。
 */
export function formatRelative(ts: number | string | Date, now: Date = new Date()): string {
  const d = toDate(ts)
  if (Number.isNaN(d.getTime())) return ''
  const diffMs = now.getTime() - d.getTime()
  if (diffMs < 60_000) return '刚刚'
  if (diffMs < 3_600_000) return `${Math.floor(diffMs / 60_000)}分钟前`
  if (diffMs < 86_400_000 && isSameDay(d, now)) return `${Math.floor(diffMs / 3_600_000)}小时前`
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (isSameDay(d, yesterday)) return `昨天 ${formatTimeHM(d)}`
  if (d.getFullYear() === now.getFullYear()) return formatDateMD(d)
  return `${d.getFullYear()}-${formatDateMD(d)}`
}

/**
 * 会话列表时间标签：今天 HH:mm；今年 M/D HH:mm；跨年 YYYY/M/D。
 * （与 ChatView 旧 formatConversationTime 语义一致，供迁移用）
 */
export function formatConversationLabel(ts: number | string | Date, now: Date = new Date()): string {
  const d = toDate(ts)
  if (Number.isNaN(d.getTime())) return ''
  if (isSameDay(d, now)) return formatTimeHM(d)
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}/${d.getDate()} ${formatTimeHM(d)}`
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}
