/**
 * U1 等待阶段推断 resolveWaitingStage + useWaitingStage：
 * - tool/confirm 事件 → tooling（U1 扩白名单后内核工具帧才进得来 events）
 * - notice phase=waiting → retrieving，不回落「分析中(thinking)」
 * - notice phase=starting/started → starting；running → thinking
 * - 事件流清空 → 回落到本地 elapsed 分段
 * - 后端 notice message 被透传为 lastNoticeMessage
 */
import { describe, it, expect } from 'vitest'
import { resolveWaitingStage, WAITING_STAGE_TEXT } from '../useWaitingStage'
import type { ChatStreamEvent } from '../useChatStream'

const notice = (phase: string, message: string): ChatStreamEvent =>
  ({ type: 'notice', data: JSON.stringify({ phase, status: 'running', message }) })

describe('resolveWaitingStage 事件映射', () => {
  it('tool 事件 → tooling（优先于 elapsed 兜底）', () => {
    const events: ChatStreamEvent[] = [{ type: 'tool', data: '{"tool":"siyi_notes_search"}' }]
    const r = resolveWaitingStage(events, 120) // elapsed 本会推成 summarizing
    expect(r.stage).toBe('tooling')
  })

  it('confirm 事件 → tooling', () => {
    const events: ChatStreamEvent[] = [{ type: 'confirm', data: '{"confirmation_id":"c1"}' }]
    expect(resolveWaitingStage(events, 60).stage).toBe('tooling')
  })

  it('phase=waiting → retrieving，不回落「分析中」', () => {
    const events = [notice('waiting', '执行内核仍在执行（已等待 30 秒）…')]
    const r = resolveWaitingStage(events, 30)
    expect(r.stage).toBe('retrieving')
    expect(r.stage).not.toBe('thinking')
    expect(r.lastNoticeMessage).toContain('执行内核仍在执行')
  })

  it('phase=starting/started → starting；running → thinking', () => {
    expect(resolveWaitingStage([notice('starting', '正在启动内核并分析任务…')], 1).stage).toBe('starting')
    expect(resolveWaitingStage([notice('started', 'x')], 10).stage).toBe('starting')
    expect(resolveWaitingStage([notice('running', 'x')], 10).stage).toBe('thinking')
  })

  it('最新一条映射事件胜出（工具后又来 waiting notice → retrieving）', () => {
    const events: ChatStreamEvent[] = [
      { type: 'tool', data: '{}' },
      notice('waiting', '仍在执行…'),
    ]
    // 从后往前扫，waiting notice 比 tool 更新 → retrieving
    expect(resolveWaitingStage(events, 40).stage).toBe('retrieving')
  })

  it('无映射事件 → 回落本地 elapsed 分段', () => {
    expect(resolveWaitingStage([], 2).stage).toBe('starting')
    expect(resolveWaitingStage([], 10).stage).toBe('thinking')
    expect(resolveWaitingStage([{ type: 'planner', data: '{}' }], 50).stage).toBe('summarizing')
  })

  it('事件流清空后恢复本地推断（tool 事件移除 → 回 elapsed）', () => {
    const before: ChatStreamEvent[] = [{ type: 'tool', data: '{}' }]
    expect(resolveWaitingStage(before, 50).stage).toBe('tooling')
    expect(resolveWaitingStage([], 50).stage).toBe('summarizing')
  })

  it('文案表覆盖全阶段', () => {
    expect(WAITING_STAGE_TEXT.starting).toContain('启动内核')
    expect(WAITING_STAGE_TEXT.retrieving).toContain('检索')
    expect(WAITING_STAGE_TEXT.thinking).toContain('分析')
    expect(WAITING_STAGE_TEXT.tooling).toContain('工具')
    expect(WAITING_STAGE_TEXT.summarizing).toContain('汇总')
  })
})
