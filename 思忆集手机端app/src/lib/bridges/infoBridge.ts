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
    /*
     * 参数名必须与后端 `_info_items.py:55 info_items_list` 严格一致。
     *
     * 此前传的是 section / unread_only / saved_only / query 四个字段，
     * 而后端签名是 source / category / saved / read / search / limit / offset /
     * sort_by / sort_desc，且末尾带 `**kwargs`——
     * 参数名写错时不会被报错，而是直接落进 kwargs 被忽略。
     * 结果：**四个过滤条件全部失效**，界面无论怎么筛都返回同一条数据
     * （恒为未过滤的第一页），用户会以为「筛选坏了/数据没更新」。
     *
     * 映射关系：
     *   section     → category
     *   unread_only → read: false
     *   saved_only  → saved: true
     *   query       → search
     */
    const params: Record<string, unknown> = {
      limit: args.limit,
      offset: args.offset,
    }
    if (args.section != null) params.category = args.section
    if (args.source != null) params.source = args.source
    if (args.unread_only) params.read = false
    if (args.saved_only) params.saved = true
    if (args.query != null) params.search = args.query

    const res = await callRpc<unknown>('info.items.list', params)
    return asArray<InfoItem>(res, 'items', 'list', 'data')
  } catch {
    return []
  }
}

