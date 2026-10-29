/**
 * S2-3 死信红角标：NavSidebar 自动化入口角标渲染测试。
 *
 * 数据源为既有 task_queue.stats RPC 的 stats.dead_letter 字段
 * （与 TaskQueueModule 消费同一接口，前端不新增后端调用）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import NavSidebar from '../NavSidebar'
import { useStore } from '../../../store/useStore'

const callMock = vi.fn()

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => ({
    status: 'connected',
    call: callMock,
    onStatusChange: () => () => {},
  }),
}))

function seedStats(deadLetter: number) {
  callMock.mockResolvedValue({ success: true, stats: { pending: 0, dead_letter: deadLetter } })
}

beforeEach(() => {
  callMock.mockReset()
  useStore.setState({ activeModule: 'chat', navCollapsed: false, locale: 'zh-CN' })
})

describe('NavSidebar 死信红角标（S2-3）', () => {
  it('stats.dead_letter > 0 时自动化入口渲染常驻角标并带双语 tooltip', async () => {
    seedStats(3)
    render(<NavSidebar />)
    const badge = await screen.findByTestId('nav-deadletter-badge')
    expect(badge.textContent).toBe('3')
    // tooltip 走 i18n key nav.deadLetterBadge，{count} 插值为死信条数
    expect(badge.closest('button')?.getAttribute('title')).toBe(
      '有 3 条任务重试耗尽，点击查看',
    )
  })

  it('stats.dead_letter = 0 时不渲染角标', async () => {
    seedStats(0)
    const { container } = render(<NavSidebar />)
    await waitFor(() => expect(callMock).toHaveBeenCalledWith('task_queue.stats', {}))
    expect(container.querySelector('[data-testid="nav-deadletter-badge"]')).toBeNull()
  })

  it('点击角标跳转到任务队列（点击查看语义）', async () => {
    seedStats(2)
    render(<NavSidebar />)
    const badge = await screen.findByTestId('nav-deadletter-badge')
    fireEvent.click(badge)
    expect(useStore.getState().activeModule).toBe('task-queue')
  })

  it('英文语言下 tooltip 为对应文案', async () => {
    useStore.setState({ locale: 'en' })
    seedStats(5)
    render(<NavSidebar />)
    const badge = await screen.findByTestId('nav-deadletter-badge')
    expect(badge.closest('button')?.getAttribute('title')).toBe(
      '5 task(s) exhausted retries. Click to view.',
    )
  })
})
