import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, act } from '@testing-library/react'
import { useEmitInteractionEvents, isReportableSelection, extractSelectedText } from './useInteractionEvents'

describe('useEmitInteractionEvents 纯函数', () => {
  it('isReportableSelection 按长度门槛过滤', () => {
    expect(isReportableSelection('短', 10, 2000)).toBe(false)
    expect(isReportableSelection('x'.repeat(15), 10, 2000)).toBe(true)
    expect(isReportableSelection('x'.repeat(3000), 10, 2000)).toBe(false)
  })

  it('extractSelectedText 清空两端空白且异常兜底', () => {
    expect(extractSelectedText(() => '  中间文本  ')).toBe('中间文本')
    expect(extractSelectedText(() => { throw new Error('boom') })).toBe('')
  })
})

describe('useEmitInteractionEvents hook', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
  })

  it('选区达门槛后防抖上报 selection.detected', () => {
    const emit = vi.fn()
    const state = { text: '这是一段被选中的较长文本用于测试事件上报' }

    function Harness() {
      useEmitInteractionEvents({ emit, getSelectionText: () => state.text, debounceMs: 0 })
      return null
    }
    render(<Harness />)

    act(() => {
      document.dispatchEvent(new MouseEvent('mouseup'))
    })
    act(() => {
      vi.advanceTimersByTime(1)
    })

    expect(emit).toHaveBeenCalledTimes(1)
    expect(emit).toHaveBeenCalledWith('selection.detected', { text: state.text })
  })

  it('过短选区不上报', () => {
    const emit = vi.fn()
    const state = { text: '短' }
    function Harness() {
      useEmitInteractionEvents({ emit, getSelectionText: () => state.text, debounceMs: 0 })
      return null
    }
    render(<Harness />)
    act(() => {
      document.dispatchEvent(new MouseEvent('mouseup'))
    })
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(emit).not.toHaveBeenCalled()
  })

  it('相同文本不重复上报', () => {
    const emit = vi.fn()
    const state = { text: '重复选中的这段文本不应被发两次' }
    function Harness() {
      useEmitInteractionEvents({ emit, getSelectionText: () => state.text, debounceMs: 0 })
      return null
    }
    render(<Harness />)
    for (let i = 0; i < 3; i++) {
      act(() => {
        document.dispatchEvent(new MouseEvent('mouseup'))
      })
      act(() => {
        vi.advanceTimersByTime(1)
      })
    }
    expect(emit).toHaveBeenCalledTimes(1)
  })

  it('窗口获焦上报 foreground.window.changed', () => {
    const emit = vi.fn()
    function Harness() {
      useEmitInteractionEvents({ emit, getWindowTitle: () => '小问聊天', debounceMs: 0 })
      return null
    }
    render(<Harness />)
    act(() => {
      window.dispatchEvent(new Event('focus'))
    })
    expect(emit).toHaveBeenCalledWith('foreground.window.changed', { title: '小问聊天', app: 'desk-notes' })
  })

  it('卸载后不再监听', () => {
    const emit = vi.fn()
    const state = { text: '卸载后不应再上报的选中文本' }
    function Harness() {
      useEmitInteractionEvents({ emit, getSelectionText: () => state.text, debounceMs: 0 })
      return null
    }
    const { unmount } = render(<Harness />)
    unmount()
    act(() => {
      document.dispatchEvent(new MouseEvent('mouseup'))
    })
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(emit).not.toHaveBeenCalled()
  })
})
