/**
 * mobileArticleStore — 手机端资讯列表 / 收藏 / 已读 / 离线缓存的本地持久化层。
 *
 * 历史背景：InfoView 与 mobileBookmarkSync 都依赖本模块，但仓库里一直缺失，导致 5 条
 * TS 编译错误。补齐后行为简单：纯 localStorage，键名以 `siyi-info-*` 前缀避免和其它
 * 模块冲突。所有 IO 异常都吞掉返回安全默认值，UI 不会因 storage 抖动崩溃。
 */
import { swallowError } from './swallowError'

export interface ArticleItem {
  id: string
  title: string
  url: string
  source: string
  summary?: string
  tags?: string[]
  score?: number
  publishedAt?: number
  savedAt?: number
  category?: string
}

const ARTICLES_KEY = 'siyi-info-articles'
const BOOKMARKS_KEY = 'siyi-info-bookmarks'
const READ_KEY = 'siyi-info-read'
const CACHE_KEY_PREFIX = 'siyi-info-cache:'

const MAX_ARTICLES = 500
const MAX_READ_ENTRIES = 2000
const MAX_CACHE_BYTES = 2 * 1024 * 1024 // 单条缓存上限 2 MB

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return fallback
    return JSON.parse(raw) as T
  } catch (e) {
    swallowError(`mobileArticleStore:readJson:${key}`, e)
    return fallback
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch (e) {
    swallowError(`mobileArticleStore:writeJson:${key}`, e)
  }
}

// ── 文章列表 ──
export function loadArticles(): ArticleItem[] {
  return readJson<ArticleItem[]>(ARTICLES_KEY, [])
}

export function saveArticles(list: ArticleItem[]): void {
  const trimmed = list.slice(0, MAX_ARTICLES)
  writeJson(ARTICLES_KEY, trimmed)
}

/** 把新批次合并入本地列表（按 url 去重，新数据覆盖旧字段，按 publishedAt/savedAt 倒序）。 */
export function mergeArticles(incoming: ArticleItem[]): ArticleItem[] {
  if (!incoming?.length) return loadArticles()
  const local = loadArticles()
  const byUrl = new Map<string, ArticleItem>()
  for (const it of local) {
    if (it?.url) byUrl.set(it.url, it)
  }
  for (const it of incoming) {
    if (!it?.url) continue
    const old = byUrl.get(it.url)
    byUrl.set(it.url, { ...old, ...it })
  }
  const merged = Array.from(byUrl.values()).sort((a, b) => {
    const ta = a.publishedAt || a.savedAt || 0
    const tb = b.publishedAt || b.savedAt || 0
    return tb - ta
  })
  saveArticles(merged)
  return merged
}

// ── 收藏 ──
export function loadBookmarks(): ArticleItem[] {
  return readJson<ArticleItem[]>(BOOKMARKS_KEY, [])
}

function saveBookmarks(list: ArticleItem[]): void {
  writeJson(BOOKMARKS_KEY, list)
}

export function isBookmarked(url: string): boolean {
  if (!url) return false
  return loadBookmarks().some(b => b.url === url)
}

/** 切换收藏状态，返回切换后是否处于「已收藏」。 */
export function toggleBookmark(item: ArticleItem): boolean {
  if (!item?.url) return false
  const list = loadBookmarks()
  const idx = list.findIndex(b => b.url === item.url)
  if (idx >= 0) {
    list.splice(idx, 1)
    saveBookmarks(list)
    return false
  }
  list.unshift({ ...item, savedAt: item.savedAt || Date.now() })
  saveBookmarks(list)
  return true
}

// ── 已读集合 ──
export function loadReadSet(): Set<string> {
  const arr = readJson<string[]>(READ_KEY, [])
  return new Set(arr)
}

export function markRead(url: string): void {
  if (!url) return
  const set = loadReadSet()
  if (set.has(url)) return
  set.add(url)
  // 防止无界增长
  let arr = Array.from(set)
  if (arr.length > MAX_READ_ENTRIES) arr = arr.slice(arr.length - MAX_READ_ENTRIES)
  writeJson(READ_KEY, arr)
}

// ── 离线正文缓存 ──
interface CachedContent {
  url: string
  title: string
  text: string
  savedAt: number
}

function cacheKey(url: string): string {
  return CACHE_KEY_PREFIX + url
}

export function cacheArticleContent(url: string, title: string, text: string): void {
  if (!url || !text) return
  const payload: CachedContent = { url, title: title || '', text, savedAt: Date.now() }
  try {
    const json = JSON.stringify(payload)
    if (json.length > MAX_CACHE_BYTES) return // 太大直接丢弃
    localStorage.setItem(cacheKey(url), json)
  } catch (e) {
    swallowError('mobileArticleStore:cacheArticleContent', e)
  }
}

export function getCachedContent(url: string): CachedContent | null {
  if (!url) return null
  return readJson<CachedContent | null>(cacheKey(url), null)
}
