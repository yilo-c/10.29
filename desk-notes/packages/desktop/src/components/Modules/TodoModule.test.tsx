import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TodoModule from './TodoModule'
// 桌面 store 是 @desk-notes/shared store 的同一单例 re-export，todos/locale 同源
import { useStore } from '@desk-notes/shared'

// 桩化列表/详情/头，聚焦 TodoModule 自身的「学习计划」编排与科目筛选
vi.mock('../Todo/TodoList', () => ({ default: () => <div data-testid="todo-list" /> }))
vi.mock('../Todo/TodoDetailPanel', () => ({ default: () => <div data-testid="detail" /> }))
vi.mock('../Common/PageHeader', () => ({
  default: (p: { title?: string }) => <div>{p.title}</div>,
}))

beforeEach(() => {
  useStore.setState({ locale: 'zh-CN', todos: [], filterSubject: '' })
})

describe('TodoModule', () => {
  it('一键生成学习计划：向 store 批量写入 5 条科目待办并提示已创建', async () => {
    render(<TodoModule />)

    fireEvent.click(screen.getByRole('button', { name: '创建今日备考任务' }))

    await waitFor(() => expect(useStore.getState().todos).toHaveLength(5))
    const subjects = useStore.getState().todos.map((t: any) => t.subject)
    expect(subjects).toEqual(expect.arrayContaining(['politics', 'english', 'math']))
    expect(useStore.getState().todos.every((t: any) => t.category === 'study')).toBe(true)
    expect(screen.getByText('已创建备考任务')).toBeTruthy()
  })

  it('有科目待办时渲染对应科目筛选标签及数量', () => {
    useStore.setState({
      todos: [{ id: 't1', text: '背单词', done: false, subject: 'english', createdAt: Date.now() }],
    })
    render(<TodoModule />)

    expect(screen.getByText('(1)')).toBeTruthy()
    expect(screen.getByText('全部')).toBeTruthy()
  })
})
