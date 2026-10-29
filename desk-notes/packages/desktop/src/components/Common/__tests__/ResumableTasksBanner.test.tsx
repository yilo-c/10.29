/**
 * A3（前后端对齐规划）：ResumableTasksBanner 组件测试。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import ResumableTasksBanner from '../ResumableTasksBanner'
import { useBackendEventsStore } from '@desk-notes/shared'

const mockGetWsClient = vi.fn()

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

function makeClient(callImpl?: (method: string, params?: Record<string, unknown>) => Promise<unknown>) {
  return {
    call: vi.fn().mockImplementation(callImpl ?? (() => Promise.resolve({ success: true }))),
    on: vi.fn().mockReturnValue(() => {}),
    status: 'connected',
  }
}

function resetStore() {
  useBackendEventsStore.setState({
    resumableTasks: [],
    resumableDismissed: false,
    syncConflicts: [],
    sandboxTimeouts: [],
    jobAlerts: [],
    morningBrief: null,
    recentExecSteps: [],
  })
}

describe('ResumableTasksBanner', () => {
  beforeEach(() => {
    resetStore()
    vi.clearAllMocks()
  })

  it('无可续跑任务或已忽略时不渲染', () => {
    mockGetWsClient.mockReturnValue(makeClient())
    const { container } = render(<ResumableTasksBanner />)
    expect(container.textContent).toBe('')

    useBackendEventsStore.setState({
      resumableTasks: [{ task_id: 't1', state: 'executing' }],
      resumableDismissed: true,
    })
    const { container: c2 } = render(<ResumableTasksBanner />)
    expect(c2.textContent).toBe('')
  })

  it('有可续跑任务时显示横幅与数量', () => {
    useBackendEventsStore.setState({
      resumableTasks: [
        { task_id: 't1', state: 'executing' },
        { task_id: 't2', state: 'paused' },
      ],
    })
    mockGetWsClient.mockReturnValue(makeClient())
    render(<ResumableTasksBanner />)
    expect(screen.getByText(/2 个任务未完成/)).toBeTruthy()
    expect(screen.getByText('查看')).toBeTruthy()
    expect(screen.getByText('忽略')).toBeTruthy()
  })

  it('点击查看展开任务列表', () => {
    useBackendEventsStore.setState({
      resumableTasks: [{ task_id: 'task-abc', state: 'executing' }],
    })
    mockGetWsClient.mockReturnValue(makeClient())
    render(<ResumableTasksBanner />)
    fireEvent.click(screen.getByText('查看'))
    expect(screen.getByText('task-abc')).toBeTruthy()
    expect(screen.getByText(/状态：executing/)).toBeTruthy()
    expect(screen.getByText('续跑')).toBeTruthy()
  })

  it('点击续跑调用 assistant.resume_task 并隐藏该任务', async () => {
    useBackendEventsStore.setState({
      resumableTasks: [{ task_id: 'task-x', state: 'executing' }],
    })
    const call = vi.fn().mockResolvedValue({ success: true, task_id: 'task-x' })
    mockGetWsClient.mockReturnValue(makeClient(() => Promise.resolve({ success: true })))
    mockGetWsClient.mockReturnValue({ call, on: vi.fn().mockReturnValue(() => {}), status: 'connected' })

    render(<ResumableTasksBanner />)
    fireEvent.click(screen.getByText('查看'))
    fireEvent.click(screen.getByText('续跑'))

    await waitFor(() => {
      expect(call).toHaveBeenCalledWith('assistant.resume_task', { task_id: 'task-x' })
    })
    await waitFor(() => {
      expect(screen.getByText(/任务已从断点恢复执行/)).toBeTruthy()
    })
    // 已续跑任务从列表消失（消息存续期间横幅保留）
    await waitFor(() => {
      expect(screen.queryByText('task-x')).toBeNull()
    })
  })

  it('点击忽略后横幅消失（会话内有效）', () => {
    useBackendEventsStore.setState({
      resumableTasks: [{ task_id: 't1', state: 'x' }],
    })
    mockGetWsClient.mockReturnValue(makeClient())
    render(<ResumableTasksBanner />)
    fireEvent.click(screen.getByText('忽略'))
    expect(screen.queryByText(/个任务未完成/)).toBeNull()
    expect(useBackendEventsStore.getState().resumableDismissed).toBe(true)
  })
})
