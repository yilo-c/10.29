/**
 * A2（前后端对齐规划）：SyncConflictResolver 组件测试。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import SyncConflictResolver from '../SyncConflictResolver'
import { useBackendEventsStore } from '@desk-notes/shared'

const mockGetWsClient = vi.fn()

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

interface TestWsClient {
  call: ReturnType<typeof vi.fn>
  on: ReturnType<typeof vi.fn>
  status: string
}

function makeClient(overrides: Partial<TestWsClient> = {}): TestWsClient {
  return {
    call: vi.fn().mockResolvedValue({ success: true, conflicts: [] }),
    on: vi.fn().mockReturnValue(() => {}),
    status: 'connected',
    ...overrides,
  }
}

function resetStore() {
  useBackendEventsStore.setState({
    syncConflicts: [],
    resumableTasks: [],
    resumableDismissed: false,
    sandboxTimeouts: [],
    jobAlerts: [],
    morningBrief: null,
    recentExecSteps: [],
  })
}

describe('SyncConflictResolver', () => {
  beforeEach(() => {
    resetStore()
    vi.clearAllMocks()
  })

  it('无冲突时不渲染', async () => {
    mockGetWsClient.mockReturnValue(makeClient())
    const { container } = render(<SyncConflictResolver />)
    await waitFor(() => {
      expect(mockGetWsClient).toHaveBeenCalled()
    })
    expect(container.textContent).toBe('')
  })

  it('挂载时补拉后端冲突列表并渲染', async () => {
    mockGetWsClient.mockReturnValue(makeClient({
      call: vi.fn().mockImplementation((method: string) => {
        if (method === 'sync.conflicts.list') {
          return Promise.resolve({
            success: true,
            conflicts: [{ resource: 'note', id: 'n9', title: '离线冲突笔记', has_local_version: true }],
          })
        }
        return Promise.resolve({ success: true })
      }),
    }))

    render(<SyncConflictResolver />)
    expect(await screen.findByText(/同步冲突待裁决/)).toBeTruthy()
    expect(screen.getByText('离线冲突笔记')).toBeTruthy()
    expect(screen.getByText('恢复本地')).toBeTruthy()
    expect(screen.getByText('保留远端')).toBeTruthy()
  })

  it('store 实时冲突与后端列表按 resource+id 合并去重', async () => {
    useBackendEventsStore.setState({
      syncConflicts: [{
        resource: 'note', id: 'n1', title: '实时冲突', local_time: 1, remote_time: 2, receivedAt: Date.now(),
      }],
    })
    mockGetWsClient.mockReturnValue(makeClient({
      call: vi.fn().mockImplementation((method: string) => {
        if (method === 'sync.conflicts.list') {
          return Promise.resolve({
            success: true,
            conflicts: [{ resource: 'note', id: 'n1', title: '后端版标题' }],
          })
        }
        return Promise.resolve({ success: true })
      }),
    }))

    render(<SyncConflictResolver />)
    await screen.findByText(/同步冲突待裁决（1）/)
    // store 版本优先（含更完整的元数据）
    expect(screen.getByText('实时冲突')).toBeTruthy()
    expect(screen.queryByText('后端版标题')).toBeNull()
  })

  it('点击恢复本地：调用 resolve_conflict(choice=local) 并移除条目', async () => {
    useBackendEventsStore.setState({
      syncConflicts: [{
        resource: 'note', id: 'n2', title: '待裁决', local_time: 1, remote_time: 2, receivedAt: Date.now(),
      }],
    })
    const call = vi.fn().mockImplementation((method: string) => {
      if (method === 'sync.conflicts.list') return Promise.resolve({ success: true, conflicts: [] })
      if (method === 'sync.resolve_conflict') return Promise.resolve({ success: true })
      return Promise.resolve({ success: true })
    })
    mockGetWsClient.mockReturnValue(makeClient({ call }))

    render(<SyncConflictResolver />)
    fireEvent.click(await screen.findByText('恢复本地'))

    await waitFor(() => {
      expect(call).toHaveBeenCalledWith('sync.resolve_conflict', { resource: 'note', id: 'n2', choice: 'local' })
    })
    await waitFor(() => {
      expect(useBackendEventsStore.getState().syncConflicts).toHaveLength(0)
    })
    expect(screen.getByText(/已恢复本地版本/)).toBeTruthy()
  })

  it('裁决失败时显示错误且条目保留', async () => {
    useBackendEventsStore.setState({
      syncConflicts: [{
        resource: 'todo', id: 'd1', title: '任务冲突', local_time: 1, remote_time: 2, receivedAt: Date.now(),
      }],
    })
    const call = vi.fn().mockImplementation((method: string) => {
      if (method === 'sync.conflicts.list') return Promise.resolve({ success: true, conflicts: [] })
      if (method === 'sync.resolve_conflict') return Promise.resolve({ success: false, error: 'note is not in conflict state' })
      return Promise.resolve({ success: true })
    })
    mockGetWsClient.mockReturnValue(makeClient({ call }))

    render(<SyncConflictResolver />)
    fireEvent.click(await screen.findByText('保留远端'))

    await waitFor(() => {
      expect(screen.getByText('note is not in conflict state')).toBeTruthy()
    })
    expect(useBackendEventsStore.getState().syncConflicts).toHaveLength(1)
  })
})
