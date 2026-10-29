import { describe, expect, it, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

import MdPreview from '../MdPreview'
import { useStore } from '../../../store/useStore'

/** U0 战役（2026-09-19）：Typora 迁入内容渲染补齐 + 原样 HTML 受控放行的安全护栏。
 * 插件面：remark-gfm/math/breaks/footnotes + rehype raw→sanitize→katex。 */

const renderMd = (content: string) => render(<MdPreview content={content} />)

beforeEach(() => {
  cleanup()
  useStore.setState({ floatingNotes: [] })
})

describe('Typora 渲染补齐', () => {
  it('行内公式 $E=mc^2$ 渲染 katex', () => {
    const { container } = renderMd('质能方程 $E=mc^2$ 是物理基石')
    expect(container.querySelector('.katex')).not.toBeNull()
  })

  it('块级公式 $$...$$ 渲染 katex-display', () => {
    const { container } = renderMd('$$\n\\int_0^1 x\\,dx = \\frac{1}{2}\n$$')
    expect(container.querySelector('.katex-display')).not.toBeNull()
  })

  it('原样 HTML <u> 受控放行', () => {
    const { container } = renderMd('文字 <u>下划线</u> 结束')
    expect(container.querySelector('u')).not.toBeNull()
  })

  it('单换行渲染 <br>（Typora 软换行习惯）', () => {
    const { container } = renderMd('第一行\n第二行')
    expect(container.querySelector('br')).not.toBeNull()
  })

  it('脚注 [^1] 渲染为引用链接', () => {
    const { container } = renderMd('正文[^1]\n\n[^1]: 注释内容')
    const html = container.innerHTML
    expect(/footnote/i.test(html)).toBe(true)
  })

  it('GFM 表格与任务列表不回归', () => {
    const { container } = renderMd('| A | B |\n|---|---|\n| 1 | 2 |\n\n- [x] 完成')
    expect(container.querySelector('table')).not.toBeNull()
    expect(container.querySelector('input[type="checkbox"]')).not.toBeNull()
  })
})

describe('sanitize 安全护栏（Electron 渲染层）', () => {
  it('<script> 不产出 script 节点', () => {
    const { container } = renderMd('before\n\n<script>window.__xss=1</script>\n\nafter')
    expect(container.querySelector('script')).toBeNull()
    expect((window as unknown as { __xss?: string }).__xss).toBeUndefined()
  })

  it('img onerror 事件属性被剥除', () => {
    const { container } = renderMd('<img src="x" onerror="window.__xss2=1">')
    const img = container.querySelector('img')
    expect(img?.getAttribute('onerror')).toBeNull()
    expect((window as unknown as { __xss2?: string }).__xss2).toBeUndefined()
  })

  it('iframe/object/embed 被剥除', () => {
    const { container } = renderMd('<iframe src="https://evil.example"></iframe><object></object><embed src="x">')
    expect(container.querySelector('iframe')).toBeNull()
    expect(container.querySelector('object')).toBeNull()
    expect(container.querySelector('embed')).toBeNull()
  })

  it('a[href=javascript:] 被拒', () => {
    const { container } = renderMd('<a href="javascript:window.__xss3=1">点我</a>')
    const a = container.querySelector('a')
    const href = a?.getAttribute('href') || ''
    expect(href.toLowerCase().startsWith('javascript:')).toBe(false)
  })

  it('details/summary 放行（Typora 折叠块）', () => {
    const { container } = renderMd('<details><summary>标题</summary>内容</details>')
    expect(container.querySelector('details')).not.toBeNull()
    expect(container.textContent).toContain('内容')
  })
})

describe('既有能力不回归', () => {
  it('代码高亮与 inline code', () => {
    const { container } = renderMd('```js\nconst x = 1\n```\n\n行内 `code`')
    expect(container.textContent).toContain('const x = 1')
    expect(screen.getByText('code')).toBeTruthy()
  })

  it('空内容显示占位', () => {
    const { container } = renderMd('   ')
    expect(container.querySelector('span.text-text-dim.italic')).not.toBeNull()
  })
})
