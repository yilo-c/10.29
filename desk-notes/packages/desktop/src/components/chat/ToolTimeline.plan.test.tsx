import { describe, it, expect } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ToolTimeline } from './ToolTimeline'
import { normalizeAgentEvents } from './agentEvents'
import type { ChatStreamEvent } from './useChatStream'

const planProgress: ChatStreamEvent = {
  type: 'tool_progress',
  data: JSON.stringify({
    tool: 'update_plan',
    args: { goal: '整理笔记', steps: [{ title: '查笔记', status: 'running' }, { title: '写摘要', status: 'pending' }] },
    status: 'running',
  }),
} as ChatStreamEvent

describe('update_plan 计划渲染', () => {
  it('normalize 把 update_plan 归为 plan 且带结构化 steps', () => {
    const views = normalizeAgentEvents([planProgress])
    expect(views[0].kind).toBe('plan')
    expect(views[0].title).toBe('整理笔记')
    expect(views[0].steps).toHaveLength(2)
    expect(views[0].steps?.[0]).toEqual({ title: '查笔记', status: 'running' })
  })

  it('普通工具事件仍归 tool（不受 plan 逻辑影响）', () => {
    const tool: ChatStreamEvent = {
      type: 'tool_progress',
      data: JSON.stringify({ tool: 'notes_list', args: { limit: 5 }, status: 'running' }),
    } as ChatStreamEvent
    const views = normalizeAgentEvents([tool])
    expect(views[0].kind).toBe('tool')
    expect(views[0].steps).toBeUndefined()
  })

  it('ToolTimeline 无工具但有计划时，展开后渲染步骤清单', () => {
    const views = normalizeAgentEvents([planProgress])
    render(<ToolTimeline events={views} />)
    // 折叠条标签为 goal；点击展开
    const toggle = screen.getByRole('button')
    expect(toggle.textContent).toContain('整理笔记')
    fireEvent.click(toggle)
    expect(screen.getByText('查笔记')).toBeTruthy()
    expect(screen.getByText('写摘要')).toBeTruthy()
  })
})
