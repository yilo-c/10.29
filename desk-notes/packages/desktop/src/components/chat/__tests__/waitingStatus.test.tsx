/**
 * U1 等待状态收口：WaitingStatus 一行式「已等待 Ns · <阶段文案>」。
 * - 无事件时逐秒显示已等待秒数（vi.useFakeTimers）
 * - notice 到达时后端 message 覆盖本地推断（lastMessage 优先）
 * - 阶段按 elapsed 分段推进（stageFromElapsed 纯函数 + 组件按 props 渲染）
 * - startedAt=null 且无文案时渲染空、不崩
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { WaitingStatus } from '../WaitingStatus'
import { stageFromElapsed, WAITING_STAGE_TEXT } from '../useWaitingStage'

describe('WaitingStatus 渲染', () => {
  beforeEach(() => { vi.useFakeTimers({ toFake: ['setInterval', 'Date'] }) })
  afterEach(() => { vi.useRealTimers() })

  it('无后端文案时展示阶段文案 + 已等待秒数', () => {
    const startedAt = Date.now() - 5_000
    render(<WaitingStatus startedAt={startedAt} stage="thinking" />)
    const el = screen.getByTestId('waiting-status')
    expect(el.textContent).toContain('已等待 5s')
    expect(el.textContent).toContain(WAITING_STAGE_TEXT.thinking)
  })

  it('逐秒推进：tick 后已等待秒数递增', () => {
    const startedAt = Date.now()
    render(<WaitingStatus startedAt={startedAt} stage="starting" />)
    expect(screen.getByTestId('waiting-elapsed').textContent).toContain('0s')
    act(() => { vi.advanceTimersByTime(3_000) })
    expect(screen.getByTestId('waiting-elapsed').textContent).toContain('3s')
  })

  it('有后端 notice message 时优先展示后端文案覆盖阶段推断', () => {
    const startedAt = Date.now() - 20_000
    render(<WaitingStatus startedAt={startedAt} stage="thinking" lastMessage="执行内核仍在执行（已等待 20 秒）…" />)
    const el = screen.getByTestId('waiting-status')
    expect(el.textContent).toContain('执行内核仍在执行')
    expect(el.textContent).not.toContain(WAITING_STAGE_TEXT.thinking)
  })

  it('startedAt=null 且无文案 → 渲染空、不崩', () => {
    const { container } = render(<WaitingStatus startedAt={null} stage="idle" />)
    expect(container.textContent).toBe('')
  })
})

describe('stageFromElapsed 分段兜底', () => {
  it('≤3s starting、<15s thinking、<45s retrieving、之后 summarizing', () => {
    expect(stageFromElapsed(0)).toBe('starting')
    expect(stageFromElapsed(3)).toBe('starting')
    expect(stageFromElapsed(4)).toBe('thinking')
    expect(stageFromElapsed(14)).toBe('thinking')
    expect(stageFromElapsed(15)).toBe('retrieving')
    expect(stageFromElapsed(44)).toBe('retrieving')
    expect(stageFromElapsed(45)).toBe('summarizing')
    expect(stageFromElapsed(120)).toBe('summarizing')
  })
})
