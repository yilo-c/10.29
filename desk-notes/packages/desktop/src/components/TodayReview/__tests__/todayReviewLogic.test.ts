import { describe, it, expect } from 'vitest'
import {
  aggregateDayReview,
  countCategories,
  filterByLocalDay,
  infoItemTimestamp,
  isWithinLocalDay,
  localDateKey,
  noteTimestamp,
  startOfLocalDay,
  toTimestamp,
  topCategories,
  truncate,
  type InfoItemRaw,
  type NoteRaw,
} from '../todayReviewLogic'

// 基准「现在」= 本地 2026-09-12 14:00（全部断言用本地 Date 派生，跨时区稳定）
const NOW = new Date(2026, 8, 12, 14, 0, 0)
const at = (dayOffset: number, h: number, m: number): number =>
  new Date(NOW.getFullYear(), NOW.getMonth(), NOW.getDate() + dayOffset, h, m, 0).getTime()

describe('时间过滤逻辑（今日/昨日按本地时区切日）', () => {
  it('toTimestamp 统一 秒/毫秒 epoch、ISO 串与脏值', () => {
    const ms = at(0, 9, 30)
    expect(toTimestamp(ms)).toBe(ms)
    expect(toTimestamp(Math.floor(ms / 1000))).toBe(Math.floor(ms / 1000) * 1000)
    expect(toTimestamp(new Date(ms).toISOString())).toBe(ms)
    expect(toTimestamp('not-a-date')).toBeUndefined()
    expect(toTimestamp(undefined)).toBeUndefined()
  })

  it('filterByLocalDay 只保留目标本地日条目（含 00:00 边界）', () => {
    const items: InfoItemRaw[] = [
      { id: 1, title: '今日早', collected_at: at(0, 0, 0) },
      { id: 2, title: '今日午', collected_at: new Date(at(0, 12, 5)).toISOString() },
      { id: 3, title: '昨日晚', collected_at: at(-1, 23, 50) },
      { id: 4, title: '明日前', collected_at: startOfLocalDay(1, NOW) },
      { id: 5, title: '没时间戳' },
    ]
    const today = filterByLocalDay(items, infoItemTimestamp, 0, NOW)
    expect(today.map(i => i.title)).toEqual(['今日早', '今日午'])
    expect(filterByLocalDay(items, infoItemTimestamp, -1, NOW).map(i => i.title)).toEqual(['昨日晚'])
    // 次日 00:00 属于「明天」，今日区间右开
    expect(isWithinLocalDay(startOfLocalDay(1, NOW), 0, NOW)).toBe(false)
    expect(isWithinLocalDay(at(0, 23, 59), 0, NOW)).toBe(true)
  })

  it('笔记取 updated_at，缺失时回落 created_at', () => {
    const note: NoteRaw = { id: 'a', title: 't', content: 'c', updated_at: at(-1, 8, 0), created_at: at(0, 9, 0) }
    expect(noteTimestamp(note)).toBe(at(-1, 8, 0))
    expect(noteTimestamp({ id: 'b', created_at: at(0, 9, 0) })).toBe(at(0, 9, 0))
  })
})

describe('聚合分组（资讯 + 笔记按时间倒序合并、统计条）', () => {
  const infoItems: InfoItemRaw[] = [
    { id: 11, title: 'AI 早', title_zh: '人工智能早', summary: 's1', category: 'ai', collected_at: at(0, 8, 0) },
    { id: 12, title: 'AI 晚', summary_zh: 's2', category: 'ai', collected_at: at(0, 20, 0) },
    { id: 13, title: 'Dev 中', summary: 's3', category: 'dev', collected_at: at(0, 15, 0) },
    { id: 14, title: '昨日', summary: 's4', category: 'ai', collected_at: at(-1, 22, 0) },
  ]
  const notes: NoteRaw[] = [
    { id: 'n1', title: '笔记一', content: '# 标题\n正文', category: '笔记', updated_at: at(0, 19, 0) },
    { id: 'n2', content: '首行即标题\n其余', updated_at: at(0, 7, 0) },
    { id: 'n3', title: '已删', content: 'x', updated_at: at(0, 6, 0), deleted_at: '2026-09-12T00:00:00Z' },
    { id: 'n4', title: '昨日笔记', content: 'y', updated_at: at(-1, 23, 0) },
  ]

  const review = aggregateDayReview({ infoItems, notes, dayOffset: 0, now: NOW })

  it('统计条：捕获数 / 笔记数 / 活跃类目 top3（按条数降序）', () => {
    expect(review.dateKey).toBe(localDateKey(startOfLocalDay(0, NOW)))
    expect(review.dateKey).toBe('2026-09-12')
    expect(review.captureCount).toBe(3)
    expect(review.noteCount).toBe(2)
    expect(topCategories(review.entries, 3)).toEqual([
      { category: 'ai', count: 2 },
      { category: '笔记', count: 1 },
      { category: 'dev', count: 1 },
    ])
    expect(countCategories(review.entries)).toHaveLength(4)
  })

  it('条目按时间倒序合并，标题/摘要取中文优先与首行兜底', () => {
    expect(review.entries.map(e => [e.kind, e.title])).toEqual([
      ['info', 'AI 晚'],
      ['note', '笔记一'],
      ['info', 'Dev 中'],
      ['info', '人工智能早'],
      ['note', '首行即标题'],
    ])
    expect(review.entries.every((e, i, arr) => i === 0 || arr[i - 1].ts >= e.ts)).toBe(true)
    expect(review.entries[1].summary).toBe(truncate('标题 正文', 160))
    expect(new Set(review.entries.map(e => e.key)).size).toBe(review.entries.length)
    expect(review.entries.every(e => e.ts >= startOfLocalDay(0, NOW))).toBe(true)
  })

  it('切到昨日只留昨日条目', () => {
    const y = aggregateDayReview({ infoItems, notes, dayOffset: -1, now: NOW })
    expect(y.entries.map(e => e.title)).toEqual(['昨日笔记', '昨日'])
    expect(y.captureCount).toBe(1)
    expect(y.noteCount).toBe(1)
  })
})
