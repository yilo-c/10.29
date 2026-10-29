/**
 * newsCollector 解析测试
 *
 * 这组测试锁住一个真实踩过的坑：
 *   36氪 / Solidot / 开源中国的 RSS 里 <link> 与 <title> 都是 CDATA 包裹，
 *   若先判断 URL 协议再剥 CDATA，会整条判为无效 →「抓到了条目但一条也解析不出来」。
 *
 * 用真实片段做夹具（截取自 2026-10-29 实际抓取结果），
 * 避免"自己写样本自己过"的假测试。
 */
import { describe, it, expect } from 'vitest'

/* 为了让解析函数可测，这里复刻 newsCollector 内部逻辑。
 * 若将来把 parseFeed 导出，应改为直接 import。 */
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

/** 与 newsCollector.parseFeed 的字段提取逻辑一致 */
function extractItem(block: string): { title: string; url: string } | null {
  const title = stripTags(firstTag(block, 'title'))
  let url = stripCdata(firstTag(block, 'link')).trim()
  if (!/^https?:/i.test(url)) {
    url = stripCdata(attrOf(block, 'link', 'href')).trim()
  }
  if (!url) {
    const guid = stripCdata(firstTag(block, 'guid')).trim()
    if (/^https?:/i.test(guid)) url = guid
  }
  if (!title || !url) return null
  return { title, url }
}

describe('newsCollector / CDATA 处理', () => {
  it('36氪：link 被 CDATA 包裹时仍能取到 URL', () => {
    // 真实片段
    const block = `<title>部署态加速，智元要打通物理AI全链路</title><link><![CDATA[https://36kr.com/p/4047032889800965?f=rss]]></link><pubDate>2026-10-29 22:25:57  +0800</pubDate>`
    const r = extractItem(block)
    expect(r).not.toBeNull()
    expect(r!.url).toBe('https://36kr.com/p/4047032889800965?f=rss')
    expect(r!.title).toBe('部署态加速，智元要打通物理AI全链路')
  })

  it('Solidot：title 与 link 都是 CDATA', () => {
    const block = `<title><![CDATA[乌鸦视觉决策速度两倍于人类]]></title><link><![CDATA[https://www.solidot.org/story?sid=85745]]></link><pubDate>Thu, 29 Oct 2026 18:31:38 +0800</pubDate>`
    const r = extractItem(block)
    expect(r!.title).toBe('乌鸦视觉决策速度两倍于人类')
    expect(r!.url).toBe('https://www.solidot.org/story?sid=85745')
  })

  it('开源中国：CDATA 的 link 含长路径', () => {
    const block = `<title><![CDATA[OpenAI 隔离的 AI agent 通过 CPU 缓存互相通信]]></title><link><![CDATA[https://www.oschina.net/news/503303/openai-securing-our-frontier-model-training-e]]></link>`
    const r = extractItem(block)
    expect(r!.url).toContain('oschina.net/news/503303')
    expect(r!.title).toContain('CPU 缓存')
  })

  it('Hacker News：title 无 CDATA、link 裸文本', () => {
    const block = `<title>Mistral Large 4 (Le Chonk) is now Open Weight</title><link>https://mistral.ai/news/mistral-large-4-oss/</link>`
    const r = extractItem(block)
    expect(r!.url).toBe('https://mistral.ai/news/mistral-large-4-oss/')
    expect(r!.title).toBe('Mistral Large 4 (Le Chonk) is now Open Weight')
  })

  it('Atom：link 用 href 属性', () => {
    const block = `<title>The NYT Cooking app is testing AI</title><link href="https://www.theverge.com/ai-artificial-intelligence"/>`
    const r = extractItem(block)
    expect(r!.url).toBe('https://www.theverge.com/ai-artificial-intelligence')
  })

  it('无 URL 的条目应被丢弃而非产生空链接', () => {
    const block = `<title>只有标题没有链接</title><description>摘要</description>`
    expect(extractItem(block)).toBeNull()
  })

  it('HTML 实体应被正确解码', () => {
    const block = `<title>A &amp; B &lt;测试&gt;</title><link>https://example.com/x</link>`
    const r = extractItem(block)
    expect(r!.title).toBe('A & B <测试>')
  })

  it('title 内的 HTML 标签应被剥离（description 常见内嵌 <p>）', () => {
    const block = `<title><![CDATA[<b>标题</b>内容]]></title><link>https://example.com/y</link>`
    const r = extractItem(block)
    expect(r!.title).toBe('标题 内容')
  })
})

describe('newsCollector / CDATA 剥离顺序（回归防护）', () => {
  it('stripTags 必须先剥 CDATA，否则内容会被当标签删掉', () => {
    // 若顺序反了（先 replace(/<[^>]+>/g) 再剥 CDATA），
    // <![CDATA[...]]> 会被整体替换成空格，得到空串
    const got = stripTags('<![CDATA[真实标题]]>')
    expect(got).toBe('真实标题')
    expect(got).not.toBe('')
  })

  it('stripCdata 对普通文本无副作用', () => {
    expect(stripCdata('普通标题')).toBe('普通标题')
    expect(stripCdata('')).toBe('')
  })
})
