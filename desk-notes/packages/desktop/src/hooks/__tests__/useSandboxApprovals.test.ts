/**
 * M2.2 ②：审批收件箱数据流接线——sandbox.* 事件通道驱动待处理队列与历史。
 *
 * 事件名实地核对：后端是 `sandbox.pending_added / approved / rejected / evicted / timeout`
 * （services/sandbox_manager.py:280/317/345/251/168）。**没有 sandbox.requested 这个事件名**。
 * 列表 RPC `sandbox.list_pending` 已存在（backend/handlers/_sandbox.py:32）→ 无缺口；
 * 历史 RPC 缺失 → 历史侧由事件在本机累积（见 useSandboxApprovals 头部注释）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { clearBackendEventHandlers, dispatchBackendEvent } from '@desk-notes/shared'
import { useSandboxApprovals } from '../useSandboxApprovals'

const { mockGetWsClient, wsMock } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
  wsMock: {
    status: 'connected',
    call: vi.fn(),
    on: vi.fn(() => () => {}),
    onStatusChange: vi.fn(() => () => {}),
  },
}))

vi.mock('../../utils/ai', () => ({ getWsClient: () => mockGetWsClient() }))

const ev = (event_type: string, payload: Record<string, unknown>) => ({ type: 'event' as const, event_type, payload })

beforeEach(() => {
  vi.clearAllMocks()
  clearBackendEventHandlers()
  mockGetWsClient.mockReturnValue(wsMock)
  wsMock.status = 'connected'
  localStorage.clear()
})

afterEach(() => {
  clearBackendEventHandlers()
})

describe('useSandboxApprovals', () => {
  it('pending_added 事件 → 重拉 sandbox.list_pending 并进入待处理队列', async () => {
    wsMock.call.mockResolvedValue([
      { pending_id: 'a1', action_id: 'notes.delete', status: 'pending', preview: { args: { note_id: 'n1' }, args_sha256: 'abc' } },
    ])
    const { result } = renderHook(() => useSandboxApprovals())
    await waitFor(() => expect(wsMock.call).toHaveBeenCalledWith('sandbox.list_pending', {}))
    act(() => {
      dispatchBackendEvent(ev('sandbox.pending_added', { pending_id: 'a1', action_id: 'notes.delete', preview: {} }))
    })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    expect(result.current.pending[0].action_id).toBe('notes.delete')
  })

  it('approved 事件 → 落历史（含 decision）并驱动队列刷新', async () => {
    wsMock.call.mockResolvedValue([])
    const { result } = renderHook(() => useSandboxApprovals())
    await waitFor(() => expect(wsMock.call).toHaveBeenCalledWith('sandbox.list_pending', {}))
    act(() => {
      dispatchBackendEvent(ev('sandbox.approved', { pending_id: 'a1', action_id: 'tasks.delete', success: true }))
    })
    await waitFor(() => expect(result.current.history).toHaveLength(1))
    expect(result.current.history[0]).toMatchObject({ pending_id: 'a1', action_id: 'tasks.delete', decision: 'approved' })
    expect(JSON.parse(String(localStorage.getItem('siyi-approval-history')))[0].pending_id).toBe('a1')
  })

  it('批准按钮路径直连 sandbox.approve(pending_id)，后端回 error 时不撤卡', async () => {
    const queue: unknown[] = [{ pending_id: 'z9', action_id: 'notes.delete', preview: {} }]
    wsMock.call.mockImplementation(async (method: string) => (method === 'sandbox.list_pending'
      ? queue
      : { error: '审批不得由发起该操作的同一连接自我批准' }))
    const { result } = renderHook(() => useSandboxApprovals())
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    const res = await result.current.approve('z9')
    expect(res.ok).toBe(false)
    expect(wsMock.call).toHaveBeenCalledWith('sandbox.approve', { pending_id: 'z9' })
    await waitFor(() => expect(result.current.pending).toHaveLength(1), { timeout: 800 })
  })

  it('拒绝走 sandbox.reject(pending_id) 并从队列移除', async () => {
    let queue: unknown[] = [{ pending_id: 'z8', action_id: 'notes.delete', preview: {} }]
    wsMock.call.mockImplementation(async (method: string) => {
      if (method === 'sandbox.list_pending') return queue
      if (method === 'sandbox.reject') { queue = []; return { success: true, pending_id: 'z8', action: 'rejected' } }
      return {}
    })
    const { result } = renderHook(() => useSandboxApprovals())
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    const res = await result.current.reject('z8')
    expect(res.ok).toBe(true)
    expect(wsMock.call).toHaveBeenCalledWith('sandbox.reject', { pending_id: 'z8' })
    // 300ms 合并刷新后后端快照也不含该条 = 真正闭环，而非只改前端本地态
    await waitFor(() => expect(result.current.pending).toHaveLength(0), { timeout: 2000 })
  })
})
