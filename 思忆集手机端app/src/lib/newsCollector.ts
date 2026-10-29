/**
 * 多源资讯抓取 / Multi-source news collector
 *
 * 设计目标（替代原先「必须桌面端在线才能看资讯」的架构）：
 *   手机端自己直连各大新闻源，不依赖桌面后端，断网/未配对也能用。
 *
 * 关键技术点：
 *   1. WebView 里直接 fetch 会被 CORS 拦截（新闻源不返回 Access-Control-Allow-Origin），
 *      所以全部走 `SiyiNative.httpFetch` —— 原生发请求，绕开 CORS。
 *   2. 原生走系统网络栈，用户开启系统级 VPN（如星辰）后自动经 VPN 出口，
 *      无需在 App 内做任何代理配置。
 *   3. 每个源独立 try/catch：单个源失败不影响其它源，结果里带回 errors 供 UI 提示。
 *
 * 支持格式：RSS 2.0 / Atom / JSON Feed。用轻量正则解析，不引第三方库。
 */
import SiyiNative from './native/siyiNative'
import type { ArticleItem } from './mobileArticleStore'

/* ── 源定义 ── */

export type SourceRegion = 'cn' | 'intl' | 'tech'

export interface NewsSource {
  id: string
  name: string
  url: string
  region: SourceRegion
  /** 该源是否需要境外网络（提示用户开 VPN） */
  needsVpn?: boolean
  /** 默认归到哪个板块，用于 InfoView 的 section 过滤 */
  section?: string
}

/**
 * 内置源清单。
 *
 * 选择标准：权威性优先（官方媒体 / 老牌科技媒体 / 主流报纸），
 * 而不是聚合站。RSS 均为官方提供的公开 feed。
 */
export const NEWS_SOURCES: NewsSource[] = [
  /* ── 国内（实测可抓，item 数见注释） ── */
  { id: '36kr',      name: '36氪',      url: 'https://www.36kr.com/feed',            region: 'cn', section: '科技' },   // ~30
  { id: 'ithome',    name: 'IT之家',    url: 'https://www.ithome.com/rss/',          region: 'cn', section: '科技' },   // ~60
  { id: 'ifanr',     name: '爱范儿',    url: 'https://www.ifanr.com/feed',           region: 'cn', section: '科技' },   // ~20
  { id: 'sspai',     name: '少数派',    url: 'https://sspai.com/feed',               region: 'cn', section: '科技' },   // 较少但稳定
  { id: 'solidot',   name: 'Solidot',   url: 'https://www.solidot.org/index.rss',    region: 'cn', section: '技术' },   // ~20
  { id: 'infoq_cn',  name: 'InfoQ中文', url: 'https://www.infoq.cn/feed',            region: 'cn', section: '技术' },   // ~20
  { id: 'oschina',   name: '开源中国',  url: 'https://www.oschina.net/news/rss',     region: 'cn', section: '技术' },   // ~50

  /* ── 国际（需 VPN；实测可抓） ── */
  { id: 'hn',          name: 'Hacker News',     url: 'https://hnrss.org/frontpage',                  region: 'intl', needsVpn: true, section: '技术' }, // ~20
  { id: 'techcrunch',  name: 'TechCrunch',      url: 'https://techcrunch.com/feed/',                 region: 'intl', needsVpn: true, section: '科技' }, // ~20
  { id: 'arstechnica', name: 'Ars Technica',    url: 'https://feeds.arstechnica.com/arstechnica/index', region: 'intl', needsVpn: true, section: '科技' }, // ~20
  { id: 'theverge',    name: 'The Verge',       url: 'https://www.theverge.com/rss/index.xml',       region: 'intl', needsVpn: true, section: '科技' }, // ~10
  { id: 'devto',       name: 'Dev.to',          url: 'https://dev.to/feed',                          region: 'intl', needsVpn: true, section: '技术' }, // ~12
  { id: 'lobsters',    name: 'Lobsters',        url: 'https://lobste.rs/rss',                        region: 'intl', needsVpn: true, section: '技术' }, // ~25
  { id: 'mit_tr',      name: 'MIT Technology Review', url: 'https://www.technologyreview.com/feed/', region: 'intl', needsVpn: true, section: 'AI' }, // ~10
]

