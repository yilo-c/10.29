import { getWsClient } from './ai'
import { enqueueSyncOp, getPendingSyncCount } from './syncQueue'
import { errorStore } from '../store/errorStore'
import { tStatic } from '../i18n'

/**
 * Fire-and-forget backend sync via WebSocket JSON-RPC — with durability.
 *
 * 1.1（upgrade-plan 阶段一）：旧实现断连时直接丢调用，笔记/待办写操作表面
 * 成功实际永久丢失。现在：
 * - 已连接：直发；失败入队（由 syncQueue 重试/上限/报错收口）
 * - 未连接/后端不可用：入持久化队列（localStorage），重连后 FIFO 补发
 * - C-2：入队瞬间给出 info 提示"已暂存本地"。防刷屏策略：仅在队列为空时
 *   提示一次（0→1 转变），队列清空后自然重新允许——无需模块级订阅，
 *   避免在模块求值期触碰 syncQueue 内部状态（会踩循环导入 TDZ）。
 */

function notifyStashed(): void {
  if (getPendingSyncCount() > 0) return // 队列非空 = 本轮积压已提示过
  errorStore.info(tStatic('syncQueue.stashed'))
}

export function syncCall(method: string, params: Record<string, unknown>): void {
  try {
    const client = getWsClient()
    if (client.status === 'connected') {
      client.call(method, params).catch((e: unknown) => {
        console.warn('[sync]', method, e instanceof Error ? e.message : e)
        notifyStashed()
        enqueueSyncOp(method, params)
      })
      return
    }
    notifyStashed()
    enqueueSyncOp(method, params)
  } catch {
    // backend 单例都拿不到（极端场景）——仍然入队，别丢用户数据
    try { notifyStashed(); enqueueSyncOp(method, params) } catch { /* localStorage 也不可用 */ }
  }
}
