import { describe, it, expect, vi, beforeEach } from 'vitest'

// U0 治本（2026-09-19）：utils/markdownConvert.ts 已随「读即迁移」机件整体移除。
// 本文件仅需一个断言用探测器——局部内联（子串级足矣，仅测试内使用）。
const looksLikeHtml = (content: string): boolean =>
  !!content && /<\/?(?:p|div|h[1-6]|ul|ol|span|table)/i.test(content)

// 契约 §3 group C / §1.1：KnowledgeBaseModule 已 MD 直存（graphService.saveMdFile），
// 本测试锁定「保存链对格式透明」——saveMdFile 收到的 content 必须原样是 Markdown，
// 不含 HTML 标签；前端禁把 HTML 写入 content。
const calls: Array<{ method: string; params: Record<string, unknown> }> = []
const mockClient = {
  status: 'connected',
  call: vi.fn(async (method: string, params: Record<string, unknown>) => {
    calls.push({ method, params })
    return { success: true }
  }),
}

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => mockClient,
}))

import { saveMdFile } from '../../Graph/graphService'

const MD_SAMPLE = `# 标题

- 列表项 **加粗**
- [x] 已完成

| 列 | 值 |
|----|----|
| a  | 1  |

> 引用

\`\`\`js
const x = 1
\`\`\`
`

describe('KB MD 契约（M3-5a）', () => {
  beforeEach(() => {
    calls.length = 0
    mockClient.call.mockClear()
  })

  it('示例真源内容命中 looksLikeHtml 为 false（纯 MD）', () => {
    expect(looksLikeHtml(MD_SAMPLE)).toBe(false)
  })

  it('HTML 形态会被 looksLikeHtml 判真（回归守卫）', () => {
    expect(looksLikeHtml('<p>hi</p>')).toBe(true)
    expect(looksLikeHtml('<div>x</div>')).toBe(true)
  })

  it('saveMdFile 原样转发 MD，写入 RPC 的 content 不含 HTML 标签', async () => {
    const res = await saveMdFile('notes/kb-demo.md', MD_SAMPLE)
    expect(res.success).toBe(true)

    expect(calls).toHaveLength(1)
    expect(calls[0].method).toBe('knowledge.md_save')
    expect(calls[0].params.rel).toBe('notes/kb-demo.md')
    // content 与传入 MD 逐字节一致（格式透明，无 HTML 化）
    expect(calls[0].params.content).toBe(MD_SAMPLE)
    // 落到后端的内容绝不能是 HTML 形态
    expect(looksLikeHtml(String(calls[0].params.content))).toBe(false)
  })
})
