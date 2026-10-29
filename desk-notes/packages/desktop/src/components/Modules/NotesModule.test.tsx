import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import NotesModule from './NotesModule'
import { useStore } from '../../store/useStore'

const mockGetWsClient = vi.fn()
vi.mock('../../utils/ai', () => ({ getWsClient: () => mockGetWsClient() }))
// runTrackedJob 直接把 run() 透出执行，聚焦验证 url_learning 接线
vi.mock('@desk-notes/shared', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@desk-notes/shared')>()
  return { ...mod, runTrackedJob: (opts: { run: () => Promise<unknown> }) => opts.run() }
})

// 桩化重子视图，隔离出 NotesModule 自身的编排逻辑
vi.mock('../Common/NotesBrowser', () => ({ default: () => <div data-testid="browser" /> }))
vi.mock('../Common/NoteEditor', () => ({ default: () => <div data-testid="editor" /> }))
vi.mock('../Sidebar/FolderTree', () => ({ default: () => <div data-testid="folders" /> }))
vi.mock('../Common/TrashView', () => ({ default: () => <div data-testid="trash" /> }))
vi.mock('../Common/DailyNoteView', () => ({ default: () => <div data-testid="daily" /> }))
vi.mock('../Common/PageHeader', () => ({
  default: (p: { title?: string; actions?: React.ReactNode }) => (
    <div>{p.title}{p.actions}</div>
  ),
}))

beforeEach(() => {
  vi.clearAllMocks()
  useStore.setState({ locale: 'zh-CN', floatingNotes: [], editingNoteId: null })
})

function openUrlInput() {
  render(<NotesModule />)
  fireEvent.click(screen.getByTitle('从链接创建'))
  return screen.getByPlaceholderText('输入网页链接，自动提取内容创建笔记…')
}

describe('NotesModule', () => {
  it('URL 学习成功：调 url_learning.learn 并把结果落为带标签的笔记', async () => {
    const ws = { status: 'connected', call: vi.fn().mockResolvedValue({
      title: '量子计算入门', summary: '摘要', key_points: ['要点1'], learning_suggestions: [],
    }) }
    mockGetWsClient.mockReturnValue(ws)
    const input = openUrlInput()

    fireEvent.change(input, { target: { value: 'https://example.com/ai' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() =>
      expect(ws.call).toHaveBeenCalledWith('url_learning.learn', { url: 'https://example.com/ai', timeout: 30 }),
    )
    await waitFor(() => expect(useStore.getState().floatingNotes).toHaveLength(1))
    const note = useStore.getState().floatingNotes[0]
    expect(note.title).toBe('量子计算入门')
    expect(note.tags).toContain('url-learning')
  })

  it('后端断连时不发 RPC、也不创建笔记', async () => {
    mockGetWsClient.mockReturnValue({ status: 'disconnected', call: vi.fn() })
    const input = openUrlInput()
    fireEvent.change(input, { target: { value: 'https://x.com' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await new Promise((r) => setTimeout(r, 30))
    expect(useStore.getState().floatingNotes).toHaveLength(0)
  })

  it('学习失败：展示降级文案且不建笔记', async () => {
    const ws = { status: 'connected', call: vi.fn().mockRejectedValue(new Error('boom')) }
    mockGetWsClient.mockReturnValue(ws)
    const input = openUrlInput()
    fireEvent.change(input, { target: { value: 'https://bad' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(await screen.findByText('学习失败，请检查链接是否有效')).toBeTruthy()
    expect(useStore.getState().floatingNotes).toHaveLength(0)
  })
})
