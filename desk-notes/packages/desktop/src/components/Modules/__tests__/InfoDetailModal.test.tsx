import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import type { RawSourceItem } from '@desk-notes/shared'
import InfoDetailModal from '../InfoDetailModal'

// Hoisted mocks
const { mockGetWsClient } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
}))

// i18n mock: return the key name for testing
const { mockT } = vi.hoisted(() => ({
  mockT: vi.fn((key: string) => key),
}))

vi.mock('../../../utils/ai', () => ({
  getWsClient: mockGetWsClient,
}))

vi.mock('../../../i18n', () => ({
  useTranslation: () => ({ t: mockT }),
}))

vi.mock('@desk-notes/shared', () => ({
  CATEGORY_META: {
    ai: { label: 'AI', icon: 'fa-solid fa-brain', color: '#a855f7' },
    general: { label: '综合', icon: 'fa-solid fa-globe', color: '#6b7280' },
  },
}))

vi.mock('react-markdown', () => ({
  default: ({ children }: { children: string }) => <div data-testid="markdown">{children}</div>,
}))

vi.mock('remark-gfm', () => ({ default: () => {} }))

function makeItem(overrides: Record<string, unknown> = {}): RawSourceItem {
  return {
    id: 'test-1',
    title: 'Test Article',
    url: 'https://example.com/test',
    source: 'hackernews',
    summary: 'A test article summary for testing',
    title_zh: '测试文章',
    summary_zh: '测试摘要',
    tags: ['test', 'ai'],
    score: 42,
    publishedAt: 1717200000000,
    category: 'ai',
    ...overrides,
  } as RawSourceItem
}

describe('InfoDetailModal', () => {
  const onClose = vi.fn()
  const onToggleBookmark = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({
      status: 'connected',
      call: vi.fn(),
      onStatusChange: vi.fn(),
    })
  })

  it('renders null when item is null', () => {
    const { container } = render(<InfoDetailModal item={null} onClose={onClose} />)
    expect(container.innerHTML).toBe('')
  })

  it('renders item title and metadata', () => {
    render(<InfoDetailModal item={makeItem()} onClose={onClose} />)
    expect(screen.getByText('测试文章')).toBeTruthy()
    expect(screen.getByText('Test Article')).toBeTruthy()
    expect(screen.getByText('#test #ai')).toBeTruthy()
  })

  it('shows bookmark as saved when url is in savedUrls', () => {
    render(
      <InfoDetailModal
        item={makeItem()}
        onClose={onClose}
        savedUrls={new Set(['https://example.com/test'])}
        onToggleBookmark={onToggleBookmark}
      />
    )
    expect(screen.getByText('infoDetail.bookmarked')).toBeTruthy()
  })

  it('shows bookmark as unsaved when url not in savedUrls', () => {
    render(
      <InfoDetailModal
        item={makeItem()}
        onClose={onClose}
        savedUrls={new Set()}
        onToggleBookmark={onToggleBookmark}
      />
    )
    expect(screen.getByText('infoDetail.bookmarkLater')).toBeTruthy()
  })

  it('calls onToggleBookmark when bookmark button clicked', () => {
    render(
      <InfoDetailModal
        item={makeItem()}
        onClose={onClose}
        savedUrls={new Set()}
        onToggleBookmark={onToggleBookmark}
      />
    )
    fireEvent.click(screen.getByText('infoDetail.bookmarkLater'))
    expect(onToggleBookmark).toHaveBeenCalledWith(expect.objectContaining({ id: 'test-1' }))
  })

  it('does not show bookmark button when onToggleBookmark not provided', () => {
    render(<InfoDetailModal item={makeItem()} onClose={onClose} />)
    expect(screen.queryByText('infoDetail.bookmarkLater')).toBeNull()
    expect(screen.queryByText('infoDetail.bookmarked')).toBeNull()
  })

  it('shows translate button for English-only content', () => {
    const item = makeItem({ title_zh: '', summary_zh: '' })
    render(<InfoDetailModal item={item} onClose={onClose} />)
    expect(screen.getByText('infoDetail.translateAll')).toBeTruthy()
  })

  it('calls translate RPC and shows result', async () => {
    const mockCall = vi.fn().mockResolvedValue({ translated: '翻译后的文本' })
    mockGetWsClient.mockReturnValue({
      status: 'connected',
      call: mockCall,
      onStatusChange: vi.fn(),
    })
    const item = makeItem({ title_zh: '', summary_zh: '' })
    render(<InfoDetailModal item={item} onClose={onClose} />)

    fireEvent.click(screen.getByText('infoDetail.translateAll'))
    expect(mockCall).toHaveBeenCalledWith('translate.text', expect.any(Object))
    expect(await screen.findByText('翻译后的文本')).toBeTruthy()
  })

  it('shows offline warning when translate clicked and backend disconnected', () => {
    mockGetWsClient.mockReturnValue({
      status: 'disconnected',
      call: vi.fn(),
      onStatusChange: vi.fn(),
    })
    const item = makeItem({ title_zh: '', summary_zh: '' })
    render(<InfoDetailModal item={item} onClose={onClose} />)

    fireEvent.click(screen.getByText('infoDetail.translateAll'))
    expect(screen.getByText('infoDetail.backendNotConnected')).toBeTruthy()
  })

  it('calls notes.create RPC on save to knowledge base', async () => {
    const mockCall = vi.fn().mockResolvedValue({})
    mockGetWsClient.mockReturnValue({
      status: 'connected',
      call: mockCall,
      onStatusChange: vi.fn(),
    })
    render(<InfoDetailModal item={makeItem()} onClose={onClose} />)

    fireEvent.click(screen.getByText('infoDetail.saveToKnowledgeBase'))
    expect(mockCall).toHaveBeenCalledWith('notes.create', expect.objectContaining({
      title: '测试文章',
    }))
    expect(await screen.findByText('infoDetail.savedToKb')).toBeTruthy()
  })

  it('calls onClose when backdrop clicked', () => {
    render(<InfoDetailModal item={makeItem()} onClose={onClose} />)
    // The outermost div is the backdrop with onClick={onClose}
    const backdrop = document.querySelector('.fixed.inset-0')
    if (backdrop) {
      fireEvent.click(backdrop)
      expect(onClose).toHaveBeenCalled()
    }
  })
})
