/**
 * A5（前后端对齐规划）：MorningBriefCard 组件测试。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import MorningBriefCard from '../MorningBriefCard'
import { useBackendEventsStore } from '@desk-notes/shared'

const mockGetWsClient = vi.fn()

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

function resetStore() {
  useBackendEventsStore.setState({
    morningBrief: null,
    resumableTasks: [],
    resumableDismissed: false,
    syncConflicts: [],
    sandboxTimeouts: [],
    jobAlerts: [],
    recentExecSteps: [],
  })
}

describe('MorningBriefCard', () => {
  beforeEach(() => {
    resetStore()
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({
      call: vi.fn().mockResolvedValue({ success: true }),
      on: vi.fn().mockReturnValue(() => {}),
      status: 'connected',
    })
  })

  it('无晨报时不渲染', () => {
    const { container } = render(<MorningBriefCard />)
    expect(container.textContent).toBe('')
  })

  it('渲染晨报标题/日期/内容', () => {
    useBackendEventsStore.setState({
      morningBrief: {
        title: '今日晨报',
        content: '· 任务：昨日完成 3 项\n· 单词：到期 5 个',
        date: '2026-08-09',
        receivedAt: Date.now(),
      },
    })
    render(<MorningBriefCard />)
    expect(screen.getByText('今日晨报')).toBeTruthy()
    expect(screen.getByText('2026-08-09')).toBeTruthy()
    expect(screen.getByText(/昨日完成 3 项/)).toBeTruthy()
  })

  it('点击关闭清除晨报', () => {
    useBackendEventsStore.setState({
      morningBrief: { title: 't', content: 'c', date: '', receivedAt: Date.now() },
    })
    render(<MorningBriefCard />)
    fireEvent.click(screen.getByTitle('关闭晨报'))
    expect(useBackendEventsStore.getState().morningBrief).toBeNull()
  })
})