/**
 * 已知不可用 / 待观察的源（不进主清单，避免每次刷新都报错噪音）：
 *   - 机器之心 https://www.jiqizhixin.com/rss  → 返回 0 item（2026-10 实测）
 *   - arXiv cs.AI http://export.arxiv.org/rss/cs.AI → 0 item；
 *     https://rss.arxiv.org/rss/cs.AI → 407 需认证，暂不纳入
 *   - 36氪曾经偶发返回反爬 HTML，实测连续 3 次均正常，保留但需监控
 */

/* ── 抓取结果 ── */

export interface CollectFailure {
  source: string
  reason: string
}

export interface CollectOutcome {
  items: ArticleItem[]
  errors: CollectFailure[]
  /** 成功抓取的源数量 */
  okCount: number
}

/* ── XML 解析 ── */


/** 剥掉 CDATA 包裹并去空白。很多国内源的 title/link 都带 CDATA。 */
function stripCdata(s: string): string {
  if (!s) return ''
  return s
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/^\s+|\s+$/g, '')
}

function decodeEntities(s: string): string {
  return s
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, n) => String.fromCharCode(parseInt(n, 10)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCharCode(parseInt(n, 16)))
    .replace(/&nbsp;/g, ' ')
}

function stripTags(s: string): string {
  // 先剥 CDATA，否则下一步的 <[^>]+> 会把整个 CDATA 内容当标签删掉
  // （实测：36氪/Solidot 的 title 全是 CDATA，不先剥就得到空标题）
  const noCdata = s.replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, '$1')
  return decodeEntities(noCdata.replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim()
}

function firstTag(xml: string, tag: string): string {
  const re = new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)<\\/${tag}>`, 'i')
  const m = re.exec(xml)
  return m ? m[1] : ''
}

function attrOf(xml: string, tag: string, attr: string): string {
  const re = new RegExp(`<${tag}[^>]*\\s${attr}=["']([^"']+)["'][^>]*>`, 'i')
  const m = re.exec(xml)
  return m ? m[1] : ''
}

function parseDate(raw: string): number {
  if (!raw) return 0
  const t = Date.parse(raw.trim())
  return isNaN(t) ? 0 : t
}

/** 解析 RSS 2.0 的 <item> 或 Atom 的 <entry> */
function parseFeed(xml: string, src: NewsSource): ArticleItem[] {
  const out: ArticleItem[] = []
  // Atom 用 <entry>，RSS 用 <item>
  const blocks = xml.match(/<(item|entry)(?:\s[^>]*)?>[\s\S]*?<\/\1>/gi) ?? []

  for (const block of blocks.slice(0, 30)) {
    // 标题
    let title = stripTags(firstTag(block, 'title'))

    /* 链接解析 —— 顺序很重要。
     *
     * 实测教训（2026-10-29）：36氪 / Solidot / 开源中国 的 <link> 内容是
     * `<![CDATA[https://...]]>`，若先判断 /^https?:/ 再解码，会全部判为无效，
     * 导致「抓到了条目但一条也解析不出来」。必须**先剥 CDATA 再判协议**。 */
    let url = ''
    const rawLink = firstTag(block, 'link')
    const cleanLink = stripCdata(rawLink).trim()
    if (/^https?:/i.test(cleanLink)) {
      url = cleanLink
    } else {
      // Atom: <link href="..."/>
      url = stripCdata(attrOf(block, 'link', 'href')).trim()
    }
    if (!url) {
      const guid = stripCdata(firstTag(block, 'guid')).trim()
      if (/^https?:/i.test(guid)) url = guid
    }

    if (!title || !url) continue

    // 摘要
    const descRaw =
      firstTag(block, 'description') ||
      firstTag(block, 'summary') ||
      firstTag(block, 'content')
    let summary = stripTags(descRaw).slice(0, 300)

    // Atom 的 summary 可能为空，用 content 兜底
    if (!summary) {
      summary = stripTags(firstTag(block, 'content')).slice(0, 300)
    }

    const dateRaw =
      firstTag(block, 'pubDate') ||
      firstTag(block, 'published') ||
      firstTag(block, 'updated') ||
      firstTag(block, 'dc:date')

    const id = firstTag(block, 'guid') || firstTag(block, 'id') || url

    out.push({
      id: `${src.id}:${id.slice(0, 120)}`,
      title: title.slice(0, 200),
      url,
      source: src.name,
      summary,
      tags: [src.section ?? '资讯'],
      score: 0,
      publishedAt: parseDate(decodeEntities(dateRaw)),
      savedAt: Date.now(),
      category: src.section ?? 'general',
    })
  }

  return out
}

