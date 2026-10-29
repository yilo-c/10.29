/**
 * M2.2 第1步 bug④：后端分型 notice（type=notice 的 JSON 双字段）行内渲染。
 *
 * 载荷契约：`思忆集app/backend/handlers/chat_paths/helpers.py:469-477`
 * `{event:"chat_error", error_kind, friendly, technical, status, transient, retry_after_seconds}`
 * 另有 chat_cancelled（:347-362）/ kernel_degraded（_chat_memory.py:57-74）。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { parseStreamNotice, ChatNoticeRow, NoticeTechnical } from '../StreamNotice'

const timeoutNotice = JSON.stringify({
  event: 'chat_error',
  error_kind: 'timeout',
  friendly: '模型响应超时，请稍后重试或缩短本次提问',
  technical: 'APITimeoutError: Request timed out. [http_status=408]',
  status: 408,
  transient: true,
  retry_after_seconds: null,
})

describe('parseStreamNotice', () => {
  it('chat_error 拆出 friendly/technical 双字段与瞬时标记', () => {
    const n = parseStreamNotice(timeoutNotice)
    expect(n.kind).toBe('chat_error')
    expect(n.friendly).toContain('超时')
    expect(n.technical).toContain('APITimeoutError')
    expect(n.errorKind).toBe('timeout')
    expect(n.transient).toBe(true)
  })

  it('限流带 retry_after_seconds 时给出建议等待秒数', () => {
    const n = parseStreamNotice(JSON.stringify({
      event: 'chat_error', error_kind: 'rate_limit', friendly: '', technical: 'x', transient: true, retry_after_seconds: 30,
    }))
    expect(n.friendly).toContain('限流')
    expect(n.retryAfterSeconds).toBe(30)
  })

  it('chat_cancelled / kernel_degraded 各自成型', () => {
    expect(parseStreamNotice(JSON.stringify({ event: 'chat_cancelled', chunks: 3 })).kind).toBe('chat_cancelled')
    const degraded = parseStreamNotice(JSON.stringify({ event: 'kernel_degraded', from: 'gateway', to: 'legacy', reason: 'no key' }))
    expect(degraded.kind).toBe('kernel_degraded')
    expect(degraded.friendly).toContain('no key')
  })

  it('非 JSON / 空串降级为 other + 原文，绝不抛错', () => {
    expect(parseStreamNotice('纯文本提示').friendly).toBe('纯文本提示')
    expect(parseStreamNotice('').kind).toBe('other')
    expect(parseStreamNotice('[1,2]').kind).toBe('other')
  })
})

describe('ChatNoticeRow / NoticeTechnical', () => {
  it('友好文案常驻，技术细节默认折叠、点击展开', () => {
    render(<ChatNoticeRow notice={parseStreamNotice(timeoutNotice)} />)
    expect(screen.getByTestId('chat-notice-row').textContent).toContain('模型响应超时')
    expect(screen.queryByTestId('chat-notice-technical')).toBeNull()
    fireEvent.click(screen.getByTestId('chat-notice-toggle'))
    expect(screen.getByTestId('chat-notice-technical').textContent).toContain('APITimeoutError')
  })

  it('瞬时错误给出可操作重试，非瞬时不出按钮', () => {
    const onRetry = vi.fn()
    render(<ChatNoticeRow notice={parseStreamNotice(timeoutNotice)} onRetry={onRetry} />)
    fireEvent.click(screen.getByTestId('chat-notice-retry'))
    expect(onRetry).toHaveBeenCalled()
    render(<ChatNoticeRow notice={parseStreamNotice(JSON.stringify({ event: 'chat_error', error_kind: 'auth_error', friendly: '鉴权失败', technical: '', transient: false }))} onRetry={onRetry} />)
    expect(screen.getAllByTestId('chat-notice-retry')).toHaveLength(1)
  })

  it('technical 为空时不出折叠入口', () => {
    const { container } = render(<NoticeTechnical technical="" />)
    expect(container.textContent).toBe('')
  })
})
