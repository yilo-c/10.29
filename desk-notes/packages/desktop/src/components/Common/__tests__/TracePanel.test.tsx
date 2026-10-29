/**
 * D2（前后端对齐规划）：toolErrorHints + TracePanel 测试。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { describeToolError, hasKnownToolError } from '../../../utils/toolErrorHints'
import TracePanel from '../TracePanel'

const mockGetWsClient = vi.fn()

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

describe('describeToolError', () => {
  it('映射已知 reason 为友好提示', () => {
    expect(describeToolError('action blocked by broker: capability_not_found: 能力 browser.navigate 未注册')?.title).toBe('调用了未注册的能力')
    expect(describeToolError('{"reason": "path_outside_allowed_root", "path": "C:\\Users\\x\\Desktop"}')?.title).toBe('文件路径在安全根目录之外')
    expect(describeToolError('reason=llm_source_high_risk')?.advice).toContain('告警模式')
    expect(describeToolError('WebSocket closed')?.title).toBe('后端连接中断')
  })

  it('未知 reason 返回 null', () => {
    expect(describeToolError('some random failure')).toBeNull()
    expect(describeToolError('')).toBeNull()
    expect(hasKnownToolError('capability_not_found')).toBe(true)
    expect(hasKnownToolError('random')).toBe(false)
  })
})

describe('TracePanel', () => {
  const runs = [
    {
      task_id: 'run-1',
      task_type: 'chat',
      title: '查看桌面文件',
      status: 'failed',
      source: 'smolagents',
      started_at: 1770000000,
      finished_at: 1770000060,
      cost_tokens: 1200,
      error: 'action blocked by broker: capability_not_found',
    },
    {
      task_id: 'run-2',
      task_type: 'cron',
      title: '每日晨报',
      status: 'completed',
      source: 'cron',
      started_at: 1769990000,
      finished_at: 1769990010,
      cost_tokens: 0,
      error: '',
    },
  ]

  function makeClient() {
    return {
      status: 'connected',
      call: vi.fn().mockImplementation((method: string) => {
        if (method === 'trace.list') return Promise.resolve({ success: true, runs, count: runs.length })
        if (method === 'trace.stats') return Promise.resolve({ days: 7, total_runs: 2, by_type: { chat: 1, cron: 1 }, by_status: { completed: 1, failed: 1 }, success_rate: 0.5 })
        if (method === 'trace.get') {
          return Promise.resolve({
            success: true,
            run: {
              ...runs[0],
              steps: [
                { step_no: 1, step_type: 'tool_call', tool: 'file.list', summary: 'path=Desktop', ts: 1770000001 },
                { step_no: 2, step_type: 'error', tool: 'browser.navigate', summary: 'capability_not_found', ts: 1770000030 },
              ],
            },
          })
        }
        return Promise.resolve({ success: true })
      }),
      on: vi.fn().mockReturnValue(() => {}),
    }
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue(makeClient())
  })

  it('渲染 run 列表与统计', async () => {
    render(<TracePanel />)
    expect(await screen.findByText('查看桌面文件')).toBeTruthy()
    expect(screen.getByText('每日晨报')).toBeTruthy()
    expect(screen.getByText(/近 7 天运行/)).toBeTruthy()
    expect(screen.getByText('50%')).toBeTruthy()
  })

  it('选中 run 展示步骤时间线与友好化错误', async () => {
    render(<TracePanel />)
    fireEvent.click(await screen.findByText('查看桌面文件'))

    await waitFor(() => {
      expect(screen.getByText('调用了未注册的能力')).toBeTruthy()
    })
    // 步骤时间线
    expect(screen.getByText('file.list')).toBeTruthy()
    // 失败原因友好化建议（run 错误框 + 步骤提示各一处）
    expect(screen.getAllByText(/能力清单/).length).toBeGreaterThanOrEqual(1)
    // 耗时与 tokens
    expect(screen.getByText(/tokens：1200/)).toBeTruthy()
  })

  it('后端不可达时静默不崩', async () => {
    mockGetWsClient.mockReturnValue({
      status: 'disconnected',
      call: vi.fn(),
      on: vi.fn().mockReturnValue(() => {}),
    })
    render(<TracePanel />)
    await waitFor(() => {
      expect(screen.getByText(/暂无运行记录/)).toBeTruthy()
    })
  })
})
