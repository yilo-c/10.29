import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { UsageDashboardModule } from '../UsageDashboardModule'

const callMock = vi.fn()
vi.mock('../../../utils/ai', () => ({
  getWsClient: () => ({ call: callMock }),
}))

beforeEach(() => { callMock.mockReset() })

const DASH = {
  enabled: true,
  days: 7,
  today: { calls: 12, tokens: 3400, cost: 0.05, by_provider: [] },
  stats: { total_calls: 120, total_tokens: 40000, total_cost: 0.4, by_provider: [], by_model: [] },
  budget: { daily_token_limit: 100000, daily_cost_limit: null, token_usage: 0, cost_usage: 0, exceeded: false },
  provider_health: [{ provider: 'deepseek', healthy: true }],
  tier_breakdown: { today_cost: 0.05, today_calls: 12, tier_breakdown: { hybrid: 0.05 }, vs_pure_cloud_saving: 0.2 },
}

describe('UsageDashboardModule', () => {
  it('渲染用量看板并读取数据', async () => {
    callMock
      .mockResolvedValueOnce(DASH)
      .mockResolvedValueOnce({ daily_token_limit: 100000, daily_cost_limit: null, token_usage: 0, cost_usage: 0, exceeded: false })
    render(<UsageDashboardModule />)
    await waitFor(() => expect(screen.getByText('AI 用量看板')).toBeTruthy())
    expect(screen.getByText('今日调用')).toBeTruthy()
    expect(screen.getByText('12')).toBeTruthy()
  })

  it('导出 CSV 调用 usage.export', async () => {
    callMock
      .mockResolvedValueOnce(DASH)
      .mockResolvedValueOnce({ daily_token_limit: null, daily_cost_limit: null, token_usage: 0, cost_usage: 0, exceeded: false })
      .mockResolvedValueOnce('provider,tokens,cost\n')
    render(<UsageDashboardModule />)
    await waitFor(() => expect(screen.getByRole('button', { name: '导出 CSV' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '导出 CSV' }))
    await waitFor(() => {
      expect(callMock).toHaveBeenCalledWith('usage.export', expect.anything())
    })
  })

  it('用量追踪器未初始化时显示警告', async () => {
    callMock
      .mockResolvedValueOnce({ enabled: false, error: 'not initialized' })
      .mockResolvedValueOnce(null)
    render(<UsageDashboardModule />)
    expect(await screen.findByText(/用量追踪器未初始化/)).toBeTruthy()
  })

  it('后端未连接时显示错误', async () => {
    callMock.mockRejectedValue(new Error('Not connected'))
    render(<UsageDashboardModule />)
    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})