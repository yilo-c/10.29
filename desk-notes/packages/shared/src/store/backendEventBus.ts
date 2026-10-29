/**
 * B3（前后端对齐规划）：后端事件统一分发注册表（事件订阅层转正）。
 *
 * A1 过渡层只有"事件 → backendEventsStore"一条固定管道；组件若要对
 * 事件做反应（刷新列表、弹反馈），只能各自 client.on('event') 过滤，
 * 即前端报告技术债③的"谁需要谁订阅"。
 *
 * 本模块转正为 App 级统一分发：
 * - `onBackendEvent(eventType, handler)`：任意组件注册事件反应（返回注销）
 * - `dispatchBackendEvent(event)`：唯一分发入口（由 installBackendEvents 驱动）
 * - A1 的 store 落点处理器改为通过本注册表注册，不再有旁路
 *
 * 阶段 D 的命令面板/面板组件全部走此入口，禁止再出现散落的
 * client.on('event') 手写过滤。
 */
import type { ServerPushEvent } from '../ws-client'

export type BackendEventHandler = (
  payload: Record<string, unknown>,
  event: ServerPushEvent,
) => void

const handlerMap = new Map<string, Set<BackendEventHandler>>()

/** 订阅指定类型后端事件；返回注销函数。 */
export function onBackendEvent(eventType: string, handler: BackendEventHandler): () => void {
  if (!handlerMap.has(eventType)) handlerMap.set(eventType, new Set())
  handlerMap.get(eventType)!.add(handler)
  return () => {
    handlerMap.get(eventType)?.delete(handler)
  }
}

/** 分发一个后端事件到全部订阅者（单个 handler 抛错不影响其余）。 */
export function dispatchBackendEvent(event: ServerPushEvent): void {
  const handlers = handlerMap.get(event.event_type)
  if (!handlers) return
  for (const h of handlers) {
    try {
      h(event.payload ?? {}, event)
    } catch (err) {
       
      console.error(`[backendEventBus] handler for '${event.event_type}' failed:`, err)
    }
  }
}

/** 当前某事件类型的订阅数（测试/诊断用）。 */
export function countBackendEventSubscribers(eventType: string): number {
  return handlerMap.get(eventType)?.size ?? 0
}

/** 测试辅助：清空全部订阅。 */
export function clearBackendEventHandlers(): void {
  handlerMap.clear()
}
