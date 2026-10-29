import { useEffect, useMemo, useState } from 'react'
import type { ChatStreamEvent } from './useChatStream'
import { parseStreamNotice } from './StreamNotice'

/**
 * U1「等待状态收口」：把「AI 正在忙什么」的阶段推断集中到一处。
 *
 * 优先级：
 *  1. 后端事件驱动的显式映射（最新一条有意义事件胜出）
 *     - tool / confirm / tool_progress 事件 → 'tooling'（内核工具调用/待确认；U1 扩白名单
 *       后 tool/confirm 才进得来，F-P0 再把 tool_progress 并入——悬浮助手是 chat 的另一
 *       形态，内核真跑了工具必须显示「正在调用工具…」而不是回落「正在分析…」）
 *     - notice phase: starting/started → 'starting'；running → 'thinking'；
 *       waiting → 'retrieving'（内核「仍在执行」的长等待，绝不回落成「分析中」——
 *       这是本轮 bug 的核心：dsh 伪流式只发 waiting notice，旧 UI 全程显示思考中）。
 *  2. 本地 elapsed 分段兜底（无任何后端映射命中时按已耗时推断）：
 *     ≤3s starting、<15s thinking、<45s retrieving、之后 summarizing。
 *
 * 计时一律前端 startedAt 派生（后端 checkpoint.py:48-51 的 waiting_since 是谎言 schema，
 * 禁止使用）。收不到 notice 也不报错——与 useChatStream 的 120s idle 看门狗解耦。
 */

export type WaitingStage = 'idle' | 'starting' | 'retrieving' | 'thinking' | 'tooling' | 'summarizing'

/** 阶段 → 硬编码中文文案（与仓库现状一致，i18n 属禁区）。 */
export const WAITING_STAGE_TEXT: Record<WaitingStage, string> = {
  idle: '',
  starting: '正在启动内核…',
  retrieving: '正在检索笔记与记忆…',
  thinking: '正在分析…',
  tooling: '正在调用工具…',
  summarizing: '正在汇总结果…',
}

/** notice phase → 阶段映射（未列出的 phase 不参与映射，回落本地推断）。 */
const STAGE_BY_PHASE: Record<string, WaitingStage> = {
  starting: 'starting',
  started: 'starting',
  running: 'thinking',
  waiting: 'retrieving',
}

/** 本地 elapsed 分段兜底（秒）。 */
export function stageFromElapsed(elapsedSec: number): WaitingStage {
  if (elapsedSec <= 3) return 'starting'
  if (elapsedSec < 15) return 'thinking'
  if (elapsedSec < 45) return 'retrieving'
  return 'summarizing'
}

export interface WaitingStageResult {
  stage: WaitingStage
  /** 后端 notice 携带的用户可见文案；非空时 WaitingStatus 优先展示它而非阶段文案。 */
  lastNoticeMessage: string
}

/**
 * 纯函数：由事件流 + 已耗时派生等待阶段与后端文案。单测可直接调用，无需渲染。
 * 扫描顺序：从最新事件往回找第一条「有映射意义」的事件决定 stage；同时记录最近一条
 * notice 的可展示文案（无论是否命中 phase 映射，只要有 message/friendly 就用于覆盖）。
 */
export function resolveWaitingStage(events: ChatStreamEvent[], elapsedSec: number): WaitingStageResult {
  let stage: WaitingStage | null = null
  let lastNoticeMessage = ''
  // 从后往前扫：最新一条有意义的映射事件胜出。
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i]
    if (ev.type === 'tool' || ev.type === 'confirm' || ev.type === 'tool_progress') {
      if (stage === null) stage = 'tooling'
      continue
    }
    if (ev.type === 'notice') {
      const info = parseStreamNotice(ev.data)
      // 后端可见文案优先取 notice.friendly（parseStreamNotice 已按 message/friendly/reason 归一）。
      if (!lastNoticeMessage && info.friendly) lastNoticeMessage = info.friendly
      if (stage === null) {
        const mapped = info.phase ? STAGE_BY_PHASE[info.phase] : undefined
        if (mapped) stage = mapped
      }
    }
  }
  return {
    stage: stage ?? stageFromElapsed(elapsedSec),
    lastNoticeMessage,
  }
}

/**
 * 逐秒推进的等待阶段 hook：内部按秒 tick 让 elapsed 分段兜底随时间推进
 * （starting→thinking→retrieving→summarizing），页面隐藏时暂停避免后台空转。
 */
export function useWaitingStage(events: ChatStreamEvent[], startedAt: number | null): WaitingStageResult {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (startedAt === null) return
    let timer: ReturnType<typeof setInterval> | null = null
    const start = () => { if (timer === null) timer = setInterval(() => setNow(Date.now()), 1000) }
    const stop = () => { if (timer !== null) { clearInterval(timer); timer = null } }
    const onVisibility = () => { if (document.hidden) { stop() } else { setNow(Date.now()); start() } }
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => { stop(); document.removeEventListener('visibilitychange', onVisibility) }
  }, [startedAt])

  return useMemo(() => {
    if (startedAt === null) return { stage: 'idle' as WaitingStage, lastNoticeMessage: '' }
    const elapsed = Math.max(0, Math.floor((now - startedAt) / 1000))
    return resolveWaitingStage(events, elapsed)
  }, [events, startedAt, now])
}
