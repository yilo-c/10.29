/**
 * A4（前后端对齐规划）：审批批量 + 超时可视化测试。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, renderHook, act } from '@testing-library/react'
import SandboxTimeoutHints from '../SandboxTimeoutHints'
import { useBackendEventsStore } from '@desk-notes/shared'

const mockGetWsClient = vi.fn()

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

function resetStore() {
  useBackendEventsStore.setState({
    sandboxTimeouts: [],
    resumableTasks: [],
    resumableDismissed: false,
    syncConflicts: [],
    jobAlerts: [],
    morningBrief: null,
    recentExecSteps: [],
  })
}

describe('SandboxTimeoutHints', () => {
  beforeEach(() => {
    resetStore()
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({
      call: vi.fn().mockResolvedValue({ success: true }),
      on: vi.fn().mockReturnValue(() => {}),
      status: 'connected',
    })
  })

  it('无超时事件时不渲染', () => {
    const { container } = render(<SandboxTimeoutHints />)
    expect(container.textContent).toBe('')
  })

  it('渲染超时处置与临期提醒', () => {
    useBackendEventsStore.setState({
      sandboxTimeouts: [
        { pending_id: 'p1', action_id: 'file.write', kind: 'timeout', policy: 'reject', receivedAt: Date.now() },
        { pending_id: 'p2', action_id: 'file.copy', kind: 'warning', remaining_seconds: 45, receivedAt: Date.now() },
      ],
    })
    render(<SandboxTimeoutHints />)
    expect(screen.getByText(/file.write 已超时（已自动拒绝）/)).toBeTruthy()
    expect(screen.getByText(/file.copy 将在 45s 后超时/)).toBeTruthy()
  })

  it('dry_run 策略显示降级说明', () => {
    useBackendEventsStore.setState({
      sandboxTimeouts: [
        { pending_id: 'p3', action_id: 'file.write', kind: 'timeout', policy: 'dry_run', receivedAt: Date.now() },
      ],
    })
    render(<SandboxTimeoutHints />)
    expect(screen.getByText(/降级为只读预演/)).toBeTruthy()
  })

  it('点击 ✕ 关闭单条提示', () => {
    useBackendEventsStore.setState({
      sandboxTimeouts: [
        { pending_id: 'p1', action_id: 'file.write', kind: 'timeout', policy: 'reject', receivedAt: Date.now() },
      ],
    })
    render(<SandboxTimeoutHints />)
    fireEvent.click(screen.getByTitle('关闭提示'))
    expect(useBackendEventsStore.getState().sandboxTimeouts).toHaveLength(0)
  })
})

describe('useSandbox.batchApprove', () => {
  beforeEach(() => {
    resetStore()
    vi.clearAllMocks()
  })

  it('批量批准调用 sandbox.batch_approve 并清理本地列表', async () => {
    const call = vi.fn().mockResolvedValue({ success: true, approved: 2, total: 2 })
    mockGetWsClient.mockReturnValue({ call, on: vi.fn().mockReturnValue(() => {}), status: 'connected' })

    const { useSandbox } = await import('../../../FloatingBubble/useSandbox')
    const { result } = renderHook(() => useSandbox())

    await act(async () => {
      const approved = await result.current.batchApprove(['a1', 'a2'])
      expect(approved).toBe(2)
    })

    expect(call).toHaveBeenCalledWith('sandbox.batch_approve', { pending_ids: ['a1', 'a2'] })
  })

  it('空列表直接返回 0 不发请求', async () => {
    const call = vi.fn()
    mockGetWsClient.mockReturnValue({ call, on: vi.fn().mockReturnValue(() => {}), status: 'connected' })

    const { useSandbox } = await import('../../../FloatingBubble/useSandbox')
    const { result } = renderHook(() => useSandbox())

    let approved = -1
    await act(async () => {
      approved = await result.current.batchApprove([])
    })
    expect(approved).toBe(0)
    expect(call).not.toHaveBeenCalledWith('sandbox.batch_approve', expect.anything())
  })
})
