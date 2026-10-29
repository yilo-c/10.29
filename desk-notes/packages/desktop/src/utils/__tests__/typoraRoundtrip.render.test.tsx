import { describe, expect, it, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
// @ts-expect-error 测试环境 node types 未包含 fs 命名空间
import { writeFileSync, mkdirSync } from 'node:fs'

// 相对路径资产经 notes.asset.get RPC 取图——测试环境 mock 后端响应（1px PNG data URL）
vi.mock('../../utils/siyiFileResolver', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../utils/siyiFileResolver')>()
  return {
    ...actual,
    resolveRelativeAsset: async () => 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  }
})

import MdPreview from '../../components/FloatingNote/MdPreview'

/**
 * U0 战役 · Typora 往返对账（渲染侧）：以一份覆盖 Typora 全特性的语料过**真实渲染管线**
 * （MdPreview = remark-gfm/math/breaks + rehype raw→sanitize→katex），逐特征断言
 * 「Typora 能渲染的，这里渲染成什么」。产出 JSON 报告供方案拍板（渲染对账 + 真库扫描两条腿之一）。
 *
 * 判定语义：✅ 渲染为对应 DOM / ⚠️ 降级可见（原样文本，不丢内容）/ ❌ 内容消失。
 */

type FeatState = 'ok' | 'degraded' | 'lost'

const results: Array<{ feature: string; sample: string; state: FeatState; evidence: string }> = []

function renderAndRecord(feature: string, sample: string, check: (h: string, c: HTMLElement) => 'ok' | 'degraded' | 'lost', evidenceMap: Partial<Record<FeatState, string>>) {
  const { container, unmount } = render(<MdPreview content={sample} />)
  const state = check(container.innerHTML, container)
  results.push({
    feature,
    sample: sample.slice(0, 80),
    state,
    evidence: evidenceMap[state] || (state === 'ok' ? '渲染符合预期' : '见 check 函数'),
  })
  unmount()
}


