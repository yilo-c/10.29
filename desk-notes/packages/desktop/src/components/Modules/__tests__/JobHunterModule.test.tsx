import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { JobHunterModule } from '../JobHunterModule'

const callMock = vi.fn()
vi.mock('../../../utils/ai', () => ({
  getWsClient: () => ({
    call: callMock,
    stream: vi.fn(async function* () { yield '分析结果片段' }),
  }),
}))

beforeEach(() => { callMock.mockReset() })

describe('JobHunterModule', () => {
  it('渲染职位库并读取数据', async () => {
    callMock
      .mockResolvedValueOnce({ success: true, jobs: [{ id: 'j1', title: '后端工程师', company: '字节', application_status: 'new', match_score: 82 }] })
      .mockResolvedValueOnce({ success: true, stats: { total: 1, analyzed: 1, matched: 1, avg_match_score: 82 } })
      .mockResolvedValueOnce({ success: true, platforms: [{ id: 'boss', name: 'BOSS直聘', enabled: true }] })
    render(<JobHunterModule />)
    await waitFor(() => expect(screen.getByText('后端工程师')).toBeTruthy())
    expect(screen.getByText('求职猎手')).toBeTruthy()
  })

  it('画像 Tab 读取并保存', async () => {
    callMock
      .mockResolvedValueOnce({ success: true, jobs: [], stats: { total: 0 }, platforms: [] })
      .mockResolvedValueOnce({ success: true, jobs: [], stats: { total: 0 }, platforms: [] })
      .mockResolvedValueOnce({ success: true, jobs: [], stats: { total: 0 }, platforms: [] })
      .mockResolvedValueOnce({ success: true, profile: { name: '张三', tech_stack: ['Python'] } })
    render(<JobHunterModule />)
    await waitFor(() => expect(screen.getByText('求职猎手')).toBeTruthy())
    fireEvent.click(screen.getByText('我的画像'))
    await waitFor(() => expect(screen.getByDisplayValue('张三')).toBeTruthy())
    expect(screen.getByText('保存画像')).toBeTruthy()
  })

  it('空职位数据时显示引导文案', async () => {
    callMock
      .mockResolvedValueOnce({ success: true, jobs: [] })
      .mockResolvedValueOnce({ success: true, stats: { total: 0 } })
      .mockResolvedValueOnce({ success: true, platforms: [{ id: 'boss', name: 'BOSS直聘', enabled: true }] })
    render(<JobHunterModule />)
    await waitFor(() => expect(screen.getByText(/暂无匹配职位|暂无招聘平台数据/)).toBeTruthy())
  })

  it('后端未连接时显示错误', async () => {
    callMock.mockRejectedValue(new Error('Not connected'))
    render(<JobHunterModule />)
    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})