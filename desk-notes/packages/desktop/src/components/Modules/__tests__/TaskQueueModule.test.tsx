import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { TaskQueueModule } from '../TaskQueueModule'

const callMock = vi.fn()
vi.mock('../../../utils/ai', () => ({
  getWsClient: () => ({ call: callMock }),
}))

function seed({ tasks = [], dead = [], stats = {} }: {
  tasks?: Record<string, unknown>[]; dead?: Record<string, unknown>[]; stats?: Record<string, number>
} = {}) {
  callMock
    .mockResolvedValueOnce({ success: true, tasks })
    .mockResolvedValueOnce({ success: true, stats })
    .mockResolvedValueOnce({ success: true, tasks: dead })
}

beforeEach(() => { callMock.mockReset() })

describe('TaskQueueModule', () => {
  it('渲染任务队列并读取统计', async () => {
    seed({
      tasks: [{ task_id: 't-1', capability_id: 'info-fetch', queue_name: 'default', status: 'running', attempts: 1, max_retries: 3 }],
      stats: { pending: 1, running: 1, completed: 0, failed: 0, dead_letter: 0, cancelled: 0 },
    })
    render(<TaskQueueModule />)
    await waitFor(() => expect(screen.getByText('任务队列')).toBeTruthy())
    await waitFor(() => expect(screen.getByText('info-fetch')).toBeTruthy())
    // 统计卡与任务行都渲染状态文本（running），用 getAllByText 断言至少一处
    await waitFor(() => expect(screen.getAllByText('running').length).toBeGreaterThanOrEqual(1))
  })

  it('空队列显示"队列为空"', async () => {
    seed({})
    render(<TaskQueueModule />)
    await waitFor(() => expect(screen.getByText('队列为空')).toBeTruthy())
  })

  it('清除已完成调用 task_queue.purge_completed', async () => {
    seed()
    render(<TaskQueueModule />)
    await waitFor(() => expect(screen.getByRole('button', { name: '清理已完成' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: '清理已完成' }))
    await waitFor(() => {
      expect(callMock).toHaveBeenCalledWith('task_queue.purge_completed', expect.anything())
    })
  })

  it('后端未连接时显示错误', async () => {
    callMock.mockRejectedValue(new Error('Not connected'))
    render(<TaskQueueModule />)
    expect(await screen.findByRole('alert')).toBeTruthy()
  })
})