export async function getItem(itemId: string | number): Promise<InfoItem | null> {
  try {
    /*
     * 后端 `info_items_get` 返回的是 `{item: {...}}` 信封，不是条目本身。
     * 原代码直接把整个响应当 InfoItem 用，于是所有字段都是 undefined，
     * 详情页永远是空白——但不会报错，看起来像「数据没加载出来」。
     */
    const res = await callRpc<{ item?: InfoItem | null; error?: string }>('info.items.get', { item_id: itemId })
    return res?.item ?? null
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
    /*
     * 后端返回 `{stats: {...}}` 信封，不是扁平的统计对象。
     * 原代码把整个响应当 InfoStats 用，于是 total/unread 等字段全为 undefined，
     * 首页统计永远显示 0 或空白。
     */
    const res = await callRpc<{ stats?: InfoStats; error?: string }>('info.items.stats', {})
    return res?.stats ?? null
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

/**
 * 让 AI 根据板块名称推荐关键词。
 *
 * 后端签名是 `info_sections_ai_suggest_keywords(name, count)` ——
 * 要的是**板块名称字符串**，不是 section_id。
 * 原代码传 section_id，name 为空 → 后端直接返回
 * `{"success":false,"error":"板块名称不能为空"}`，
 * 于是推荐关键词功能永远是空结果。
 *
 * 故参数改为 sectionName；调用方需要传板块名而不是 id。
 */
export async function aiSuggestKeywords(sectionName?: string): Promise<string[]> {
  try {
    const res = await callRpc<unknown>('info.sections.ai_suggest_keywords', {
      ...(sectionName != null ? { name: sectionName } : {}),
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

/*
 * 后端约定：失败时返回 `{success:false, error}` 或 `{error}`，**不抛异常**。
 * 所以「没抛错」不等于成功，必须检查信封字段，
 * 否则界面会全体假成功（点了没反应但也没报错，最难排查的一类）。
 */
function okEnvelope(res: unknown): boolean {
  if (res && typeof res === 'object') {
    const o = res as { error?: unknown; success?: unknown }
    if (o.error) return false
    if (o.success === false) return false
  }
  return true
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
    /*
     * 后端 `info_sources_add(name, url, parser_type, ...)` 用的是 parser_type，
     * 不是 type。传 type 会被吞，后端回落到默认 rss —— 对 RSS 源恰好能用，
     * 但添加其它类型（json/html）时会静默变成 rss 解析，结果抓不到内容。
     */
    const res = await callRpc<InfoSource & { error?: string }>('info.sources.add', {
      url: source.url,
      ...(source.name != null ? { name: source.name } : {}),
      ...(source.type != null ? { parser_type: source.type } : {}),
    })
    if (!res || res.error) return null
    return res
  } catch {
    return null
  }
}

export async function removeSource(sourceId: string | number): Promise<boolean> {
  try {
    /*
     * 后端 `info_sources_remove(name)` 用 name 标识源，没有 source_id。
     * 传 source_id 会被 kwargs 吞掉 → 后端判「缺少 name 参数」直接失败，
     * 而原代码不看返回值就 return true，界面显示删除成功但列表没变。
     */
    const res = await callRpc<unknown>('info.sources.remove', { name: String(sourceId) })
    return okEnvelope(res)
  } catch {
    return false
  }
}

export async function toggleSource(sourceId: string | number, enabled?: boolean): Promise<boolean> {
  try {
    // 同上：后端用 name 标识源
    const res = await callRpc<unknown>('info.sources.toggle', {
      name: String(sourceId),
      ...(enabled != null ? { enabled } : {}),
    })
    return okEnvelope(res)
  } catch {
    return false
  }
}

export async function resetSources(): Promise<boolean> {
  try {
    const res = await callRpc<unknown>('info.sources.reset', {})
    return okEnvelope(res)
  } catch {
    return false
  }
}

/* ── 抓取与个性化 ── */

/** 触发一次抓取（代理到后端，耗时可能较长）。 */
export async function fetchNow(sourceId?: string | number): Promise<boolean> {
  try {
    /*
     * 后端 `info_fetch_proxy(source, url)` 用 `source` 指定源，没有 source_id。
     * 原代码传 source_id，且它是唯一参数 → 后端收到的 source 为空、
     * url 也为空 → 走「抓全部源」分支。
     * 用户以为在刷新某个源，实际触发的是全量抓取（慢、量大、还可能被限流）。
     */
    const res = await callRpc<unknown>('info.fetch_proxy',
      sourceId != null ? { source: String(sourceId) } : {})
    return okEnvelope(res)
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

/* ── 采集与网页代理（InfoView 直接消费的接口） ── */

export interface InfoFetchResult {
  items?: InfoItem[]
  source?: string
  brief?: string
  errors?: { source: string; error: string }[]
  cached?: boolean
  [key: string]: unknown
}

/**
 * 抓取国内热榜（知乎 / 微博）。
 *
 * 后端真源 `_info_proxy.py:53`：
 *   `info_fetch_proxy(source: str = "", url: str = "", **kwargs)`
 *   source="all" | "zhihu" | "weibo" → `{ items: [...], source }`
 *
 * ⚠️ 注意：这里**没有** cache / brief 模式。缓存与 AI 简报由手机端自己
 * （localStorage + mobileInfoCollector）负责，后端只提供抓取。
 */
export async function fetchItems(source: 'all' | 'zhihu' | 'weibo' = 'all'): Promise<InfoFetchResult | null> {
  try {
    return await callRpc<InfoFetchResult>('info.fetch_proxy', { source })
  } catch {
    return null
  }
}

/**
 * 读取上次抓取的本地缓存。
 *
 * 后端不提供缓存读接口，因此这里**不发起 RPC**，直接从 localStorage 取。
 * InfoView 调用它做首屏秒开，拿不到就返回 null 走实时抓取。
 */
export async function fetchCachedItems(): Promise<InfoFetchResult | null> {
  try {
    const raw = localStorage.getItem('siyi-info-cache')
    if (!raw) return null
    const parsed = JSON.parse(raw) as { items?: InfoItem[]; time?: number }
    if (!parsed?.items || !Array.isArray(parsed.items)) return null
    return { items: parsed.items, cached: true }
  } catch {
    return null
  }
}

/** 写入本地缓存（InfoView 抓取成功后调用，便于下次秒开）。 */
export function saveCachedItems(items: InfoItem[]): void {
  try {
    localStorage.setItem('siyi-info-cache', JSON.stringify({ items, time: Date.now() }))
  } catch {
    /* 存储满了就放弃缓存，不影响主流程 */
  }
}

/**
 * AI 简报：后端**没有**专门的简报 RPC。
 *
 * 简报由手机端 `mobileInfoCollector` + 云 AI 生成（见 InfoView 的 generateBrief）。
 * 这里保留函数签名只为兼容既有调用，实际返回 null，让 InfoView 走本地生成分支。
 */
export async function fetchBrief(): Promise<{ brief?: string } | null> {
  const raw = localStorage.getItem('siyi-brief')
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as { text?: string; time?: number }
    if (parsed?.text) return { brief: parsed.text }
  } catch {
    /* ignore */
  }
  return null
}

/**
 * 通过后端代理取网页 HTML —— 绕过手机 WebView 的 CORS 限制。
 *
 * 后端 `_info_proxy.py:62` 明确说明「手机端 fetchPageTitle 依赖此模式」：
 *   `{ url }` → `{ content, url, status }`
 * 注意返回的是 **HTML 原文**，标题需要在此解析后再交给调用方，
 * 这样 InfoView 不用关心解析细节。
 */
export async function fetchPageTitle(url: string): Promise<{ title?: string; summary?: string } | null> {
  try {
    const res = await callRpc<{ content?: string; url?: string; status?: number }>(
      'info.fetch_proxy',
      { url },
    )
    const html = res?.content
    if (!html) return null
    return extractTitleAndSummary(html)
  } catch {
    return null
  }
}

/** 从 HTML 里提取 <title> 与 meta description。 */
function extractTitleAndSummary(html: string): { title?: string; summary?: string } {
  let title: string | undefined
  let summary: string | undefined

  const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)
  if (titleMatch) {
    title = decodeEntities(titleMatch[1]).trim().slice(0, 200)
  } else {
    const ogTitle = html.match(/<meta[^>]+property=["']og:title["'][^>]+content=["']([^"']*)["']/i)
    if (ogTitle) title = decodeEntities(ogTitle[1]).trim().slice(0, 200)
  }

  const descMatch =
    html.match(/<meta[^>]+name=["']description["'][^>]+content=["']([^"']*)["']/i)
    || html.match(/<meta[^>]+property=["']og:description["'][^>]+content=["']([^"']*)["']/i)
  if (descMatch) summary = decodeEntities(descMatch[1]).trim().slice(0, 500)

  return { title, summary }
}

/** 常见 HTML 实体的最小解码（够用于标题/摘要，不引第三方库）。 */
function decodeEntities(s: string): string {
  return s
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
}

/**
 * 读取资讯板块的关键词配置（板块名 → 关键词数组）。
 *
 * 后端 `info.sections.list` 返回的是**嵌套结构**，不是数组：
 *   `{ default: {meta, order, keywords}, user: [...], effective: {...}, all: [...] }`
 * 关键词真源在 `default.keywords`（内置板块）与 `user[].keywords`（用户自定义）。
 */
export async function getSectionKeywords(): Promise<Record<string, string[]>> {
  try {
    const res = await callRpc<{
      default?: { keywords?: Record<string, string[]> }
      user?: Array<{ name?: string; keywords?: string[] }>
    }>('info.sections.list', {})

    const out: Record<string, string[]> = {}

    // 内置板块：default.keywords 就是 {板块名: [关键词]}
    const defaults = res?.default?.keywords
    if (defaults && typeof defaults === 'object') {
      for (const [name, kw] of Object.entries(defaults)) {
        if (Array.isArray(kw)) {
          out[name] = kw.filter((x): x is string => typeof x === 'string')
        }
      }
    }

    // 用户自定义板块：覆盖同名内置板块
    if (Array.isArray(res?.user)) {
      for (const s of res.user) {
        if (!s || typeof s.name !== 'string') continue
        const kw = Array.isArray(s.keywords)
          ? s.keywords.filter((x): x is string => typeof x === 'string')
          : []
        out[s.name] = kw
      }
    }

    return out
  } catch {
    return {}
  }
}

/**
 * 保存资讯板块关键词配置。
 *
 * 后端 `info.sections.update(section_id, name?, emoji?, keywords?, enabled?, sources?)`
 * 需要 section_id，而调用方传的是「板块名 → 关键词」映射，因此先查 list 拿 id 再逐个 update。
 * 内置板块的 id 即板块名（见 `_SECTION_ORDER`），用户板块取 `user[].id`。
 */
export async function setSectionKeywords(keywords: Record<string, string[]>): Promise<boolean> {
  try {
    const res = await callRpc<{
      default?: { order?: string[] }
      user?: Array<{ id?: string | number; name?: string }>
    }>('info.sections.list', {})

    const byName = new Map<string, string>()
    // 内置板块：id 直接用板块名
    for (const name of res?.default?.order ?? []) {
      if (typeof name === 'string') byName.set(name, name)
    }
    // 用户自定义板块：用真实 id（字符串化，后端签名是 section_id: str）
    for (const s of res?.user ?? []) {
      if (s && typeof s.name === 'string' && s.id != null) {
        byName.set(s.name, String(s.id))
      }
    }

    const jobs: Promise<unknown>[] = []
    for (const [name, kw] of Object.entries(keywords)) {
      const id = byName.get(name)
      if (!id) continue
      jobs.push(callRpc('info.sections.update', { section_id: id, keywords: kw }))
    }
    if (jobs.length === 0) return false
    await Promise.all(jobs)
    return true
  } catch {
    return false
  }
}
