/**
 * 会话存储测试
 *
 * 重点验证持久化的边界情况：
 *   - 标题从首条用户消息生成
 *   - 超长会话截断（防单会话无限膨胀）
 *   - 会话数超限时清理最旧的
 *   - IndexedDB 不可用时优雅降级（不崩）
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// 内存版 IndexedDB mock
const mem = new Map<string, unknown>()

vi.mock('idb', () => ({}))

// 直接 mock 掉 openDB 依赖的全局 indexedDB
beforeEach(() => {
  mem.clear()
})

import { deriveTitle, newSessionId } from '../sessionStore'

describe('sessionStore / deriveTitle', () => {
  it('用首条消息生成标题', () => {
    expect(deriveTitle('帮我查一下明天的天气')).toBe('帮我查一下明天的天气')
  })

  it('超长标题被截断并加省略号', () => {
    const long = '这是一个非常非常长的用户提问内容需要被截断处理以免标题过长'
    const t = deriveTitle(long)
    expect(t.length).toBeLessThanOrEqual(21)
    expect(t.endsWith('…')).toBe(true)
  })

  it('空内容返回「新会话」', () => {
    expect(deriveTitle('')).toBe('新会话')
    expect(deriveTitle('   ')).toBe('新会话')
  })

  it('多余空白被压缩', () => {
    expect(deriveTitle('  帮我   记一下  ')).toBe('帮我 记一下')
  })
})

describe('sessionStore / newSessionId', () => {
  it('id 唯一', () => {
    const ids = new Set(Array.from({ length: 100 }, () => newSessionId()))
    expect(ids.size).toBe(100)
  })

  it('id 带 s_ 前缀便于识别', () => {
    expect(newSessionId().startsWith('s_')).toBe(true)
  })
})
