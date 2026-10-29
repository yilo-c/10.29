/**
 * 信息流桥 — 封装桌面后端 `info.*` RPC。
 *
 * 后端真源：`思忆集app/backend/handlers/_info_items.py`、
 * `_info_sources.py`、`_info_proxy.py`。
 *
 * 设计约定：
 *  - 所有列表类接口统一返回数组（后端可能返回 `{items: []}` 或裸数组，
 *    这里都归一化，让 UI 层不用关心后端信封差异）。
 *  - 单个接口失败不应让整个信息流页面空白，因此读接口失败返回空集合，
 *    写接口失败返回 `false` / `null` 由调用方决定提示。
 */
import { callRpc } from '../rpcClient'

/* ── 类型 ── */

export interface InfoItem {
  id: string | number
  title?: string
  summary?: string
  content?: string
  url?: string
  source?: string
  source_id?: string | number
  section?: string
  published_at?: string | number
  fetched_at?: string | number
  is_read?: boolean
  is_saved?: boolean
  [key: string]: unknown
}

export interface InfoSource {
  id: string | number
  name?: string
  url?: string
  type?: string
  enabled?: boolean
  item_count?: number
  [key: string]: unknown
}

export interface InfoSection {
  id: string | number
  name?: string
  keywords?: string[]
  [key: string]: unknown
}

export interface InfoStats {
  total?: number
  unread?: number
  saved?: number
  sources?: number
  [key: string]: unknown
}

export interface ListItemsArgs {
  section?: string
  source?: string
  unread_only?: boolean
  saved_only?: boolean
  limit?: number
  offset?: number
  query?: string
}

/* ── 内部工具 ── */

/** 后端可能返回裸数组，也可能返回 {items: []} / {list: []}，统一成数组。 */
function asArray<T>(res: unknown, ...keys: string[]): T[] {
  if (Array.isArray(res)) return res as T[]
  if (res && typeof res === 'object') {
    for (const k of keys) {
      const v = (res as Record<string, unknown>)[k]
      if (Array.isArray(v)) return v as T[]
    }
    // 兜底：找对象里第一个数组字段
    for (const v of Object.values(res as Record<string, unknown>)) {
      if (Array.isArray(v)) return v as T[]
    }
  }
  return []
}

/* ── 条目 ── */

export async function listItems(args: ListItemsArgs = {}): Promise<InfoItem[]> {
  try {
    const res = await callRpc<unknown>('info.items.list', {
      section: args.section,
      source: args.source,
      unread_only: args.unread_only,
      saved_only: args.saved_only,
      limit: args.limit,
      offset: args.offset,
      query: args.query,
    })
    return asArray<InfoItem>(res, 'items', 'list', 'data')
  } catch {
    return []
  }
}

export async function getItem(itemId: string | number): Promise<InfoItem | null> {
  try {
    const res = await callRpc<InfoItem>('info.items.get', { item_id: itemId })
    return res ?? null
  } catch {
    return null
  }
}

export async function updateItem(
  itemId: string | number,
  patch: Partial<Pick<InfoItem, 'title' | 'summary' | 'content' | 'section'>>,
): Promise<boolean> {
  try {
    await callRpc('info.items.update', { item_id: itemId, ...patch })
    return true
  } catch {
    return false
  }
}

export async function toggleRead(itemId: string | number, read?: boolean): Promise<boolean> {
  try {
    await callRpc('info.items.toggle_read', { item_id: itemId, ...(read != null ? { read } : {}) })
    return true
  } catch {
    return false
  }
}

export async function toggleSaved(itemId: string | number, saved?: boolean): Promise<boolean> {
  try {
    await callRpc('info.items.toggle_saved', { item_id: itemId, ...(saved != null ? { saved } : {}) })
    return true
  } catch {
    return false
  }
}

/** 把信息条目转存为个人笔记，返回新笔记 id（失败返回 null）。 */
export async function saveItemToNote(
  itemId: string | number,
  options: { noteId?: string; append?: boolean } = {},
): Promise<{ note_id?: string } | null> {
  try {
    return await callRpc<{ note_id?: string }>('info.items.save_to_note', {
      item_id: itemId,
      note_id: options.noteId,
      append: options.append,
    })
  } catch {
    return null
  }
}

export async function itemSources(): Promise<Array<{ source?: string; count?: number }>> {
  try {
    const res = await callRpc<unknown>('info.items.sources', {})
    return asArray(res, 'sources', 'items')
  } catch {
    return []
  }
}

export async function stats(): Promise<InfoStats | null> {
  try {
    return await callRpc<InfoStats>('info.items.stats', {})
  } catch {
    return null
  }
}

/* ── 分区 ── */

export async function listSections(): Promise<InfoSection[]> {
  try {
    const res = await callRpc<unknown>('info.sections.list', {})
    return asArray<InfoSection>(res, 'sections', 'items')
  } catch {
    return []
  }
}

export async function createSection(name: string, keywords: string[] = []): Promise<InfoSection | null> {
  try {
    return await callRpc<InfoSection>('info.sections.create', { name, keywords })
  } catch {
    return null
  }
}

export async function updateSection(
  sectionId: string | number,
  patch: { name?: string; keywords?: string[] },
): Promise<boolean> {
  try {
    await callRpc('info.sections.update', { section_id: sectionId, ...patch })
    return true
  } catch {
    return false
  }
}

