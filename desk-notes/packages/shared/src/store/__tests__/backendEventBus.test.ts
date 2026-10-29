/**
 * B3（前后端对齐规划）：backendEventBus 统一分发注册表测试。
 */
import { describe, it, expect, beforeEach, vi } from 'vitest'
import type { ServerPushEvent } from '../../ws-client'
import {
  onBackendEvent,
  dispatchBackendEvent,
  countBackendEventSubscribers,
  clearBackendEventHandlers,
} from '../backendEventBus'
import { installBackendEvents, useBackendEventsStore } from '../backendEventsStore'

function ev(event_type: string, payload: Record<string, unknown>): ServerPushEvent {
  return { type: 'event', event_type, payload }
}

function resetStore() {
  useBackendEventsStore.setState({
    resumableTasks: [],
    resumableDismissed: false,
    syncConflicts: [],
    sandboxTimeouts: [],
    jobAlerts: [],
    morningBrief: null,
    recentExecSteps: [],
  })
}

describe('backendEventBus', () => {
  beforeEach(() => {
    clearBackendEventHandlers()
  })

  it('订阅/分发/注销', () => {
    const handler = vi.fn()
    const off = onBackendEvent('sync.completed', handler)
    expect(countBackendEventSubscribers('sync.completed')).toBe(1)

    dispatchBackendEvent(ev('sync.completed', { push_count: 1 }))
    expect(handler).toHaveBeenCalledWith({ push_count: 1 }, expect.objectContaining({ event_type: 'sync.completed' }))

    off()
    dispatchBackendEvent(ev('sync.completed', { push_count: 2 }))
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('未订阅的事件类型静默忽略', () => {
    expect(() => dispatchBackendEvent(ev('unknown.event', {}))).not.toThrow()
  })

  it('单个 handler 抛错不影响其余订阅者', () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {})
    const good = vi.fn()
    onBackendEvent('sync.failed', () => { throw new Error('boom') })
    onBackendEvent('sync.failed', good)
    dispatchBackendEvent(ev('sync.failed', { error: 'x' }))
    expect(good).toHaveBeenCalledTimes(1)
    err.mockRestore()
  })

  it('installBackendEvents 经 bus 分发：store 落点 + 组件订阅共存', () => {
    resetStore()
    // fake client：仅实现 on()，通配符订阅收全部事件
    const handlers = new Map<string, Array<(e: ServerPushEvent) => void>>()
    const client = {
      on(eventType: string, handler: (e: ServerPushEvent) => void) {
        if (!handlers.has(eventType)) handlers.set(eventType, [])
        handlers.get(eventType)!.push(handler)
        return () => {
          const arr = handlers.get(eventType)
          if (arr) handlers.set(eventType, arr.filter((h) => h !== handler))
        }
      },
    } as never as import('../../ws-client').WsClient

    const uninstall = installBackendEvents(client)

    // 组件级订阅（B3 新入口）
    const onSync = vi.fn()
    const offComponent = onBackendEvent('sync.completed', onSync)

    // 通过通配符处理器分发（模拟后端推送）
    const wildcard = handlers.get('*') ?? []
    expect(wildcard.length).toBe(1)
    wildcard[0](ev('sync.completed', { push_count: 3 }))
    wildcard[0](ev('sync.conflict', { resource: 'note', id: 'c1' }))

    expect(onSync).toHaveBeenCalledTimes(1)
    expect(useBackendEventsStore.getState().syncConflicts).toHaveLength(1)

    offComponent()
    uninstall()
    expect(countBackendEventSubscribers('sync.completed')).toBe(0)
    expect(countBackendEventSubscribers('sync.conflict')).toBe(0)
  })
})
