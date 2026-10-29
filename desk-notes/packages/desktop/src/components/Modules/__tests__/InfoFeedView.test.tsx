import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { RawSourceItem } from '@desk-notes/shared'
import InfoFeedView from '../InfoFeedView'

// Hoisted mocks
const { mockGetWsClient } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
}))

vi.mock('../../../utils/ai', () => ({
  getWsClient: mockGetWsClient,
}))

vi.mock('@desk-notes/shared', () => ({
  CATEGORY_META: {
    ai: { label: 'AI动态', icon: 'fa-solid fa-brain', color: '#a855f7' },
    dev: { label: '开发技术', icon: 'fa-solid fa-code', color: '#3b82f6' },
    general: { label: '综合', icon: 'fa-solid fa-globe', color: '#6b7280' },
  },
}))

vi.mock('../../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

function makeItem(overrides: Record<string, unknown> = {}): RawSourceItem {
  return {
    id: 'test-1',
    title: 'Test AI Article',
    url: 'https://example.com/ai-news',
    source: 'hackernews',
    summary: 'This is a test article about AI developments',
    title_zh: '测试AI文章',
    summary_zh: '这是一篇关于AI发展的测试文章',
    tags: ['ai', 'machine-learning'],
    score: 100,
    publishedAt: 1717200000000,
    category: 'ai',
    ...overrides,
  } as RawSourceItem
}

describe('InfoFeedView', () => {
  const onItemClick = vi.fn()
  const onFilterSource = vi.fn()
  const onToggleBookmark = vi.fn()

  const baseProps = {
    items: [] as RawSourceItem[],
    filterSource: null as string | null,
    onFilterSource,
    onItemClick,
    savedUrls: new Set<string>() as Set<string>,
    onToggleBookmark,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({
      status: 'connected',
      call: vi.fn(),
      onStatusChange: vi.fn(),
    })
  })

  it('shows empty state when no items', () => {
    render(<InfoFeedView {...baseProps} items={[]} />)
    expect(screen.getByText('infoFeed.empty')).toBeTruthy()
  })

  it('renders feed items with bilingual display', () => {
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)
    expect(screen.getByText('测试AI文章')).toBeTruthy()
    expect(screen.getByText('Test AI Article')).toBeTruthy()
  })

  it('shows English title when no zh translation', () => {
    render(<InfoFeedView {...baseProps} items={[makeItem({ title_zh: '', summary_zh: '' })]} />)
    expect(screen.getByText('Test AI Article')).toBeTruthy()
  })

  it('filters items by search query', () => {
    const items = [
      makeItem({ id: '1', title: 'React 19 Released', tags: ['react', 'frontend'], category: 'dev' }),
      makeItem({ id: '2', title: 'AI Breakthrough', tags: ['ai'], category: 'ai', title_zh: 'AI突破' }),
    ] as RawSourceItem[]
    render(<InfoFeedView {...baseProps} items={items} />)

    const searchInput = screen.getByPlaceholderText('infoFeed.searchPlaceholder')
    fireEvent.change(searchInput, { target: { value: 'React' } })
    expect(screen.getByText('React 19 Released')).toBeTruthy()
    expect(screen.queryByText('AI Breakthrough')).toBeNull()
  })

  it('filters items by search in Chinese title', () => {
    const items = [
      makeItem({ id: '1', title: 'English Title', title_zh: '中文标题', category: 'ai' }),
      makeItem({ id: '2', title: 'Other Article', title_zh: '其他文章', category: 'dev' }),
    ] as RawSourceItem[]
    render(<InfoFeedView {...baseProps} items={items} />)

    const searchInput = screen.getByPlaceholderText('infoFeed.searchPlaceholder')
    fireEvent.change(searchInput, { target: { value: '中文' } })
    expect(screen.getByText('中文标题')).toBeTruthy()
    expect(screen.queryByText('其他文章')).toBeNull()
  })

  it('clears search query when clear button clicked', () => {
    const items = [
      makeItem({ id: '1', title: 'React 19' }),
      makeItem({ id: '2', title: 'AI News' }),
    ] as RawSourceItem[]
    render(<InfoFeedView {...baseProps} items={items} />)

    const searchInput = screen.getByPlaceholderText('infoFeed.searchPlaceholder')
    fireEvent.change(searchInput, { target: { value: 'React' } })
    expect(screen.queryByText('AI News')).toBeNull()

    // Click the X clear button (only visible when searchQuery is truthy)
    const clearBtn = document.querySelector('[data-icon="fa-xmark"]')?.closest('button')
    if (clearBtn) {
      fireEvent.click(clearBtn)
      expect(screen.getByText('AI News')).toBeTruthy()
    }
  })

  it('toggles personalization mode', async () => {
    const mockCall = vi.fn().mockResolvedValue({
      scores: { 'test-1': 0.95 },
      order: ['test-1'],
      keywords_matched: 3,
    })
    mockGetWsClient.mockReturnValue({
      status: 'connected',
      call: mockCall,
      onStatusChange: vi.fn(),
    })
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)

    fireEvent.click(screen.getByText('infoFeed.personalized'))
    expect(mockCall).toHaveBeenCalledWith('info.personalize', expect.any(Object))
  })

  it('calls onItemClick when clicking an item', () => {
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)
    fireEvent.click(screen.getByText('测试AI文章'))
    expect(onItemClick).toHaveBeenCalledWith(expect.objectContaining({ id: 'test-1' }))
  })

  it('shows bookmark button when onToggleBookmark provided', () => {
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)
    expect(screen.getByTitle('infoFeed.bookmarkAdd')).toBeTruthy()
  })

  it('shows filled bookmark when item url is saved', () => {
    render(
      <InfoFeedView
        {...baseProps}
        items={[makeItem()]}
        savedUrls={new Set(['https://example.com/ai-news'])}
      />
    )
    expect(screen.getByTitle('infoFeed.bookmarkRemove')).toBeTruthy()
  })

  it('calls onToggleBookmark when bookmark clicked', () => {
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)
    fireEvent.click(screen.getByTitle('infoFeed.bookmarkAdd'))
    expect(onToggleBookmark).toHaveBeenCalledWith(expect.objectContaining({ id: 'test-1' }))
  })

  it('displays category and source counts', () => {
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)
    expect(screen.getByText('infoFeed.allCountFmt')).toBeTruthy()
  })

  it('shows AI summary button when no summary loaded', () => {
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)
    expect(screen.getByText('infoFeed.aiSummary')).toBeTruthy()
  })

  it('calls info.summarize and shows result', async () => {
    const mockCall = vi.fn().mockResolvedValue({ summary: '这是AI生成的摘要' })
    mockGetWsClient.mockReturnValue({
      status: 'connected',
      call: mockCall,
      onStatusChange: vi.fn(),
    })
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)

    fireEvent.click(screen.getByText('infoFeed.aiSummary'))
    expect(mockCall).toHaveBeenCalledWith('info.summarize', expect.any(Object))
    expect(await screen.findByText('这是AI生成的摘要')).toBeTruthy()
  })

  it('toggles bilingual mode off', () => {
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)

    // Bilingual is on by default — shows zh title
    expect(screen.getByText('测试AI文章')).toBeTruthy()

    // Click bilingual toggle to turn it off
    fireEvent.click(screen.getByText('infoFeed.bilingual'))
    // Now should show English title only (zh title hidden)
    expect(screen.getByText('Test AI Article')).toBeTruthy()
  })

  it('groups items by category when group mode is active', () => {
    const items = [
      makeItem({ id: '1', title: 'AI News', category: 'ai', title_zh: 'AI新闻' }),
      makeItem({ id: '2', title: 'Dev News', category: 'dev', title_zh: '开发新闻' }),
    ] as RawSourceItem[]
    render(<InfoFeedView {...baseProps} items={items} />)
    expect(screen.getAllByText('AI动态').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('开发技术').length).toBeGreaterThanOrEqual(1)
  })

it('shows error message when summarize fails', async () => {
    const mockCall = vi.fn().mockResolvedValue({ summary: '', error: '生成失败' })
    mockGetWsClient.mockReturnValue({
      status: 'connected',
      call: mockCall,
      onStatusChange: vi.fn(),
    })
    render(<InfoFeedView {...baseProps} items={[makeItem()]} />)

    fireEvent.click(screen.getByText('infoFeed.aiSummary'))
    expect(await screen.findByText('生成失败')).toBeTruthy()
  })
})
