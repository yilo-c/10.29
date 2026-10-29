import type { RawSourceItem } from '@desk-notes/shared/types'

const HN_TOP_URL = 'https://hacker-news.firebaseio.com/v0/topstories.json'
const HN_ITEM_URL = (id: number) => `https://hacker-news.firebaseio.com/v0/item/${id}.json`
const DEVTO_URL = 'https://dev.to/api/articles?top=1&per_page=10'

async function fetchHN(): Promise<RawSourceItem[]> {
  try {
    const ids: number[] = await fetch(HN_TOP_URL).then(r => {
      if (!r.ok) throw new Error('HN API failed')
      return r.json()
    })
    const items = await Promise.all(
      ids.slice(0, 15).map(id =>
        fetch(HN_ITEM_URL(id)).then(r => r.json()).catch(() => null)
      )
    )
    return items.filter(Boolean).map((i: Record<string, unknown>) => ({
      id: `hn-${i.id}`,
      title: String(i.title || ''),
      url: i.url ? String(i.url) : `https://news.ycombinator.com/item?id=${i.id}`,
      source: 'hackernews' as const,
      summary: String(i.title || ''),
      tags: [String(i.type || '')].filter(Boolean),
      score: Number(i.score || 0),
      publishedAt: Number(i.time || 0) * 1000,
    }))
  } catch {
    return []
  }
}

async function fetchDevTo(): Promise<RawSourceItem[]> {
  try {
    const items: Record<string, unknown>[] = await fetch(DEVTO_URL).then(r => {
      if (!r.ok) throw new Error('Dev.to API failed')
      return r.json()
    })
    return items.map((i: Record<string, unknown>) => ({
      id: `devto-${i.id}`,
      title: String(i.title || ''),
      url: String(i.url || ''),
      source: 'devto' as const,
      summary: String(i.description || ''),
      tags: Array.isArray(i.tag_list) ? i.tag_list as string[] : [],
      score: Number(i.positive_reactions_count || 0),
      publishedAt: new Date(String(i.published_at || Date.now())).getTime(),
    }))
  } catch {
    return []
  }
}

async function fetchGitHubTrending(): Promise<RawSourceItem[]> {
  try {
    // Use the unofficial trending API
    const data: Record<string, unknown>[] = await fetch('https://api.gitterapp.com/repositories?since=daily').then(r => {
      if (!r.ok) throw new Error('GitHub trending API failed')
      return r.json()
    })
    return (data || []).map((repo: Record<string, unknown>) => ({
      id: `gh-${repo.author}-${repo.name}`,
      title: `${repo.author}/${repo.name}`,
      url: `https://github.com/${repo.author}/${repo.name}`,
      source: 'github' as const,
      summary: String(repo.description || ''),
      tags: Array.isArray(repo.language) ? repo.language as string[] : repo.language ? [String(repo.language)] : [],
      score: Number(repo.stars || 0),
      publishedAt: Date.now(),
    }))
  } catch {
    return []
  }
}

async function fetchReddit(): Promise<RawSourceItem[]> {
  try {
    const data = await fetch('https://www.reddit.com/r/programming/hot/.json?limit=10', {
      headers: { 'User-Agent': 'desk-notes/1.0' },
    }).then(r => {
      if (!r.ok) throw new Error('Reddit API failed')
      return r.json()
    })
    const children: Record<string, unknown>[] = data?.data?.children || []
    return children.map((child: Record<string, unknown>) => {
      const post = child?.data as Record<string, unknown> || {}
      return {
        id: `reddit-${post.id}`,
        title: String(post.title || ''),
        url: String(post.url || ''),
        source: 'reddit' as const,
        summary: String(post.selftext || post.title || '').slice(0, 200),
        tags: [],
        score: Number(post.score || 0),
        publishedAt: Number(post.created_utc || 0) * 1000,
      }
    })
  } catch {
    return []
  }
}

export async function fetchAllSources(): Promise<{ items: RawSourceItem[]; errors: { source: string; error: string }[] }> {
  // Check for CORS (browser mode) — all external fetch calls will fail
  const isCorsEnv = typeof window !== 'undefined' && !window.electronAPI

  const results = await Promise.allSettled([
    fetchHN().then(items => ({ source: 'hackernews', items })),
    fetchDevTo().then(items => ({ source: 'devto', items })),
    fetchGitHubTrending().then(items => ({ source: 'github', items })),
    fetchReddit().then(items => ({ source: 'reddit', items })),
  ])

  const items: RawSourceItem[] = []
  const errors: { source: string; error: string }[] = []

  for (const result of results) {
    if (result.status === 'fulfilled') {
      items.push(...result.value.items)
      if (result.value.items.length === 0) {
        errors.push({ source: result.value.source, error: isCorsEnv ? '浏览器模式不支持跨域请求，请在桌面端应用中使用' : '暂无数据' })
      }
    } else {
      const source = result.reason?.source || 'unknown'
      const msg = result.reason?.message || ''
      // Return real error details instead of generic message
      errors.push({ source, error: msg || '获取失败' })
    }
  }

  if (isCorsEnv && errors.length > 0) {
    errors.push({ source: 'system', error: '当前为浏览器模式，跨域请求被浏览器阻止。请在桌面端应用中使用信息模块，或配置 CORS 代理。' })
  }

  return { items, errors }
}

export function formatRawForPrompt(items: RawSourceItem[]): string {
  return items.map((item, i) =>
    `[${i + 1}] ${item.title} (${item.source})\n   ${item.summary.slice(0, 120)}\n   ${item.tags.length ? '#' + item.tags.join(' #') : ''}`
  ).join('\n\n')
}

export type { RawSourceItem }
