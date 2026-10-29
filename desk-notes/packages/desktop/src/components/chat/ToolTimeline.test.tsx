import { describe, expect, it } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolTimeline } from './ToolTimeline'
import type { AgentEventView } from './agentEvents'

const tool = (status: AgentEventView['status'], detail = ''): AgentEventView => ({
  id: `ev-${Math.random()}`,
  kind: 'tool',
  title: '工具结果',
  detail,
  status,
})

describe('ToolTimeline', () => {
  it('renders nothing when there are no tool steps', () => {
    const { container } = render(<ToolTimeline events={[{ id: 'p1', kind: 'plan', title: '计划', detail: '', status: 'running' }]} />)
    expect(container.innerHTML).toBe('')
  })

  it('collapses by default and expands on click', () => {
    render(<ToolTimeline events={[tool('done', '找到 3 条结果'), tool('running', '运行中')]} durationSec={12.4} />)
    // 折叠态：只显示摘要，不显示步骤明细
    expect(screen.getByText('使用了 2 个工具 · 12.4s')).toBeTruthy()
    expect(screen.queryByText('找到 3 条结果')).toBeNull()
    fireEvent.click(screen.getByRole('button', { expanded: false }))
    expect(screen.getByText('找到 3 条结果')).toBeTruthy()
    expect(screen.getByText('运行中')).toBeTruthy()
  })

  it('omits duration segment when not provided', () => {
    render(<ToolTimeline events={[tool('done')]} />)
    expect(screen.getByText('使用了 1 个工具')).toBeTruthy()
  })
})