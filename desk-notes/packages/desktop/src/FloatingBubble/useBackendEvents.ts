import { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { getWsClient } from '../utils/ai'
import type { PetMood } from '../types/pet'

/** 浏览器桥五事件名 — 与后端 emit 一致（M1 订阅接线）。 */
export const BROWSER_EVENT_TYPES = [
  'browser.captured',
  'browser.summarized',
  'browser.note_created',
  'browser.task_created',
  'browser.reading_saved',
] as const

export type BrowserEventType = typeof BROWSER_EVENT_TYPES[number]

/** A server-push event from the backend EventBus → WebSocket bridge. */
export interface BackendEvent {
  type: 'event'
  event_type: string
  payload: Record<string, unknown>
  source?: string
}

/** Typed event payloads the frontend cares about. */
export interface MoodChangedPayload {
  mood: PetMood
  reason?: string
}

export interface ProactiveSuggestionPayload {
  id: string
  icon: string
  title: string
  description: string
  priority: number
  actionType?: string
  actionPayload?: string
}

export interface BackendEvents {
  'mood.changed': MoodChangedPayload
  'proactive.suggestion': ProactiveSuggestionPayload
  'info.collected': Record<string, unknown>
  'inbox.delivered': { item_id: string; category: string; title: string; content_preview: string; timestamp: string }
  'agent.chain.started': { chain_id: string; goal: string; total_steps: number }
  'agent.chain.node.started': { chain_id: string; node_id: string; description: string; action_id: string; risk_level: string }
  'agent.chain.node.completed': { chain_id: string; node_id: string; description: string }
  'agent.chain.node.failed': { chain_id: string; node_id: string; description: string; error: string }
  'agent.chain.completed': { chain_id: string; status: string; total: number; completed: number; failed: number; goal: string }
  'workflow.progress': { step: number; total: number; label: string; status: string }
  'workflow.completed': { result: Record<string, unknown>; title: string }
  'task.progress': { task_id: string; step: number; total: number; message: string; seq: number }
  'vocabulary.due_reminder': { due_count: number; today: string; message: string }
  'vocabulary.review.recorded': { word_id: number; word: string; quality: number; repetitions: number; efactor: number; interval: number; next_review: string }
  'vocabulary.session.summary': { total: number; good: number; forgot: number; duration_seconds: number; ended_reason: string; started_at?: string; ended_at?: string }
  'vocabulary.word_added': { word_id: number; word: string; wordbook_id: number; source: string }
  'reading.words_proposed': {
    article_id: string
    title: string
    count: number
    words: Array<{ word: string; phonetic: string; meaning: string; context: string }>
  }
  'vocabulary.dictation.checked': { word_id: number; word: string; correct: boolean; exact: boolean; suggested_quality: number }
  // 云同步事件（cloud_sync_client.sync_once 结束时推送）
  'sync.completed': {
    device_id: string
    push_ok: boolean
    pull_ok: boolean
    push_count: number
    pull_count: number
    cursor: number
  }
  'sync.failed': {
    device_id: string
    // push/pull 部分失败时携带 push_ok/pull_ok/push_count/pull_count/cursor
    push_ok?: boolean
    pull_ok?: boolean
    push_count?: number
    pull_count?: number
    cursor?: number
    // 认证错误/通用异常时携带 error；认证错误额外 fatal:true 并停止循环
    error?: string
    fatal?: boolean
  }
  // ── 浏览器桥五事件（M1/V1-7 接线）──
  // 事件名与后端 emit 一致：browser/bridge_content.py:116/184/231/287、
  // browser/_bridge_summary.py:287 → core/events event_bus → ws_server 推送。
  'browser.captured': { title: string; url: string; entry_id?: string; contentKind?: string; executedRecommendedAction?: string }
  'browser.summarized': { title: string; url: string; summary: string; warnings?: string[]; contentKind?: string; keyPoints?: string[]; actionHints?: string[] }
  'browser.note_created': { title: string; url: string; note_id?: string; contentKind?: string; executedRecommendedAction?: string }
  'browser.task_created': { title: string; url: string; task_id?: string; contentKind?: string; executedRecommendedAction?: string }
  'browser.reading_saved': { title: string; url: string; article_id?: string; source?: string }
  // 通知推送通道（P2.9）：notification.delivered 为完整通知 dict，notification.toast 仅 high/urgent
  'notification.delivered': {
    id: string
    event_type: string
    title: string
    body: string
    priority: string
    created_at: string
    read: boolean
  }
  'notification.toast': {
    id?: string
    event_type?: string
    title: string
    body: string
    priority: string
    created_at?: string
  }
  // ── 第二档 #4/#5：前端消费 / 上报的事件 ──
  // clipboard.changed：后端剪贴板轮询 emit（services.clipboard_context），
  //   EventBridge 推到前端 → 聊天框弹出"附为上下文"提示。
  'clipboard.changed': { text: string }
  // selection.detected / foreground.window.changed / url.intercepted：前端经
  //   event.emit RPC 上报到后端 EventBus，触发主动性引擎（第二档 #5）。
  'selection.detected': { text: string }
  'foreground.window.changed': { title: string; app?: string }
  'url.intercepted': { url: string; title?: string }
}

export interface InfoCollectedPayload {
  brief: string
  items: unknown[]
  errors: { source: string; error: string }[]
}

/**
 * Subscribe to backend server-push events via WebSocket EventBridge.
 *
 * ```ts
 * const { lastEvent, on } = useBackendEvents()
 *
 * // Latest mood.changed event
 * if (lastEvent('mood.changed')?.payload.mood === 'happy') { ... }
 *
 * // Subscribe to specific event types
 * useEffect(() => on('proactive.suggestion', (ev) => { ... }), [])
 * ```
 */
export function useBackendEvents() {
  const [connected, setConnected] = useState(false)
  const lastEventsRef = useRef<Map<string, BackendEvent>>(new Map())
  const [, forceUpdate] = useState(0)

  useEffect(() => {
    const client = getWsClient()
    setConnected(client.status === 'connected')

    const unsubStatus = client.onStatusChange((s) => {
      setConnected(s === 'connected')
    })

    // Use '*' wildcard — WsClient._dispatchEvent dispatches by event_type (e.g.
    // 'mood.changed'), never by the literal string 'event'. Subscribing to
    // 'event' would never fire, causing ALL backend push events to be lost.
    const unsubEvent = client.on('*', (event) => {
      lastEventsRef.current.set(event.event_type, event as BackendEvent)
      forceUpdate(n => n + 1)
    })

    return () => {
      unsubStatus()
      unsubEvent()
    }
  }, [])

  /** Get the last received event of a given type. */
  const lastEvent = useCallback((eventType: string): BackendEvent | undefined => {
    return lastEventsRef.current.get(eventType)
  }, [])

  /** Get all unread events of a given type since last acknowledged. */
  const consumeEvents = useCallback((eventType: string): BackendEvent[] => {
    const all: BackendEvent[] = []
    for (const [key, ev] of lastEventsRef.current) {
      if (key === eventType) all.push(ev)
    }
    lastEventsRef.current.delete(eventType)
    return all
  }, [])

  return { connected, lastEvent, consumeEvents }
}

/**
 * 订阅后端浏览器桥五事件（browser.captured / summarized / note_created /
 * task_created / reading_saved）。返回卸载函数。与 useBackendEvents 的
 * '*' 通配缓存互补：通配供 lastEvent 拉取，此 API 供实时推送处理。
 */
export function subscribeBrowserEvents(handler: (event: BackendEvent) => void): () => void {
  const client = getWsClient()
  const unsubs = BROWSER_EVENT_TYPES.map(t =>
    client.on(t, (event) => handler(event as BackendEvent)),
  )
  return () => { for (const u of unsubs) u() }
}

/** React 包装：组件挂载期间实时接收 browser.* 事件。 */
export function useBrowserEvents(handler: (event: BackendEvent) => void): void {
  const handlerRef = useRef(handler)
  handlerRef.current = handler
  useEffect(() => subscribeBrowserEvents(ev => handlerRef.current(ev)), [])
}

export interface BrowserContext {
  title: string
  url: string
  lastEventType: BrowserEventType | ''
}

/** 最近一次浏览器桥事件提供的页面感知（标题/URL），供悬浮层注入上下文。 */
export function useBrowserContext(): BrowserContext {
  const { lastEvent } = useBackendEvents()
  return useMemo<BrowserContext>(() => {
    for (const t of BROWSER_EVENT_TYPES) {
      const ev = lastEvent(t)
      if (ev?.payload) {
        return {
          title: String(ev.payload.title ?? ''),
          url: String(ev.payload.url ?? ''),
          lastEventType: t,
        }
      }
    }
    return { title: '', url: '', lastEventType: '' }
  }, [lastEvent])
}

/**
 * 订阅单个后端 server-push 事件（第二档 #4 使用）。
 *
 * 与 useBackendEvents 的 '*' 全量缓存不同，此 hook 只订阅指定事件类型，并通过
 * `seq`（每次新事件自增）让消费方可靠地区分"新到达"与"已有缓存"，避免重复消费。
 *
 * ```ts
 * const { payload, seq } = useBackendEvent('clipboard.changed')
 * useEffect(() => { if (payload?.text) showHint(payload.text) }, [seq])
 * ```
 */
export function useBackendEvent(eventType: string): {
  payload: Record<string, unknown> | null
  seq: number
} {
  const [state, setState] = useState<{ payload: Record<string, unknown> | null; seq: number }>({
    payload: null,
    seq: 0,
  })
  useEffect(() => {
    const client = getWsClient()
    const handler = (event: BackendEvent) => {
      setState((s) => ({
        payload: (event?.payload as Record<string, unknown>) ?? {},
        seq: s.seq + 1,
      }))
    }
    const unsub = client.on(eventType, handler)
    return () => {
      try {
        unsub()
      } catch {
        /* ignore */
      }
    }
  }, [eventType])
  return state
}

