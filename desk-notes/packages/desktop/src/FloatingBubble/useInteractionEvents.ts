// 第二档 #5：前端 → 后端事件上报（"听事件"信号的来源侧）。
//
// 监听文档内的本地交互信号，经后端 event.emit RPC（白名单：selection.detected /
// foreground.window.changed）把事件注入后端 EventBus，触发 ProactiveTriggerEngine
// 的"听事件"触发器，产出门户管家消息（与 info.brief 同款管道）。
//
// 关键设计：纯逻辑（extractSelectedText / isReportableSelection）与副作用（监听、
// 防抖、RPC）分离，便于单测；hook 支持依赖注入（emit / getSelectionText /
// getWindowTitle 等），测试无需真实 WebSocket。

import { useEffect, useRef } from 'react'
import { getWsClient } from '../utils/ai'

const DEFAULT_SELECTION_MIN = 10
const DEFAULT_SELECTION_MAX = 2000
const DEFAULT_DEBOUNCE_MS = 700

export interface EmitDeps {
  /** 注入式事件发送（测试用）；缺省走 getWsClient().call('event.emit')。 */
  emit?: (eventType: string, payload: Record<string, unknown>) => void
  /** 注入式取选区文本（测试用）；缺省读 window.getSelection()。 */
  getSelectionText?: () => string
  /** 注入式取窗口标题（测试用）；缺省读 document.title。 */
  getWindowTitle?: () => string
  selectionMin?: number
  selectionMax?: number
  debounceMs?: number
}

/** 读取当前文档选区文本（含异常兜底，绝不抛）。 */
export function extractSelectedText(getSelectionText?: () => string): string {
  try {
    const fn =
      getSelectionText ??
      (() =>
        typeof window !== 'undefined' && window.getSelection
          ? window.getSelection()?.toString() ?? ''
          : '')
    return fn().trim()
  } catch {
    return ''
  }
}

/** 选区是否值得上报（长度在 [min, max] 之间）。 */
export function isReportableSelection(
  text: string,
  min = DEFAULT_SELECTION_MIN,
  max = DEFAULT_SELECTION_MAX,
): boolean {
  const t = text.trim()
  return t.length >= min && t.length <= max
}

/**
 * 挂载后持续监听本地交互信号并上报到后端。
 *
 * - 选区变化（mouseup / selectionchange）：防抖后若选区值得上报且不同于上次，发
 *   selection.detected {text}。
 * - 窗口获得焦点（window focus）：发 foreground.window.changed {title, app}。
 */
export function useEmitInteractionEvents(deps: EmitDeps = {}): void {
  const emitRef = useRef(deps.emit)
  emitRef.current = deps.emit
  const selMin = deps.selectionMin ?? DEFAULT_SELECTION_MIN
  const selMax = deps.selectionMax ?? DEFAULT_SELECTION_MAX
  const debounceMs = deps.debounceMs ?? DEFAULT_DEBOUNCE_MS
  const getSelectionText = deps.getSelectionText
  const getWindowTitle = deps.getWindowTitle
  const lastSentTextRef = useRef('')
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const emit = (eventType: string, payload: Record<string, unknown>) => {
      const injected = emitRef.current
      if (injected) {
        injected(eventType, payload)
        return
      }
      const client = getWsClient()
      if (!client || client.status !== 'connected') return
      void client.call('event.emit', { event_type: eventType, payload }).catch(() => {
        /* 上报失败静默：交互信号不可阻断 UX */
      })
    }

    const fireSelectionIfAny = () => {
      const text = extractSelectedText(getSelectionText)
      if (!isReportableSelection(text, selMin, selMax)) return
      if (text === lastSentTextRef.current) return
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => {
        lastSentTextRef.current = text
        emit('selection.detected', { text })
      }, debounceMs)
    }

    const onFocus = () => {
      const title =
        getWindowTitle !== undefined
          ? getWindowTitle()
          : typeof document !== 'undefined'
            ? document.title
            : ''
      emit('foreground.window.changed', { title: title ?? '', app: 'desk-notes' })
    }

    document.addEventListener('mouseup', fireSelectionIfAny)
    document.addEventListener('selectionchange', fireSelectionIfAny)
    window.addEventListener('focus', onFocus)
    return () => {
      document.removeEventListener('mouseup', fireSelectionIfAny)
      document.removeEventListener('selectionchange', fireSelectionIfAny)
      window.removeEventListener('focus', onFocus)
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [getSelectionText, getWindowTitle, selMin, selMax, debounceMs])
}
