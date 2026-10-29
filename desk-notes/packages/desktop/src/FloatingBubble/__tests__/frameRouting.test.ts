/**
 * frameRouting 单测 —— 锁死「流式帧分流」规则。
 *
 * 回归背景（2026-09-15 用户实测）：悬浮窗输出复读膨胀，形态为
 * "当前您有 N 篇笔记等待探索…" 逐字符自我叠加。根因是 notice / tool_progress
 * 等元数据帧被当正文追加（旧代码用 client.stream<string>() 丢失了帧 type）。
 *
 * 本测试的核心断言：**元数据帧绝不进入正文**——这是防复读的唯一机器防线。
 */
import { describe, it, expect } from 'vitest'

import { applyFrameToMessages, isBodyFrame, type RoutedMessage } from '../frameRouting'

const userMsg: RoutedMessage = { role: 'user', content: '看看我的笔记' }
const emptyAssistant: RoutedMessage = { role: 'assistant', content: '' }

describe('applyFrameToMessages', () => {
  it('chunk 帧追加进正文（唯一的正文来源）', () => {
    const out = applyFrameToMessages([userMsg, emptyAssistant], { type: 'chunk', data: '你好' })
    expect(out[out.length - 1].content).toBe('你好')

    const out2 = applyFrameToMessages(out, { type: 'chunk', data: '，世界' })
    expect(out2[out2.length - 1].content).toBe('你好，世界')
  })

  it('notice 帧不写入正文（复读根因的机器防线）', () => {
    const out = applyFrameToMessages([userMsg, emptyAssistant], {
      type: 'notice',
      data: JSON.stringify({ phase: 'starting', status: 'running', message: '正在启动内核并分析任务…' }),
    })
    expect(out[out.length - 1].content).toBe('')
  })

  it.each(['done', 'tool_progress', 'tool_result', 'planner', 'status'])(
    '%s 帧不写入正文',
    type => {
      const out = applyFrameToMessages([userMsg, emptyAssistant], { type, data: '某个状态文本' })
      expect(out[out.length - 1].content).toBe('')
    },
  )

  it('error 帧追加错误段（有正文时换行接续）', () => {
    const withBody = applyFrameToMessages([userMsg, { role: 'assistant', content: '部分回复' }], {
      type: 'error',
      data: '内核未响应',
    })
    expect(withBody[withBody.length - 1].content).toBe('部分回复\n[错误] 内核未响应')

    const noBody = applyFrameToMessages([userMsg, emptyAssistant], { type: 'error', data: '内核未响应' })
    expect(noBody[noBody.length - 1].content).toBe('[错误] 内核未响应')
  })

  it('复读场景复现：大量 notice 混杂 chunk 时，正文只含 chunk 文本', () => {
    let msgs: RoutedMessage[] = [userMsg, emptyAssistant]
    // 模拟真实流：反复出现状态/工具帧 + 少量正文帧
    const stream = [
      { type: 'notice', data: '当前您有 145 篇笔记等待探索，这些笔记覆盖了以下几个领域：' },
      { type: 'chunk', data: '你的笔记主要' },
      { type: 'tool_progress', data: '正在检索…' },
      { type: 'notice', data: '当前您有 145 篇笔记等待探索，这些笔记覆盖了以下几个领域：' },
      { type: 'chunk', data: '集中在 AI 与前端。' },
      { type: 'planner', data: '计划：先检索再总结' },
      { type: 'done', data: '' },
    ]
    for (const f of stream) msgs = applyFrameToMessages(msgs, f)

    const body = msgs[msgs.length - 1].content
    expect(body).toBe('你的笔记主要集中在 AI 与前端。')
    // 元数据文本一次都不得出现在正文里
    expect(body).not.toContain('145 篇笔记')
    expect(body).not.toContain('正在检索')
    expect(body).not.toContain('计划：')
  })

  it('边界：空帧 / 空 data / 空列表 / 末条非 assistant → 原样返回', () => {
    const base: RoutedMessage[] = [userMsg, emptyAssistant]
    expect(applyFrameToMessages(base, null)).toBe(base)
    expect(applyFrameToMessages(base, undefined)).toBe(base)
    expect(applyFrameToMessages(base, { type: 'chunk', data: '' })).toBe(base)
    expect(applyFrameToMessages([], { type: 'chunk', data: 'x' })).toEqual([])
    // 末条是 user（正常情况下不该发生）→ 不误写
    const onlyUser: RoutedMessage[] = [userMsg]
    expect(applyFrameToMessages(onlyUser, { type: 'chunk', data: 'x' })).toBe(onlyUser)
  })

  it('不可变：不修改入参数组', () => {
    const base: RoutedMessage[] = [userMsg, emptyAssistant]
    const out = applyFrameToMessages(base, { type: 'chunk', data: 'x' })
    expect(base[base.length - 1].content).toBe('')
    expect(out).not.toBe(base)
  })
})

describe('isBodyFrame', () => {
  it('仅 chunk/error 且有 data 时为正文帧', () => {
    expect(isBodyFrame({ type: 'chunk', data: 'x' })).toBe(true)
    expect(isBodyFrame({ type: 'error', data: 'x' })).toBe(true)
    expect(isBodyFrame({ type: 'notice', data: 'x' })).toBe(false)
    expect(isBodyFrame({ type: 'chunk', data: '' })).toBe(false)
    expect(isBodyFrame(null)).toBe(false)
  })
})
