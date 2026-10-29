/**
 * D3（前后端对齐规划）：InfoLibraryView 测试。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import InfoLibraryView from '../InfoLibraryView'

const mockGetWsClient = vi.fn()

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

const items = [
  { id: 1, title: 'Rust 异步运行时对比', url: 'https://example.com/1', source: 'HN', category: 'tech', read: false, saved: false, collected_at: Date.now() / 1000 - 3600 },
  { id: 2, title: '已读条目', url: 'https://example.com/2', source: 'Dev.to', category: 'tech', read: true, saved: true, collected_at: Date.now() / 1000 - 7200 },
]

const sections = [
  { id: 'tech', name: '技术', keywords: ['rust'], is_default: true },
  { id: 'custom-1', name: '自建板块', keywords: [], is_default: false },
]

function makeClient() {
  return {
    status: 'connected',
    call: vi.fn().mockImplementation((method: string) => {
      if (method === 'info.items.list') return Promise.resolve({ items, total: items.length })
      if (method === 'info.sections.list') return Promise.resolve({ sections })
      if (method === 'info.items.toggle_read') return Promise.resolve({ success: true })
      if (method === 'info.items.toggle_saved') return Promise.resolve({ success: true })
      if (method === 'info.items.save_to_note') return Promise.resolve({ success: true, note_id: 'n1' })
      if (method === 'info.sections.create') return Promise.resolve({ success: true })
      if (method === 'info.sections.delete') return Promise.resolve({ success: true })
      return Promise.resolve({ success: true })
    }),
    on: vi.fn().mockReturnValue(() => {}),
  }
}

describe('InfoLibraryView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue(makeClient())
  })

  it('渲染条目列表与板块', async () => {
    render(<InfoLibraryView />)
    expect(await screen.findByText('Rust 异步运行时对比')).toBeTruthy()
    expect(screen.getByText('已读条目')).toBeTruthy()
    expect(screen.getByText('技术')).toBeTruthy()
    expect(screen.getByText('自建板块')).toBeTruthy()
    expect(screen.getByText(/共 2 条/)).toBeTruthy()
  })

  it('默认板块不可删除，自定义板块可删除', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    render(<InfoLibraryView />)
    await screen.findByText('自建板块')

    // 默认板块显示"默认"标记而非删除按钮
    expect(screen.getByText('默认')).toBeTruthy()

    const deleteButtons = screen.getAllByTitle('删除板块')
    expect(deleteButtons).toHaveLength(1)
    fireEvent.click(deleteButtons[0])
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('info.sections.delete', { section_id: 'custom-1' })
    })
  })

  it('收藏与保存到笔记', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    render(<InfoLibraryView />)
    await screen.findByText('Rust 异步运行时对比')

    const saveButtons = screen.getAllByTitle('保存到笔记')
    fireEvent.click(saveButtons[0])
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('info.items.save_to_note', { item_id: 1 })
    })
    await waitFor(() => {
      expect(screen.getByText('已保存到笔记')).toBeTruthy()
    })

    const starButtons = screen.getAllByTitle('收藏')
    fireEvent.click(starButtons[0])
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('info.items.toggle_saved', { item_id: 1 })
    })
  })

  it('创建板块', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    render(<InfoLibraryView />)
    await screen.findByText('自建板块')

    const input = screen.getByPlaceholderText('新板块名…')
    fireEvent.change(input, { target: { value: 'AI 动态' } })
    fireEvent.click(screen.getByText('添加'))
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('info.sections.create', { name: 'AI 动态', keywords: [] })
    })
  })

  it('切换未读过滤传递 read=false', async () => {
    const client = makeClient()
    mockGetWsClient.mockReturnValue(client)
    render(<InfoLibraryView />)
    await screen.findByText('Rust 异步运行时对比')

    fireEvent.click(screen.getByText('未读'))
    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('info.items.list', { limit: 50, read: false })
    })
  })
})
