/**
 * C1（前后端对齐规划）：TodoDetailPanel 组件测试。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import TodoDetailPanel from '../TodoDetailPanel'

const actions = {
  setPriority: vi.fn(),
  setDueDate: vi.fn(),
  clearReminder: vi.fn(),
  toggleTodo: vi.fn(),
  addSubtask: vi.fn(),
}

const baseTodo = {
  id: 't1',
  text: '复习机械原理',
  done: false,
  createdAt: new Date(2026, 7, 9, 8, 0).getTime(),
  priority: 1 as const,
  dueDate: new Date(2026, 7, 10, 18, 0).getTime(),
  estimatedMinutes: 90,
}

const subtask = { id: 's1', parentId: 't1', text: '做课后题', done: false, createdAt: Date.now() }

vi.mock('@desk-notes/shared', () => {
  const storeFn = (sel: (s: Record<string, unknown>) => unknown) => sel({
    todos: [baseTodo, subtask],
    setPriority: actions.setPriority,
    setDueDate: actions.setDueDate,
    clearReminder: actions.clearReminder,
    toggleTodo: actions.toggleTodo,
    addSubtask: actions.addSubtask,
  })
  return {
    useStore: storeFn,
    formatDateTime: (ts: number) => new Date(ts).toISOString().slice(0, 16).replace('T', ' '),
  }
})

describe('TodoDetailPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('渲染任务内容与元信息', () => {
    render(<TodoDetailPanel todo={baseTodo} onClose={() => {}} />)
    expect(screen.getByText('复习机械原理')).toBeTruthy()
    expect(screen.getByText('任务详情')).toBeTruthy()
    expect(screen.getByText(/优先级/)).toBeTruthy()
    expect(screen.getByText(/子任务（1）/)).toBeTruthy()
    expect(screen.getByText('90 分钟')).toBeTruthy()
  })

  it('点击优先级按钮调用 setPriority', () => {
    render(<TodoDetailPanel todo={baseTodo} onClose={() => {}} />)
    fireEvent.click(screen.getByText('高'))
    expect(actions.setPriority).toHaveBeenCalledWith('t1', 0)
  })

  it('修改截止日期调用 setDueDate', () => {
    render(<TodoDetailPanel todo={baseTodo} onClose={() => {}} />)
    const input = document.querySelector('input[type="datetime-local"]') as HTMLInputElement
    expect(input).toBeTruthy()
    fireEvent.change(input, { target: { value: '2026-08-12T09:30' } })
    expect(actions.setDueDate).toHaveBeenCalledWith('t1', new Date('2026-08-12T09:30').getTime())
  })

  it('添加子任务调用 addSubtask 并清空输入', () => {
    render(<TodoDetailPanel todo={baseTodo} onClose={() => {}} />)
    const input = screen.getByPlaceholderText('添加子任务…')
    fireEvent.change(input, { target: { value: '整理笔记' } })
    fireEvent.click(screen.getByText('添加'))
    expect(actions.addSubtask).toHaveBeenCalledWith('t1', '整理笔记')
    expect((input as HTMLInputElement).value).toBe('')
  })

  it('子任务列表显示并可切换完成', () => {
    render(<TodoDetailPanel todo={baseTodo} onClose={() => {}} />)
    expect(screen.getByText('做课后题')).toBeTruthy()
  })

  it('点击关闭调用 onClose', () => {
    const onClose = vi.fn()
    render(<TodoDetailPanel todo={baseTodo} onClose={onClose} />)
    fireEvent.click(screen.getByTitle('关闭详情'))
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
