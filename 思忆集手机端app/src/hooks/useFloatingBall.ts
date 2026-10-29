/* ── 悬浮球 + 小问情绪同步 ── */

import { useEffect, useCallback, useRef, useState } from 'react'
import { useStore } from '@desk-notes/shared'
import { floatingBall, type FloatingBallMood } from '../lib/floatingBall'
import { useMobileStore } from '../store/mobileStore'
import { swallowError } from '../lib/swallowError'

/** 后端 mood → 悬浮球 mood */
const BACKEND_MOOD_MAP: Record<string, FloatingBallMood> = {
  neutral: 'idle',
  happy: 'happy',
  success: 'happy',
  proud: 'happy',
  thinking: 'thinking',
  alert: 'alert',
  worried: 'alert',
}

interface UseFloatingBallOptions {
  autoInit?: boolean
  /** 是否连接后端 mood.changed 事件 */
  enableXiaoWenMood?: boolean
}

export function useFloatingBall(options?: UseFloatingBallOptions) {
  const { autoInit = true, enableXiaoWenMood = true } = options || {}
  const inited = useRef(false)
  const currentMood = useRef<FloatingBallMood>('idle')
  const [wsConnected, setWsConnected] = useState(false)

  const show = useCallback(async () => floatingBall.show(), [])
  const hide = useCallback(async () => floatingBall.hide(), [])
  const setMood = useCallback(async (mood: FloatingBallMood) => {
    currentMood.current = mood
    await floatingBall.setMood(mood)
  }, [])
  const setBadge = useCallback(async (count: number) => {
    await floatingBall.setBadge(count)
  }, [])

  /**
   * 绑定悬浮球的交互行为。
   *
   * 关键：悬浮球在 App 之外悬停，用户点它是想「快速做点什么」。
   * 这里把单击接到 AI 对话、双击接到快速记录，
   * 否则球只是能拖动的装饰。
   */
  useEffect(() => {
    floatingBall.setHandlers({
      onTap: () => {
        try {
          // 切到 AI 标签页并聚焦输入框
          const store = useMobileStore.getState()
          store.requestTab?.('ai')
        } catch { /* store 未就绪 */ }
      },
      onDoubleTap: () => {
        try {
          useMobileStore.getState().requestQuickNote?.()
        } catch { /* ignore */ }
      },
    })
  }, [])

  // ── 生命周期 ──
  useEffect(() => {
    if (!autoInit || inited.current) return
    inited.current = true

    floatingBall.init().catch(() => {})

    return () => {
      floatingBall.destroy().catch(() => {})
      inited.current = false
    }
  }, [autoInit])

  // ── 待办角标同步 ──
  useEffect(() => {
    const unsub = useStore.subscribe(state => {
      const pending = state.todos.filter((t: { done: boolean }) => !t.done).length
      floatingBall.setBadge(pending).catch(() => {})
    })
    return unsub
  }, [])

  // ── 后端 mood.changed 事件监听（小问情绪同步） ──
  useEffect(() => {
    if (!enableXiaoWenMood) return
    let cancelled = false
    let unsubEvent: (() => void) | null = null
    let unsubStatus: (() => void) | null = null

    async function connect() {
      try {
        const { onEvent, onConnectionStatusChange, getConnectionStatus } = await import('../lib/rpcClient')

        setWsConnected(getConnectionStatus() === 'connected')
        unsubStatus = onConnectionStatusChange(s => {
          if (!cancelled) setWsConnected(s === 'connected')
        })

        unsubEvent = onEvent('mood.changed', (event: any) => {
          if (cancelled) return
          const mapped = BACKEND_MOOD_MAP[event?.payload?.mood] || 'idle'
          currentMood.current = mapped
          floatingBall.setMood(mapped)
        })
      } catch (e) { /* offline */ swallowError('useFloatingBall:84', e) }
    }

    connect()
    return () => {
      cancelled = true
      unsubEvent?.()
      unsubStatus?.()
    }
  }, [enableXiaoWenMood])

  return { show, hide, setMood, setBadge, wsConnected }
}
