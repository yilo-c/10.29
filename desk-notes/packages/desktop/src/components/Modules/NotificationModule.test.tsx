import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import {
  NotificationModule,
  eventLabel,
  formatRelativeTime,
  isHighPriority,
  type NotificationItem,
  type NotificationStats,
} from './NotificationModule'

const mockGetWsClient = vi.fn()

vi.mock('../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

interface TestWsClient {
  call: ReturnType<typeof vi.fn>
  on: ReturnType<typeof vi.fn>
}

function makeClient(overrides: Partial<TestWsClient> = {}): TestWsClient {
  return {
    call: vi.fn(),
    on: vi.fn().mockReturnValue(() => {}),
    ...overrides,
  }
}

function seedCall(client: { call: ReturnType<typeof vi.fn> }, items: NotificationItem[] = [], stats: Partial<NotificationStats> = {}) {
  client.call.mockImplementation((method: string) => {
    if (method === 'notification.list') return Promise.resolve({ items })
    if (method === 'notification.stats') {
      return Promise.resolve({
        total: items.length,
        unread: items.filter((n) => !n.read).length,
        by_event: {},
        by_priority: {},
        channels: [{ name: 'websocket', healthy: true }],
        subscribed_events: [],
        enabled: true,
        ...stats,
      })
    }
    return Promise.resolve({})
  })
}

const SAMPLE: NotificationItem = {
  id: 'n1',
  event_type: 'vocabulary.due_reminder',
  title: '单词到期复习',
  body: '10 个单词到期了',
  priority: 'normal',
  created_at: '2026-08-04T10:00:00+00:00',
  read: false,
}

describe('通知中心纯函数', () => {
  it('eventLabel 映射已知事件并回退到原始类型', () => {
    expect(eventLabel('vocabulary.due_reminder')).toBe('单词到期复习')
    expect(eventLabel('sync.failed')).toBe('同步失败')
    expect(eventLabel('unknown.event')).toBe('unknown.event')
  })

  it('isHighPriority 识别 high/urgent', () => {
    expect(isHighPriority('urgent')).toBe(true)
    expect(isHighPriority('high')).toBe(true)
    expect(isHighPriority('normal')).toBe(false)
    expect(isHighPriority('low')).toBe(false)
  })

  it('formatRelativeTime 输出相对时间', () => {
    const now = Date.parse('2026-08-04T10:10:00+00:00')
    expect(formatRelativeTime('2026-08-04T10:09:30+00:00', now)).toBe('刚刚')
    expect(formatRelativeTime('2026-08-04T10:00:00+00:00', now)).toBe('10 分钟前')
    expect(formatRelativeTime('2026-08-04T08:00:00+00:00', now)).toBe('2 小时前')
    expect(formatRelativeTime('2026-08-01T08:00:00+00:00', now)).toBe('3 天前')
    expect(formatRelativeTime('invalid')).toBe('')
  })
})

describe('NotificationModule', () => {
  let client: TestWsClient

  beforeEach(() => {
    vi.clearAllMocks()
    client = makeClient()
    mockGetWsClient.mockReturnValue(client)
  })

  it('成功加载并渲染通知列表', async () => {
    seedCall(client, [SAMPLE])
    render(<NotificationModule />)

    expect(await screen.findByText('10 个单词到期了')).toBeTruthy()
    // 事件标签 + 标题均为「单词到期复习」，出现两处
    expect(screen.getAllByText('单词到期复习').length).toBeGreaterThanOrEqual(2)
    // 统计摘要
    expect(screen.getByText('通知总数')).toBeTruthy()
  })

  it('空态显示"暂无通知"', async () => {
    seedCall(client, [])
    render(<NotificationModule />)

    expect(await screen.findByText('暂无通知')).toBeTruthy()
  })

  it('后端未连接时显示错误', async () => {
    mockGetWsClient.mockReturnValue(null)
    render(<NotificationModule />)

    expect(await screen.findByRole('alert')).toBeTruthy()
  })

  it('RPC 调用失败时显示错误', async () => {
    client.call.mockRejectedValue(new Error('Not connected'))
    render(<NotificationModule />)

    expect(await screen.findByRole('alert')).toBeTruthy()
  })

  it('list 返回 error 时显示错误', async () => {
    client.call.mockImplementation((method: string) => {
      if (method === 'notification.list') return Promise.resolve({ items: [], error: 'dispatcher unavailable' })
      return Promise.resolve({})
    })
    render(<NotificationModule />)

    expect(await screen.findByRole('alert')).toBeTruthy()
  })

  it('点击"已读"调用 notification.mark_read 并刷新', async () => {
    seedCall(client, [SAMPLE])
    render(<NotificationModule />)

    await screen.findByText('10 个单词到期了')
    fireEvent.click(screen.getByRole('button', { name: /标记.*已读/ }))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('notification.mark_read', { notification_id: 'n1', read: true })
    })
    // 操作后重新加载列表
    expect(client.call).toHaveBeenCalledWith('notification.list', expect.anything())
  })

  it('点击"删除"调用 notification.delete', async () => {
    seedCall(client, [SAMPLE])
    render(<NotificationModule />)

    await screen.findByText('10 个单词到期了')
    fireEvent.click(screen.getByRole('button', { name: /^删除/ }))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('notification.delete', { notification_id: 'n1' })
    })
  })

  it('点击"全部已读"调用 notification.mark_all_read', async () => {
    seedCall(client, [SAMPLE])
    render(<NotificationModule />)

    await screen.findByText('10 个单词到期了')
    fireEvent.click(screen.getByRole('button', { name: '全部已读' }))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('notification.mark_all_read', {})
    })
  })

  it('订阅 notification.delivered 事件以实时刷新', async () => {
    seedCall(client, [SAMPLE])
    render(<NotificationModule />)

    await waitFor(() => {
      expect(client.on).toHaveBeenCalledWith('notification.delivered', expect.any(Function))
    })
  })

  it('未读筛选只显示未读通知', async () => {
    const readItem: NotificationItem = { ...SAMPLE, id: 'n2', read: true, title: '已读通知', body: '这是已读的通知内容' }
    seedCall(client, [SAMPLE, readItem])
    render(<NotificationModule />)

    await screen.findByText('这是已读的通知内容')
    fireEvent.click(screen.getByRole('button', { name: '未读' }))

    expect(screen.getByText('10 个单词到期了')).toBeTruthy()
    expect(screen.queryByText('这是已读的通知内容')).toBeNull()
  })
})
