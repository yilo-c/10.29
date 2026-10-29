/**
 * V2-02（2026-08-24）：前端行为事件上报，补 C5 采集盲区。
 *
 * 经既有 getWsClient() 调用 usage.record，后端落 usage_stats + behavior_logger
 * （BehaviorEvent privacy=local，仅本地存储、不上云）。
 *
 * 事件命名约定（与计划 V2-02 一致）：
 *   - module.open   ：模块导航（domain=模块名，如 notes/chat/todo/inbox）
 *   - capture.note  ：新建便签
 *   - capture.task  ：新建待办
 *   - capture.inbox ：收件箱新增
 *   - chat.send     ：聊天发送
 *
 * 上报失败静默，不阻断 UI。
 */
import { getWsClient } from './ai'

export function trackUsage(action: string, detail?: { domain?: string }): void {
  try {
    getWsClient()?.call('usage.record', { action, domain: detail?.domain ?? '' }).catch(() => {})
  } catch {
    /* 上报失败静默 */
  }
}
