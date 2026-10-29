import { describe, expect, it } from 'vitest'
import { hardenSoftBreaks } from '../markdownHardBreaks'

describe('hardenSoftBreaks', () => {
  it('条目内 4 行补硬断行，条目间空行不动', () => {
    const md = [
      '### 🤖 AI',
      '',
      '**标题一** [原文](https://a)',
      '点评：值得关注。',
      '来源：HackerNews',
      '',
      '**标题二** [原文](https://b)',
      '点评：也很值得关注。',
      '来源：掘金',
      '',
    ].join('\n')
    const out = hardenSoftBreaks(md)
    const lines = out.split('\n')
    // 标题/点评行补硬断行；条目末行（下一行是空行）不补
    expect(lines[2]).toBe('**标题一** [原文](https://a)  ')
    expect(lines[3]).toBe('点评：值得关注。  ')
    expect(lines[4]).toBe('来源：HackerNews')
    // 空行原样
    expect(lines[5]).toBe('')
    // 标题行（下一行是空行）不补
    expect(lines[0]).toBe('### 🤖 AI')
  })

  it('围栏代码块内不动', () => {
    const md = ['```python', 'x = 1', 'y = 2', '```', 'done'].join('\n')
    const out = hardenSoftBreaks(md)
    expect(out).toBe(md)
  })

  it('已带硬断行（尾两空格）的行不叠加', () => {
    const md = 'a  \nb'
    expect(hardenSoftBreaks(md)).toBe('a  \nb')
  })

  it('行尾反斜杠硬断行不叠加', () => {
    const md = 'a\\\nb'
    expect(hardenSoftBreaks(md)).toBe('a\\\nb')
  })

  it('空串与单行安全', () => {
    expect(hardenSoftBreaks('')).toBe('')
    expect(hardenSoftBreaks('only line')).toBe('only line')
  })
})
