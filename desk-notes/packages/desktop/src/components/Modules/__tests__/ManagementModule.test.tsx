import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { ManagementModule } from '../ManagementModule'

const callMock = vi.fn()
vi.mock('../../../utils/ai', () => ({
  getWsClient: () => ({ call: callMock }),
}))

const seedEvolution = () => {
  callMock
    .mockResolvedValueOnce({ available: true, last_scan_at: '2026-08-17T00:00:00+00:00', upgrades: [{ name: 'u1', title: '升级项A', status: 'pending' }] })
    .mockResolvedValueOnce({ briefing: { title: '今日早报', content: '内容' } })
    .mockResolvedValueOnce([{ name: 'r1', status: 'applied' }])
}

beforeEach(() => { callMock.mockReset() })

describe('ManagementModule', () => {
  it('渲染系统管理，默认进化 Tab', async () => {
    seedEvolution()
    render(<ManagementModule />)
    await waitFor(() => expect(screen.getByText('系统管理')).toBeTruthy())
    expect(screen.getByText('受控进化')).toBeTruthy()
    await waitFor(() => expect(screen.getByText('升级项A')).toBeTruthy())
  })

  it('切到审计 Tab 调用 audit.list', async () => {
    seedEvolution()
    callMock
      .mockResolvedValueOnce({ entries: [{ id: 'a1', action: 'create', resource_type: 'note', status: 'ok' }] })
      .mockResolvedValueOnce({ total: 1 })
    render(<ManagementModule />)
    await waitFor(() => expect(screen.getByText('系统管理')).toBeTruthy())
    fireEvent.click(screen.getByText('审计回滚'))
    await waitFor(() => {
      expect(callMock).toHaveBeenCalledWith('audit.list', expect.anything())
    })
    await waitFor(() => expect(screen.getByText('create')).toBeTruthy())
  })

  it('切到时间线 Tab 调用 timeline.list', async () => {
    seedEvolution()
    callMock.mockResolvedValueOnce({
      entries: [{ id: 'tl1', source_type: 'note', title: '读书记录', content: '在读 xx' }],
      counts_by_type: { note: 1 },
    })
    render(<ManagementModule />)
    await waitFor(() => expect(screen.getByText('系统管理')).toBeTruthy())
    fireEvent.click(screen.getByText('时间线'))
    await waitFor(() => {
      expect(callMock).toHaveBeenCalledWith('timeline.list', expect.anything())
    })
    await waitFor(() => expect(screen.getByText('读书记录')).toBeTruthy())
  })

  it('后端未连接时显示错误', async () => {
    callMock.mockRejectedValue(new Error('Not connected'))
    render(<ManagementModule />)
    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})