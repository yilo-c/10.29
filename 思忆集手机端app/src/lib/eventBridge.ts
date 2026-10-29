/**
 * Event bridge — 把后端 WebSocket 推送的 `type=event` 消息映射到手机端状态/通知。
 *
 * 注册一次即可（App 启动时调用 `registerEventBridge()`），返回的 cleanup 在卸载或重新配对时调用。
 * 所有订阅都通过 `onEvent` 走 shared WsClient；当 WS 断开时只是收不到事件，不会抛错。
 */
import { onEvent } from './rpcClient'
import { useMobileStore } from '../store/mobileStore'
import { useStore } from '@desk-notes/shared'
import type { ServerPushEvent } from '@desk-notes/shared'

type EventHandler = (event: ServerPushEvent) => void

/** 简易事件队列：按事件类型让组件订阅（不依赖 zustand），用于 toast / 提醒铃声等瞬态 UI。 */
const externalListeners = new Map<string, Set<EventHandler>>()

export function onBackendEvent(eventType: string, handler: EventHandler): () => void {
  let bucket = externalListeners.get(eventType)
  if (!bucket) {
    bucket = new Set()
    externalListeners.set(eventType, bucket)
  }
  bucket.add(handler)
  return () => {
    bucket?.delete(handler)
  }
}

function fanOut(eventType: string, event: ServerPushEvent): void {
  const bucket = externalListeners.get(eventType)
  if (!bucket) return
  for (const h of bucket) {
    try { h(event) } catch { /* never let a listener crash the bridge */ }
  }
}

interface CleanupRegistry {
  cleanup: () => void
}

let installed: CleanupRegistry | null = null

/**
 * 在 App 启动时调用一次。重复调用会先清掉旧订阅再重建（用于切换配对场景）。
 */
export function registerEventBridge(): () => void {
  if (installed) {
    installed.cleanup()
    installed = null
  }

  const unsubs: Array<() => void> = []

  /* ─── 同步事件 ─── */
  unsubs.push(onEvent('sync.completed', (event) => {
    const payload = (event.payload ?? {}) as Record<string, unknown>
    const cursor = typeof payload.cursor === 'number'
      ? new Date(payload.cursor * 1000).toISOString()
      : new Date().toISOString()
    const store = useMobileStore.getState()
    store.setSyncStatus('success')
    store.setLastSyncTime(cursor)
    fanOut('sync.completed', event)
  }))

  unsubs.push(onEvent('sync.failed', (event) => {
    useMobileStore.getState().setSyncStatus('error')
    fanOut('sync.failed', event)
  }))

  /* ─── 收件箱新增（桌面/AI 通道写入） ─── */
  unsubs.push(onEvent('inbox.new', (event) => {
    fanOut('inbox.new', event)
  }))

  /* ─── 沙箱审批请求 ─── */
  unsubs.push(onEvent('sandbox.requested', (event) => {
    // 异步刷新 pending 列表
    void useMobileStore.getState().fetchApprovals().catch(() => { /* swallow */ })
    fanOut('sandbox.requested', event)
  }))

  /* ─── 能力提案 ─── */
  unsubs.push(onEvent('capability.requested', (event) => {
    fanOut('capability.requested', event)
  }))

  /* ─── 记忆浮现 ─── */
  unsubs.push(onEvent('memory.surface', (event) => {
    fanOut('memory.surface', event)
  }))

  /* ─── 天气刷新 ─── */
  unsubs.push(onEvent('weather.refreshed', (event) => {
    fanOut('weather.refreshed', event)
  }))

  /* ─── 提醒触发 ─── */
  unsubs.push(onEvent('reminder.fired', (event) => {
    fanOut('reminder.fired', event)
  }))

  /* ─── 心情 / Agent 状态变化 ─── */
  unsubs.push(onEvent('mood.changed', (event) => {
    fanOut('mood.changed', event)
  }))
  unsubs.push(onEvent('agent.state.changed', (event) => {
    fanOut('agent.state.changed', event)
  }))

  /* ─── 使用统计更新（异步看板） ─── */
  unsubs.push(onEvent('usage.stats.updated', (event) => {
    fanOut('usage.stats.updated', event)
  }))

  /* ─── 设置远端变更（其它设备改了 settings.set） ─── */
  unsubs.push(onEvent('settings.changed', (event) => {
    // 由 settingsBridge 拦截标志位避免回环；这里只做扇出。
    fanOut('settings.changed', event)
  }))

  /* ─── 通用：通配符订阅一次，用于调试 ─── */
  unsubs.push(onEvent('*', (event) => {
    // 在 DEV 模式下打日志，发布时静默
    if (import.meta.env?.DEV) {
      // eslint-disable-next-line no-console
      console.debug('[siyi event]', event.event_type, event.payload)
    }
  }))

  // 防止笔记/任务等共享 store 被静默使用：占位调用让 tree-shake 不去掉。
  void useStore

  const cleanup = () => {
    for (const u of unsubs) {
      try { u() } catch { /* ignore */ }
    }
  }
  installed = { cleanup }
  return cleanup
}

/** 测试 / 解除配对时手动卸载 */
export function unregisterEventBridge(): void {
  if (installed) {
    installed.cleanup()
    installed = null
  }
}
