/**
 * mobileInfoCollector — 资讯采集薄封装。
 *
 * 桌面端有完整的 InfoCollectorService（HN/Dev.to/GitHub/Reddit/V2EX 并发抓取 + AI 简报），
 * 手机端不重复实现，全部走 `info.brief` RPC。若桌面端不在线，则返回空集 + 错误码，
 * 让 InfoView 继续展示本地缓存（mobileArticleStore.loadArticles()）。
 *
 * `fetchPageTitle` 用 `info.fetch_proxy`（绕过 CORS）拉取 HTML 后用最朴素的正则抽 title/
 * og:description；解析失败也不抛，给一个最低限可读的回退值。
 */
import { infoBrief, infoFetchProxy } from './bridges/learningBridge'
import type { ArticleItem } from './mobileArticleStore'

export interface CollectResult {
  items: ArticleItem[]
  errors: Array<{ source?: string; reason?: string }>
}

function normalize(raw: any): ArticleItem | null {
  if (!raw || typeof raw !== 'object') return null
  const url = raw.url || raw.link
  const title = raw.title || raw.name
  if (!url || !title) return null
  return {
    id: String(raw.id || raw.uuid || url),
    title: String(title),
    url: String(url),
    source: String(raw.source || raw.provider || ''),
    summary: raw.summary || raw.description || '',
    tags: Array.isArray(raw.tags) ? raw.tags.map(String) : [],
    score: typeof raw.score === 'number' ? raw.score : 0,
    publishedAt: typeof raw.publishedAt === 'number'
      ? raw.publishedAt
      : typeof raw.published_at === 'number'
        ? raw.published_at
        : 0,
    savedAt: Date.now(),
    category: raw.category || 'general',
  }
}

export async function collectAll(topics?: string[]): Promise<CollectResult> {
  try {
    const raw = await infoBrief(topics)
    const items: ArticleItem[] = []
    for (const r of raw) {
      const n = normalize(r)
      if (n) items.push(n)
    }
    return { items, errors: [] }
  } catch (e) {
    return { items: [], errors: [{ reason: String(e) }] }
  }
}

const TITLE_RE = /<title[^>]*>([\s\S]*?)<\/title>/i
const META_DESC_RE = /<meta[^>]+(?:name|property)\s*=\s*["'](?:og:)?description["'][^>]*content\s*=\s*["']([^"']+)["']/i
const META_DESC_RE2 = /<meta[^>]+content\s*=\s*["']([^"']+)["'][^>]+(?:name|property)\s*=\s*["'](?:og:)?description["']/i

export async function fetchPageTitle(url: string): Promise<{ title: string; summary: string }> {
  const fallback = { title: url, summary: '' }
  try {
    const html = await infoFetchProxy(url)
    if (!html) return fallback
    const titleMatch = TITLE_RE.exec(html)
    const title = titleMatch ? decodeHtml(titleMatch[1].trim()).slice(0, 200) : url
    const descMatch = META_DESC_RE.exec(html) || META_DESC_RE2.exec(html)
    const summary = descMatch ? decodeHtml(descMatch[1].trim()).slice(0, 500) : ''
    return { title, summary }
  } catch {
    return fallback
  }
}

function decodeHtml(s: string): string {
  return s
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
}