/** JSON Feed 兜底（部分源返回 .json） */
function parseJsonFeed(text: string, src: NewsSource): ArticleItem[] {
  try {
    const d = JSON.parse(text) as { items?: Array<Record<string, unknown>> }
    if (!Array.isArray(d.items)) return []
    return d.items.slice(0, 30).map((it) => {
      const url = String(it.url ?? it.external_url ?? it.id ?? '')
      return {
        id: `${src.id}:${String(it.id ?? url).slice(0, 120)}`,
        title: String(it.title ?? '').slice(0, 200),
        url,
        source: src.name,
        summary: String(it.summary ?? it.content_text ?? '').slice(0, 300),
        tags: [src.section ?? '资讯'],
        score: 0,
        publishedAt: parseDate(String(it.date_published ?? it.date_modified ?? '')),
        savedAt: Date.now(),
        category: src.section ?? 'general',
      } as ArticleItem
    }).filter(x => x.title && x.url)
  } catch {
    return []
  }
}

/* ── 单源抓取 ── */

export async function fetchSource(src: NewsSource): Promise<{ items: ArticleItem[]; error?: string }> {
  try {
    const r = await SiyiNative.httpFetch({
      url: src.url,
      // 8 秒。原来 15 秒时，VPN 未开的情况下 7 个境外源全超时，
      // 14 源 3 批 × 15s = 45s 白等。RSS feed 首字节通常 1-3s 内返回。
      timeout: 8000,
      headers: { Accept: 'application/rss+xml, application/atom+xml, application/xml, application/json, */*' },
    })

    if (!r.ok) {
      const hint = r.status === 403 ? '被站点拒绝（反爬）'
        : r.status === 0 ? '网络不可达（境外源可能需开 VPN）'
        : `HTTP ${r.status}`
      return { items: [], error: hint }
    }

    const body = r.body || ''
    const trimmed = body.trimStart()

    let items: ArticleItem[] = []
    if (trimmed.startsWith('{')) {
      items = parseJsonFeed(body, src)
    } else {
      items = parseFeed(body, src)
    }

    if (items.length === 0) {
      return { items: [], error: '未解析出条目（源格式可能变了）' }
    }
    return { items }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { items: [], error: msg.includes('timeout') ? '请求超时' : msg }
  }
}

/* ── 并发抓取全部 ── */

export interface CollectOptions {
  /** 只抓这些源 id；不传则抓全部 */
  sourceIds?: string[]
  /** 只抓这些区域 */
  regions?: SourceRegion[]
  /** 并发上限 */
  concurrency?: number
}

export async function collectNews(opts: CollectOptions = {}): Promise<CollectOutcome> {
  let list = NEWS_SOURCES
  if (opts.sourceIds?.length) {
    const set = new Set(opts.sourceIds)
    list = list.filter(s => set.has(s.id))
  }
  if (opts.regions?.length) {
    const set = new Set(opts.regions)
    list = list.filter(s => set.has(s.region))
  }

  // 并发 6：14 个源分 3 批完成。再高会让部分新闻站触发限流（36氪曾返回反爬页）。
  const limit = Math.max(1, opts.concurrency ?? 6)
  const items: ArticleItem[] = []
  const errors: CollectFailure[] = []
  let okCount = 0

  // 分批并发，避免同时打开过多连接
  for (let i = 0; i < list.length; i += limit) {
    const batch = list.slice(i, i + limit)
    const results = await Promise.all(batch.map(async s => ({ src: s, res: await fetchSource(s) })))
    for (const { src, res } of results) {
      if (res.error) {
        errors.push({ source: src.name, reason: res.error })
      } else {
        okCount++
        items.push(...res.items)
      }
    }
  }

  // 去重（按 url）+ 按时间倒序
  const seen = new Set<string>()
  const deduped: ArticleItem[] = []
  for (const it of items) {
    const key = it.url.replace(/[?#].*$/, '')
    if (seen.has(key)) continue
    seen.add(key)
    deduped.push(it)
  }
  deduped.sort((a, b) => (b.publishedAt || 0) - (a.publishedAt || 0))

  return { items: deduped, errors, okCount }
}

/** 源清单按区域分组，供 UI 展示与勾选 */
export function groupSources(): Record<SourceRegion, NewsSource[]> {
  const g: Record<SourceRegion, NewsSource[]> = { cn: [], intl: [], tech: [] }
  for (const s of NEWS_SOURCES) g[s.region].push(s)
  return g
}
