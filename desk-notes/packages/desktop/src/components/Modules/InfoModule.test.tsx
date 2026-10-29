import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react'

// 键名契约测试：RSS 订阅项与「稍后读」文章在前端读取的字段/参数
// 必须与后端 handler 的返回逐字对齐（键名漂移会静默变空白，不抛错）。
const mockCall = vi.fn()

vi.mock('../../utils/ai', () => ({
  getWsClient: () => ({ status: 'connected', call: mockCall }),
}))

vi.mock('../../FloatingBubble/useBackendEvents', () => ({
  useBackendEvents: () => ({ lastEvent: () => null }),
}))

// 用键名直回替代真实文案，避免测试耦合语言包
vi.mock('../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

import InfoModule from './InfoModule'

// 后端 services/rss_subscriber.py:list_subscriptions 的真实返回形状
const FEED = {
  id: 'feed-hash-1',
  url: 'https://example.com/feed.xml',
  name: '示例订阅源',
  category_hint: 'ai',
  feed_title: 'Example Feed',
  last_fetched_at: '2026-09-10T08:00:00+00:00',
  item_count: 12,
  last_error: '超时（10s）',
}

// 后端 handlers/_reading.py:reading_list_articles 的真实返回形状
const ARTICLE = {
  id: 'a1b2c3d4e5f6a7b8',
  title: '稍后读文章标题',
  url: 'https://example.com/article',
  source: 'example',
  saved_at: '2026-09-01T00:00:00+00:00',
}

function seed(articles = [ARTICLE], feeds = [FEED]) {
  mockCall.mockImplementation((method: string) => {
    if (method === 'info.cached_items') return Promise.resolve({ brief: '', items: [] })
    if (method === 'reading.list_articles') return Promise.resolve({ articles, count: articles.length })
    if (method === 'rss.list') return Promise.resolve({ feeds, total: feeds.length })
    if (method === 'reading.delete_article') return Promise.resolve({ success: true })
    return Promise.resolve({})
  })
}

async function openPocketTab() {
  render(<InfoModule />)
  fireEvent.click(screen.getByText('info.pocket'))
}

describe('InfoModule 稍后读 / RSS 字段契约', () => {
  beforeEach(() => {
    mockCall.mockReset()
  })

  it('RSS 行的时间与错误列取后端 last_fetched_at / last_error', async () => {
    seed()
    await openPocketTab()

    expect(await screen.findByText('示例订阅源')).toBeTruthy()
    // 时间列必须由 last_fetched_at 渲染（旧代码读 last_fetched → 恒空）
    expect(screen.getByText(new Date(FEED.last_fetched_at).toLocaleDateString('zh-CN'))).toBeTruthy()
    // 错误列必须由 last_error 渲染（旧代码读 error → 恒空）
    expect(screen.getByText(FEED.last_error)).toBeTruthy()
  })

  it('退订传后端约定的 feed_id 参数且取 id 字段', async () => {
    seed()
    await openPocketTab()

    const row = (await screen.findByText('示例订阅源')).closest('div.flex.items-center') as HTMLElement
    fireEvent.click(within(row).getByRole('button'))

    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('rss.unsubscribe', { feed_id: FEED.id })
    })
  })

  it('删除已收藏文章传 article_id（uuid）而不是 url', async () => {
    seed()
    await openPocketTab()

    const link = await screen.findByRole('link', { name: ARTICLE.title })
    const card = link.closest('div.flex.items-start') as HTMLElement
    fireEvent.click(within(card).getByRole('button'))

    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('reading.delete_article', { article_id: ARTICLE.id })
    })
    expect(mockCall).not.toHaveBeenCalledWith('reading.delete_article', { article_id: ARTICLE.url })
  })
})
