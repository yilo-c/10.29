import { useEffect, useState } from 'react'
import { WAITING_STAGE_TEXT, type WaitingStage } from './useWaitingStage'

/**
 * U1 等待状态：一行式「已等待 Ns · <阶段文案>」。
 *
 * - 逐秒 tick + document.hidden 暂停（与 ChatView 原 StreamElapsedSeconds 同款逻辑，
 *   提为共享叶子组件后 ChatView 内联气泡可整体替换为这一个）。
 * - 有后端 notice message 时优先展示后端文案（lastMessage），否则展示 stage 映射文案。
 * - startedAt 为 null（非流式中）时渲染 idle 占位（一行极淡文案，不崩）。
 */
export interface WaitingStatusProps {
  startedAt: number | null
  stage: WaitingStage
  /** 后端 notice 携带的用户可见文案（优先于本地阶段推断展示）。 */
  lastMessage?: string
}

/** 逐秒推进的已等待秒数（页面隐藏时暂停计时，恢复可见立即刷新一次）。 */
function useElapsedSeconds(startedAt: number | null): number {
  const [, tick] = useState(0)
  useEffect(() => {
    if (startedAt === null) return
    let timer: ReturnType<typeof setInterval> | null = null
    const start = () => { if (timer === null) timer = setInterval(() => tick((s) => s + 1), 1000) }
    const stop = () => { if (timer !== null) { clearInterval(timer); timer = null } }
    const onVisibility = () => { if (document.hidden) { stop() } else { tick((s) => s + 1); start() } }
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility) }
  }, [startedAt])
  return startedAt ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000)) : 0
}

export function WaitingStatus({ startedAt, stage, lastMessage }: WaitingStatusProps) {
  const sec = useElapsedSeconds(startedAt)
  const text = (lastMessage && lastMessage.trim()) ? lastMessage.trim() : WAITING_STAGE_TEXT[stage]
  // 非流式（startedAt=null 且无文案）时不占位，避免历史页留下幽灵行。
  if (startedAt === null && !text) return null
  return (
    <span
      data-testid="waiting-status"
      className="inline-flex items-center gap-1.5"
    >
      <span className="w-2 h-2 rounded-full bg-brand animate-pulse" />
      <span data-testid="waiting-elapsed" className="text-2xs text-text-dim tabular-nums">已等待 {sec}s</span>
      {text && (
        <>
          <span className="text-text-dim">·</span>
          <span className="font-medium text-text-secondary">{text}</span>
        </>
      )}
    </span>
  )
}
