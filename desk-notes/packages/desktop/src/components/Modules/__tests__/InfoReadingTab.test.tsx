import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import InfoReadingTab from '../InfoReadingTab'
import type { RawSourceItem } from '@desk-notes/shared'

function makeItem(overrides: Partial<RawSourceItem> = {}): RawSourceItem {
  return {
    id: 'test-1',
    title: 'Test Article',
    url: 'https://example.com/test',
    source: 'hackernews',
    summary: 'A test article summary',
    title_zh: '测试文章',
    summary_zh: '测试摘要',
    tags: ['test', 'ai'],
    score: 42,
    publishedAt: 1717200000000,
    category: 'ai',
    ...overrides,
  }
}

describe('InfoReadingTab', () => {
  const baseProps = {
    savedArticles: [] as RawSourceItem[],
    loading: false,
    backendConnected: true,
    onToggleBookmark: vi.fn(),
  }

  it('shows loading spinner when loading', () => {
    render(<InfoReadingTab {...baseProps} loading={true} />)
    expect(screen.getByText('加载中...')).toBeTruthy()
  })

  it('shows empty state when no articles', () => {
    render(<InfoReadingTab {...baseProps} savedArticles={[]} />)
    expect(screen.getByText('暂无收藏文章')).toBeTruthy()
  })

  it('shows offline empty state when backend disconnected', () => {
    render(<InfoReadingTab {...baseProps} savedArticles={[]} backendConnected={false} />)
    expect(screen.getByText('后端未连接')).toBeTruthy()
  })

  it('renders a list of saved articles', () => {
    const items = [
      makeItem({ id: 'test-1', title_zh: '测试文章' }),
      makeItem({ id: 'test-2', title: 'Second Article', title_zh: '第二篇文章', url: 'https://example.com/2' }),
    ]
    render(<InfoReadingTab {...baseProps} savedArticles={items} />)
    expect(screen.getByText('测试文章')).toBeTruthy()
    expect(screen.getByText('第二篇文章')).toBeTruthy()
  })

  it('shows original title when title_zh differs', () => {
    const item = makeItem()
    render(<InfoReadingTab {...baseProps} savedArticles={[item]} />)
    expect(screen.getByText('Test Article')).toBeTruthy()
  })

  it('calls onToggleBookmark when delete button clicked', () => {
    const onToggleBookmark = vi.fn()
    const item = makeItem()
    render(<InfoReadingTab {...baseProps} savedArticles={[item]} onToggleBookmark={onToggleBookmark} />)

    // The trash button has title "取消收藏"
    const btn = screen.getByTitle('取消收藏')
    fireEvent.click(btn)
    expect(onToggleBookmark).toHaveBeenCalledWith(item)
  })

  it('shows source label for each article', () => {
    const item = makeItem()
    render(<InfoReadingTab {...baseProps} savedArticles={[item]} />)
    expect(screen.getByText('hackernews')).toBeTruthy()
  })

  it('renders external link for articles with url', () => {
    const item = makeItem()
    render(<InfoReadingTab {...baseProps} savedArticles={[item]} />)
    const link = screen.getByTitle('打开原文')
    expect(link).toBeTruthy()
    expect(link.closest('a')?.getAttribute('href')).toBe('https://example.com/test')
  })
})
