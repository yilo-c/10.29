import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryModule } from './MemoryModule'

const mockGetWsClient = vi.fn()

vi.mock('../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

interface TestWsClient {
  call: ReturnType<typeof vi.fn>
}

function makeClient(): TestWsClient {
  return { call: vi.fn() }
}

const ACTIVE_ENTRY = {
  entry_id: 'memory:mem_001',
  source_type: 'memory',
  source_id: 'mem_001',
  title: '用户喜欢爬山',
  snippet: '从对话中提取：每周六去爬山。',
  updated_at: '2026-09-16T01:00:00Z',
  metadata: {},
}

const REVOKED_ENTRY = {
  entry_id: 'note:42',
  source_type: 'note',
  source_id: 42,
  title: '旧笔记',
  snippet: '已被撤销的条目',
  updated_at: '2026-09-15T01:00:00Z',
  metadata: { revoked: true },
}

/** memory.list 默认实现：active 请求返回生效条目，include_revoked 请求返回混排（含未撤销项） */
function seedList(client: TestWsClient, opts?: { total?: number; total_revoked?: number }) {
  client.call.mockImplementation((method: string, params: Record<string, unknown>) => {
    if (method === 'memory.list') {
      if (params?.include_revoked) {
        return Promise.resolve({
          success: true,
          items: [ACTIVE_ENTRY, REVOKED_ENTRY], // 后端混排语义：含未撤销项，前端过滤
          total: 2,
          total_revoked: opts?.total_revoked ?? 1,
        })
      }
      return Promise.resolve({
        success: true,
        items: [ACTIVE_ENTRY],
        total: opts?.total ?? 1,
        total_revoked: opts?.total_revoked ?? 1,
      })
    }
    if (method === 'memory.delete') return Promise.resolve({ success: true, entry_id: ACTIVE_ENTRY.entry_id, mode: 'revoked' })
    if (method === 'memory.restore') return Promise.resolve({ success: true, entry_id: REVOKED_ENTRY.entry_id, mode: 'restored' })
    return Promise.resolve({})
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('MemoryModule（U3 批2）', () => {
  it('挂载调 memory.list 渲染记忆行', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    seedList(client)
    render(<MemoryModule />)

    expect(await screen.findByText('用户喜欢爬山')).toBeTruthy()
    expect(screen.getByText('从对话中提取：每周六去爬山。')).toBeTruthy()
    expect(client.call).toHaveBeenCalledWith('memory.list', { limit: 50, offset: 0, include_revoked: false })
    expect(screen.getByTestId('memory-workspace')).toBeTruthy()
  })

  it('已撤销 tab：include_revoked=true 混排结果只渲染 revoked 行，tab 计数显示 total_revoked', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    seedList(client, { total_revoked: 3 })
    render(<MemoryModule />)
    await screen.findByText('用户喜欢爬山')

    fireEvent.click(screen.getByTestId('memory-tab-revoked'))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('memory.list', { limit: 200, offset: 0, include_revoked: true })
    })
    expect(await screen.findByText('旧笔记')).toBeTruthy()
    // 混排里的未撤销条目不得出现在已撤销 tab
    expect(screen.queryByText('用户喜欢爬山')).toBeNull()
    // tab 标签实时显示 total_revoked
    expect(screen.getByTestId('memory-tab-revoked').textContent).toContain('已撤销 (3)')
  })

  it('已撤销 tab 计数为 0 时显示空态「没有已撤销的记忆」', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    client.call.mockImplementation((method: string) => {
      if (method === 'memory.list') {
        return Promise.resolve({ success: true, items: [], total: 0, total_revoked: 0 })
      }
      return Promise.resolve({})
    })
    render(<MemoryModule />)
    await screen.findByTestId('memory-tab-revoked')

    fireEvent.click(screen.getByTestId('memory-tab-revoked'))
    expect(await screen.findByText('没有已撤销的记忆')).toBeTruthy()
  })

  it('撤销点击→confirm→memory.delete 收到精确 {entry_id, reason:"memory_panel"}', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    seedList(client)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<MemoryModule />)
    await screen.findByText('用户喜欢爬山')

    fireEvent.click(screen.getByRole('button', { name: '撤销' }))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('memory.delete', { entry_id: 'memory:mem_001', reason: 'memory_panel' })
    })
    expect(confirmSpy).toHaveBeenCalled()
    // 成功后刷新当前 tab（memory.list 至少被调 2 次）
    await waitFor(() => {
      expect(client.call.mock.calls.filter((c) => c[0] === 'memory.list').length).toBeGreaterThanOrEqual(2)
    })
    confirmSpy.mockRestore()
  })

  it('恢复点击→memory.restore 收到 {entry_id, reason}（无二次确认）', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    seedList(client)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<MemoryModule />)
    await screen.findByText('用户喜欢爬山')

    fireEvent.click(screen.getByTestId('memory-tab-revoked'))
    await screen.findByText('旧笔记')
    fireEvent.click(screen.getByRole('button', { name: '恢复' }))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('memory.restore', { entry_id: 'note:42', reason: 'memory_panel_restore' })
    })
    // 软恢复不走 confirm
    expect(confirmSpy).not.toHaveBeenCalled()
    confirmSpy.mockRestore()
  })

  it('success:false 显示 error 文案且不再调 memory.list 刷新', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    client.call.mockImplementation((method: string) => {
      if (method === 'memory.list') return Promise.resolve({ success: false, error: '记忆服务不可用' })
      return Promise.resolve({ success: true })
    })
    render(<MemoryModule />)

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('记忆服务不可用')
    await new Promise((r) => setTimeout(r, 50))
    expect(client.call.mock.calls.filter((c) => c[0] === 'memory.list').length).toBe(1)
  })

  it('entry_id 缺失的行「撤销」按钮禁用（id 错位面板侧防线）', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    client.call.mockImplementation((method: string) => {
      if (method === 'memory.list') {
        return Promise.resolve({
          success: true,
          items: [{ title: '无 id 条目', snippet: '缺 entry_id', source_type: 'memory', metadata: {} }],
          total: 1,
          total_revoked: 0,
        })
      }
      return Promise.resolve({})
    })
    render(<MemoryModule />)
    await screen.findByText('无 id 条目')

    const btn = screen.getByRole('button', { name: '撤销' }) as HTMLButtonElement
    expect(btn.disabled).toBe(true)
  })

  it('后端未连接（getWsClient 为空）显示「后端未连接」提示条', async () => {
    mockGetWsClient.mockReturnValue(null)
    render(<MemoryModule />)
    expect(await screen.findByTestId('memory-offline')).toBeTruthy()
    expect(screen.getByTestId('memory-offline').textContent).toContain('后端未连接')
  })
})
