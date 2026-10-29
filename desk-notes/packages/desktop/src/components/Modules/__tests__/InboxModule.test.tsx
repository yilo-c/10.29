import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import InboxModule from '../InboxModule'

// ── Hoisted mocks ──────────────────────────────────────────────────────────
const { mockGetWsClient, mockUseBackendEvents, mockErrorStore } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
  mockUseBackendEvents: vi.fn(),
  mockErrorStore: { success: vi.fn(), warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../../../utils/ai', () => ({ getWsClient: mockGetWsClient }))

vi.mock('../../../FloatingBubble/useBackendEvents', () => ({
  useBackendEvents: mockUseBackendEvents,
}))

vi.mock('../../../utils/swallowError', () => ({ swallowError: vi.fn() }))

vi.mock('../../../store/errorStore', () => ({ errorStore: mockErrorStore }))

vi.mock('../../../utils/trackUsage', () => ({ trackUsage: vi.fn() }))

vi.mock('../../Common/Icon', () => ({
  default: ({ icon }: { icon: string }) => <i data-testid="icon" data-icon={icon} />,
}))

vi.mock('../ApprovalQueuePanel', () => ({ default: () => <div data-testid="approval-queue" /> }))

vi.mock('../../../i18n', () => ({
  useTranslation: () => ({ t: (key: string, params?: Record<string, unknown>) => {
    if (params && 'count' in params) return `${key}:${params.count}`
    return key
  } }),
}))

/** 生成 N 条未分类收件箱条目（C-14①-5 分页用） */
function makeEntries(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    content: `条目 ${i + 1}`,
    status: 'new',
    metadata: {},
  }))
}

describe('InboxModule', () => {
  const mockCall = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockCall.mockImplementation(async () => [])
    mockGetWsClient.mockReturnValue({ call: mockCall, status: 'connected' })
    mockUseBackendEvents.mockReturnValue({ lastEvent: vi.fn(() => null) })
  })

  // ── C-14①-5 分页 ────────────────────────────────────────────────────────
  it('renders only first page (50) out of 120 entries with load-more button', async () => {
    mockCall.mockImplementation(async (method: string) => (method === 'inbox.list' ? makeEntries(120) : []))
    render(<InboxModule />)

    await waitFor(() => expect(screen.getByText('条目 1')).toBeTruthy())
    expect(screen.getByText('条目 50')).toBeTruthy()
    // 第 51 条不应渲染
    expect(screen.queryByText('条目 51')).toBeNull()
    // 加载更多按钮带剩余数量（120 - 50 = 70）
    expect(screen.getByText('inbox.loadMore:70')).toBeTruthy()
  })

  it('load-more click appends the next page', async () => {
    mockCall.mockImplementation(async (method: string) => (method === 'inbox.list' ? makeEntries(120) : []))
    render(<InboxModule />)
    await waitFor(() => expect(screen.getByText('条目 50')).toBeTruthy())

    fireEvent.click(screen.getByText('inbox.loadMore:70'))
    expect(screen.getByText('条目 100')).toBeTruthy()
    expect(screen.queryByText('条目 101')).toBeNull()
    expect(screen.getByText('inbox.loadMore:20')).toBeTruthy()
  })

  it('hides load-more when all entries fit one page', async () => {
    mockCall.mockImplementation(async (method: string) => (method === 'inbox.list' ? makeEntries(30) : []))
    render(<InboxModule />)
    await waitFor(() => expect(screen.getByText('条目 30')).toBeTruthy())
    expect(screen.queryByText(/inbox\.loadMore/)).toBeNull()
  })

  it('resets pagination when switching kind filter', async () => {
    // 60 条全部未分类 → "全部"视图第 1 页 50 条
    mockCall.mockImplementation(async (method: string) => (method === 'inbox.list' ? makeEntries(60) : []))
    render(<InboxModule />)
    await waitFor(() => expect(screen.getByText('条目 50')).toBeTruthy())

    // 点"加载更多"→ 60 条全部可见，按钮消失
    fireEvent.click(screen.getByText('inbox.loadMore:10'))
    expect(screen.getByText('条目 60')).toBeTruthy()

    // 切换类型筛选（全部未分类 → uncertain 侧栏按钮）→ 分页重置回第一页
    fireEvent.click(screen.getByText('inbox.kindUncertain'))
    expect(screen.queryByText('条目 51')).toBeNull()
  })

  // ── C-14①-1 手动分类回写 ────────────────────────────────────────────────
  it('writes classify result back into metadata.classification and updates UI', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') {
        return [{ id: 5, content: '帮我分类', status: 'new', metadata: { device_id: 'mobile-1' } }]
      }
      if (method === 'inbox.classify') {
        return { success: true, kind: 'task', confidence: 0.9, reason: '是待办', suggested_action: 'convert_to_task' }
      }
      return {}
    })
    render(<InboxModule />)
    await waitFor(() => expect(screen.getByText('帮我分类')).toBeTruthy())

    // 点魔杖按钮（title=inbox.classifyTitle）
    fireEvent.click(screen.getByTitle('inbox.classifyTitle'))

    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('inbox.update', {
        entry_id: 5,
        metadata: {
          device_id: 'mobile-1', // 旧 metadata 不被抹（C-14①-2）
          classification: { kind: 'task', confidence: 0.9, reason: '是待办', suggested_action: 'convert_to_task' },
        },
      })
    })
    // 乐观更新：徽章立即显示任务分类，不再是"分类中"（confidence 0.9 ≥ 0.8 不显示百分比）
    await waitFor(() => expect(screen.getAllByText('inbox.kindTask').length).toBeGreaterThan(0))
    expect(screen.queryByText('inbox.classifying')).toBeNull()
  })

  it('does not write back when classify returns error payload', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return [{ id: 6, content: '分类失败', status: 'new', metadata: {} }]
      if (method === 'inbox.classify') return { success: false, error: 'ai_client unavailable' }
      return {}
    })
    render(<InboxModule />)
    await waitFor(() => expect(screen.getByText('分类失败')).toBeTruthy())

    fireEvent.click(screen.getByTitle('inbox.classifyTitle'))
    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('inbox.classify', { entry_id: 6 })
    })
    // 无 inbox.update 回写，条目保持"分类中"
    expect(mockCall.mock.calls.some(c => c[0] === 'inbox.update')).toBe(false)
    expect(screen.getByText('inbox.classifying')).toBeTruthy()
  })

  // ── 基础渲染 ────────────────────────────────────────────────────────────
  it('renders offline state when ws client is not connected', () => {
    mockGetWsClient.mockReturnValue({ call: mockCall, status: 'disconnected' })
    render(<InboxModule />)
    expect(screen.getByText('ux.offlineTitle')).toBeTruthy()
  })

  it('archive removes entry via inbox.update archived', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return [{ id: 8, content: '归档我', status: 'new', metadata: { classification: { kind: 'note', confidence: 1, reason: '', suggested_action: '' } } }]
      return { success: true }
    })
    render(<InboxModule />)
    await waitFor(() => expect(screen.getByText('归档我')).toBeTruthy())

    fireEvent.click(screen.getByTitle('inbox.archive'))
    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('inbox.update', { entry_id: 8, status: 'archived' })
    })
    expect(screen.queryByText('归档我')).toBeNull()
  })
})