export async function deleteSection(sectionId: string | number): Promise<boolean> {
  try {
    await callRpc('info.sections.delete', { section_id: sectionId })
    return true
  } catch {
    return false
  }
}

/** 让 AI 根据现有内容推荐分区关键词。 */
export async function aiSuggestKeywords(sectionId?: string | number): Promise<string[]> {
  try {
    const res = await callRpc<unknown>('info.sections.ai_suggest_keywords', {
      ...(sectionId != null ? { section_id: sectionId } : {}),
    })
    if (Array.isArray(res)) return res.filter((x): x is string => typeof x === 'string')
    if (res && typeof res === 'object') {
      const v = (res as Record<string, unknown>).keywords
      if (Array.isArray(v)) return v.filter((x): x is string => typeof x === 'string')
    }
    return []
  } catch {
    return []
  }
}

/* ── 信息源 ── */

export async function listSources(): Promise<InfoSource[]> {
  try {
    const res = await callRpc<unknown>('info.sources.list', {})
    return asArray<InfoSource>(res, 'sources', 'items')
  } catch {
    return []
  }
}

export async function addSource(source: {
  url: string
  name?: string
  type?: string
}): Promise<InfoSource | null> {
  try {
    return await callRpc<InfoSource>('info.sources.add', source)
  } catch {
    return null
  }
}

export async function removeSource(sourceId: string | number): Promise<boolean> {
  try {
    await callRpc('info.sources.remove', { source_id: sourceId })
    return true
  } catch {
    return false
  }
}

export async function toggleSource(sourceId: string | number, enabled?: boolean): Promise<boolean> {
  try {
    await callRpc('info.sources.toggle', {
      source_id: sourceId,
      ...(enabled != null ? { enabled } : {}),
    })
    return true
  } catch {
    return false
  }
}

export async function resetSources(): Promise<boolean> {
  try {
    await callRpc('info.sources.reset', {})
    return true
  } catch {
    return false
  }
}

/* ── 抓取与个性化 ── */

/** 触发一次抓取（代理到后端，耗时可能较长）。 */
export async function fetchNow(sourceId?: string | number): Promise<boolean> {
  try {
    await callRpc('info.fetch_proxy', { ...(sourceId != null ? { source_id: sourceId } : {}) })
    return true
  } catch {
    return false
  }
}

export async function personalize(options: Record<string, unknown> = {}): Promise<boolean> {
  try {
    await callRpc('info.personalize', options)
    return true
  } catch {
    return false
  }
}

/* ── 采集与简报（InfoView 直接消费的接口） ── */

export interface InfoFetchResult {
  items?: InfoItem[]
  brief?: string
  errors?: { source: string; error: string }[]
  cached?: boolean
  [key: string]: unknown
}

/**
 * 主动抓取最新条目（后端会并发拉取所有启用的信息源）。
 * InfoView 优先用它，失败后回退到本地采集。
 */
export async function fetchItems(): Promise<InfoFetchResult | null> {
  try {
    return await callRpc<InfoFetchResult>('info.fetch_proxy', { mode: 'items' })
  } catch {
    return null
  }
}

/** 只读缓存，不触发网络抓取 —— 首屏用它快速出内容。 */
export async function fetchCachedItems(): Promise<InfoFetchResult | null> {
  try {
    return await callRpc<InfoFetchResult>('info.fetch_proxy', { mode: 'cache' })
  } catch {
    return null
  }
}

/** 让后端生成今日 AI 简报（耗时较长，只在用户主动触发或后台刷新时调）。 */
export async function fetchBrief(): Promise<{ brief?: string } | null> {
  try {
    return await callRpc<{ brief?: string }>('info.fetch_proxy', { mode: 'brief' })
  } catch {
    return null
  }
}

/**
 * 通过后端代理解析网页标题/摘要 —— 绕过手机 WebView 的 CORS 限制。
 * 失败时 InfoView 会回退到本地 `mobileInfoCollector.fetchPageTitle`。
 */
export async function fetchPageTitle(url: string): Promise<{ title?: string; summary?: string } | null> {
  try {
    return await callRpc<{ title?: string; summary?: string }>('info.fetch_proxy', {
      mode: 'page_title',
      url,
    })
  } catch {
    return null
  }
}

/** 读取资讯板块的关键词配置（板块名 → 关键词数组）。 */
export async function getSectionKeywords(): Promise<Record<string, string[]>> {
  try {
    const res = await callRpc<unknown>('info.sections.list', {})
    const sections = asArray<InfoSection>(res, 'sections', 'items')
    const out: Record<string, string[]> = {}
    for (const s of sections) {
      if (!s || typeof s !== 'object') continue
      const name = typeof s.name === 'string' ? s.name : null
      if (!name) continue
      const kw = Array.isArray(s.keywords)
        ? s.keywords.filter((x): x is string => typeof x === 'string')
        : []
      out[name] = kw
    }
    return out
  } catch {
    return {}
  }
}

/** 保存资讯板块关键词配置。 */
export async function setSectionKeywords(keywords: Record<string, string[]>): Promise<boolean> {
  try {
    await callRpc('info.personalize', { section_keywords: keywords })
    return true
  } catch {
    return false
  }
}
