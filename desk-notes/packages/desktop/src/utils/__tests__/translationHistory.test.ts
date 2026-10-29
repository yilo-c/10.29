import { describe, it, expect, beforeEach } from 'vitest'
import {
  addTranslationHistory,
  getTranslationHistory,
  clearTranslationHistory,
  mergeBackendHistory,
  type HistoryEntry,
} from '../translationHistory'

const local = (over: Partial<HistoryEntry>): HistoryEntry => ({
  id: 'local-1',
  sourceText: 'hello',
  result: '你好',
  langDir: 'en2zh',
  mode: 'learn',
  ts: 1000,
  ...over,
})

describe('translationHistory localStorage 闭环', () => {
  beforeEach(() => { localStorage.clear() })

  it('add → get 往返，同 sourceText+langDir+mode 去重置顶', () => {
    addTranslationHistory('a', '甲', 'en2zh', 'quick')
    addTranslationHistory('b', '乙', 'en2zh', 'quick')
    addTranslationHistory('a', '甲2', 'en2zh', 'quick')
    const h = getTranslationHistory()
    expect(h.map(e => e.sourceText)).toEqual(['a', 'b'])
    expect(h[0].result).toBe('甲2')
  })

  it('空内容不写入', () => {
    addTranslationHistory('  ', 'x', 'en2zh', 'quick')
    addTranslationHistory('x', '  ', 'en2zh', 'quick')
    expect(getTranslationHistory()).toEqual([])
  })

  it('clear 清空', () => {
    addTranslationHistory('a', '甲', 'en2zh', 'quick')
    clearTranslationHistory()
    expect(getTranslationHistory()).toEqual([])
  })
})

describe('mergeBackendHistory（C-14④-1 后端历史读回）', () => {
  it('后端条目映射为 HistoryEntry（source=zh → zh2en）', () => {
    const merged = mergeBackendHistory([], [
      { source: 'zh', target: 'en', sourceText: '你好', translatedText: 'hello', timestamp: 2000 },
      { source: 'en', target: 'zh', sourceText: 'world', translatedText: '世界', timestamp: 1000 },
    ])
    expect(merged).toHaveLength(2)
    expect(merged[0]).toMatchObject({ sourceText: '你好', langDir: 'zh2en', mode: 'learn', ts: 2000 })
    expect(merged[1]).toMatchObject({ sourceText: 'world', langDir: 'en2zh' })
  })

  it('本地条目优先：同 sourceText+langDir 的后端条目不重复并入', () => {
    const merged = mergeBackendHistory(
      [local({ sourceText: 'hello', langDir: 'en2zh', mode: 'deep' })],
      [{ source: 'en', sourceText: 'hello', translatedText: '你好（后端版）', timestamp: 9999 }],
    )
    expect(merged).toHaveLength(1)
    expect(merged[0].result).toBe('你好')
    expect(merged[0].mode).toBe('deep')
  })

  it('空字段/畸形条目跳过，backend 非数组原样返回 local', () => {
    const base = [local({})]
    expect(mergeBackendHistory(base, [
      { sourceText: '  ', translatedText: 'x' },
      { sourceText: 'x', translatedText: '' },
      null as unknown as { sourceText?: string },
      { sourceText: 'ok', translatedText: '好', timestamp: 5 },
    ])).toHaveLength(2)
    expect(mergeBackendHistory(base, undefined as unknown as never[])).toBe(base)
  })

  it('结果按 ts 倒序，上限 50 条', () => {
    const backend = Array.from({ length: 60 }, (_, i) => ({
      source: 'en', sourceText: `t${i}`, translatedText: `译${i}`, timestamp: i,
    }))
    const merged = mergeBackendHistory([], backend)
    expect(merged).toHaveLength(50)
    expect(merged[0].sourceText).toBe('t59')
    for (let i = 1; i < merged.length; i++) expect(merged[i - 1].ts).toBeGreaterThanOrEqual(merged[i].ts)
  })
})