describe('Typora 往返对账 · 渲染侧', () => {
  it('收集全特征对账结果', async () => {
    // ── 基础语法（GFM 标准面）──
    renderAndRecord('标题 h1-h3', '# 一级\n## 二级\n### 三级', (h) => (h.includes('<h1') && h.includes('<h2') && h.includes('<h3') ? 'ok' : 'lost'), { lost: '标题缺失' })
    renderAndRecord('加粗/斜体/删除线', '**粗** *斜* ~~删~~', (h) => (h.includes('<strong') && h.includes('<em') && h.includes('<del') ? 'ok' : 'lost'), { lost: '基础样式缺失' })
    renderAndRecord('有序/无序/嵌套列表', '- a\n- b\n  - b1\n1. one\n2. two', (h) => (h.includes('<ul') && h.includes('<ol') && h.includes('<li') ? 'ok' : 'lost'), { lost: '列表缺失' })
    renderAndRecord('任务列表', '- [x] 已完成\n- [ ] 未完成', (h) => (h.includes('checkbox') ? 'ok' : 'lost'), { lost: '任务列表缺失' })
    renderAndRecord('GFM 表格', '| A | B |\n|---|---|\n| 1 | 2 |', (h) => (h.includes('<table') ? 'ok' : 'lost'), { lost: '表格缺失' })
    renderAndRecord('代码围栏+语言高亮', '```js\nconst x = 1\n```', (_h, c) => (c.textContent?.includes('const x = 1') ? 'ok' : 'lost'), { lost: '代码块内容丢失' })
    renderAndRecord('行内代码', '行内 `code` 字', (h) => (h.includes('<code') ? 'ok' : 'lost'), { lost: '行内码缺失' })
    renderAndRecord('引用块', '> 引用一行', (h) => (h.includes('<blockquote') ? 'ok' : 'lost'), { lost: '引用缺失' })
    renderAndRecord('链接与分隔线', '[百度](https://baidu.com)\n\n---', (h) => (h.includes('<a') && h.includes('<hr') ? 'ok' : 'lost'), { lost: '链接或分隔线缺失' })

    // ── Typora 扩展（本批已补）──
    renderAndRecord('行内公式 $..$', '质能 $E=mc^2$ 方程', (h) => (h.includes('katex') ? 'ok' : 'lost'), { lost: '公式未渲染' })
    renderAndRecord('块级公式 $$..$$', '$$\n\\frac{1}{2}\n$$', (h) => (h.includes('katex-display') || h.includes('katex') ? 'ok' : 'lost'), { lost: '块公式未渲染' })
    renderAndRecord('原样 HTML 行内（u/font/sub/sup/kbd/mark）', '<u>下划线</u> <font color="red">红</font> H<sub>2</sub> x<sup>2</sup> <kbd>Ctrl</kbd> <mark>亮</mark>', (h) => {
      const tags = ['<u', '<font', '<sub', '<sup', '<kbd', '<mark']
      const hit = tags.filter((t) => h.includes(t)).length
      return hit === tags.length ? 'ok' : hit > 0 ? 'degraded' : 'lost'
    }, { ok: '全部放行', degraded: '部分标签被 sanitize 剥除（内容仍在，样式丢失）' })
    renderAndRecord('HTML 块级 details/center', '<details><summary>展开</summary>\n\n- 内部列表\n\n</details>', (h) => (h.includes('<details') && h.includes('内部列表') ? 'ok' : h.includes('<details') ? 'degraded' : 'lost'), { ok: 'details+内部内容', degraded: 'details 在但内部列表被吞' })
    renderAndRecord('Typora 软换行（单换行）', '第一行\n第二行', (h) => (h.includes('<br') ? 'ok' : 'lost'), { lost: '换行被折叠' })

    // ── 已知缺口（登记，不判失败）──
    renderAndRecord('Mermaid 图表', '```mermaid\ngraph TD; A-->B;\n```', (h) => (h.includes('mermaid') ? 'ok' : h.includes('language-mermaid') || h.includes('graph TD') ? 'degraded' : 'lost'), { ok: '已渲染', degraded: '降级为代码块（已知缺口）' })
    renderAndRecord('[TOC] 目录标记', '[TOC]\n\n# 章\n', (h) => (h.includes('<nav') || h.includes('<ul') && h.includes('章') ? 'ok' : h.includes('[TOC]') ? 'degraded' : 'lost'), { ok: '已渲染目录', degraded: '原样显示（已知缺口）' })
    renderAndRecord('YAML front matter', '---\ntitle: 笔记\n---\n\n正文', (h) => (h.includes('title: 笔记') ? 'degraded' : 'ok'), { degraded: '元数据按正文显示（已知缺口）' })
    renderAndRecord('==高亮== 标记', '这是 ==高亮文字== 语法', (h) => (h.includes('<mark>高亮文字</mark>') ? 'ok' : h.includes('==高亮文字==') ? 'degraded' : 'lost'), { ok: 'mark 渲染', degraded: '原样 ==（已知缺口： remark-gfm 无此扩展）' })
    renderAndRecord('脚注 [^1]', '正文[^1]\n\n[^1]: 注', (h) => (h.includes('footnote') || h.includes('data-footnotes') ? 'ok' : h.includes('[^1]') ? 'degraded' : 'lost'), { ok: '已渲染', degraded: '原样显示（已知缺口： remark-footnotes@5 空壳包）' })

    // ── 图片四形态（方案拍板的核心证据）──
    // 相对路径资产走异步 RPC，需 waitFor 数据落地（方案 A 实证）
    {
      const { container, unmount } = render(<MdPreview content="![图](assets/pic.png)" />)
      await waitFor(() => expect(container.querySelector('img')).not.toBeNull())
      const ok = container.innerHTML.includes('<img src="data:image/png')
      results.push({ feature: '图片 · 相对路径（Typora 主形态，方案 A）', sample: '![图](assets/pic.png)', state: ok ? 'ok' : 'degraded', evidence: ok ? '相对路径经 notes.asset.get RPC 渲染（方案 A 生效）' : 'img 在但未走资产解析' })
      unmount()
    }
    renderAndRecord('图片 · siyi-file 协议', '![图](siyi-file://note-attachments/a.png)', (h) => (h.includes('<img') ? 'ok' : 'lost'), { ok: 'img 产出（经 transfer.download RPC）' })
    renderAndRecord('图片 · http(s)', '![图](https://example.com/a.png)', (h) => (h.includes('<img') ? 'ok' : 'lost'), { ok: 'img 产出' })
    renderAndRecord('图片 · data URI', '![图](data:image/png;base64,iVBOR)', (h) => (h.includes('<img') ? 'ok' : 'lost'), { ok: 'img 产出' })

    // ── 汇总报告（仓外）──
    const summary = {
      generatedAt: new Date().toISOString(),
      pipeline: 'MdPreview (remark-gfm/math/breaks + rehype raw→sanitize→katex)',
      total: results.length,
      ok: results.filter((r) => r.state === 'ok').length,
      degraded: results.filter((r) => r.state === 'degraded').length,
      lost: results.filter((r) => r.state === 'lost').length,
      details: results,
    }
    try {
      mkdirSync('D:/h/3.25/typora-audit', { recursive: true })
      writeFileSync('D:/h/3.25/typora-audit/render-report.json', JSON.stringify(summary, null, 1), 'utf-8')
    } catch { /* 报告写仓外失败不阻塞测试 */ }

    // 硬断言只锁「绝不丢内容」底线：基础语法 + 已补能力必须 ok
    expect(summary.lost, `内容消失项: ${JSON.stringify(results.filter((r) => r.state === 'lost'), null, 1)}`).toBe(0)
    // 已知缺口必须是 degraded（可见降级）而非 lost
  })
})
