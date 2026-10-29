/**
 * C3（前后端对齐规划）：datetime 工具层测试（固定时间基准，防时间炸弹）。
 */
import { describe, it, expect } from 'vitest'
import {
  formatTimeHM,
  formatDateTime,
  formatDateMD,
  formatMessageTimestamp,
  formatRelative,
  formatConversationLabel,
} from '../datetime'

// 固定基准：2026-08-09 14:30
const NOW = new Date(2026, 7, 9, 14, 30, 0)

describe('基础格式', () => {
  it('formatTimeHM / formatDateMD / formatDateTime', () => {
    const ts = new Date(2026, 7, 9, 9, 5, 0)
    expect(formatTimeHM(ts)).toBe('09:05')
    expect(formatDateMD(ts)).toBe('08-09')
    expect(formatDateTime(ts)).toBe('2026-08-09 09:05')
  })
})

describe('formatMessageTimestamp', () => {
  it('今天显示 HH:mm', () => {
    expect(formatMessageTimestamp(new Date(2026, 7, 9, 8, 3), NOW)).toBe('08:03')
  })

  it('昨天显示 昨天 HH:mm', () => {
    expect(formatMessageTimestamp(new Date(2026, 7, 8, 22, 45), NOW)).toBe('昨天 22:45')
  })

  it('今年更早显示 MM-DD HH:mm', () => {
    expect(formatMessageTimestamp(new Date(2026, 2, 5, 10, 0), NOW)).toBe('03-05 10:00')
  })

  it('跨年显示完整日期时间', () => {
    expect(formatMessageTimestamp(new Date(2025, 11, 31, 23, 59), NOW)).toBe('2025-12-31 23:59')
  })

  it('非法时间返回空串', () => {
    expect(formatMessageTimestamp(Number.NaN, NOW)).toBe('')
  })
})

describe('formatRelative', () => {
  it('刚刚 / 分钟前 / 小时前', () => {
    expect(formatRelative(new Date(2026, 7, 9, 14, 29, 40), NOW)).toBe('刚刚')
    expect(formatRelative(new Date(2026, 7, 9, 14, 5), NOW)).toBe('25分钟前')
    expect(formatRelative(new Date(2026, 7, 9, 9, 0), NOW)).toBe('5小时前')
  })

  it('昨天与更早', () => {
    expect(formatRelative(new Date(2026, 7, 8, 20, 0), NOW)).toBe('昨天 20:00')
    expect(formatRelative(new Date(2026, 4, 1, 8, 0), NOW)).toBe('05-01')
    expect(formatRelative(new Date(2025, 0, 2, 8, 0), NOW)).toBe('2025-01-02')
  })
})

describe('formatConversationLabel', () => {
  it('今天 HH:mm；今年 M/D HH:mm；跨年 YYYY/M/D', () => {
    expect(formatConversationLabel(new Date(2026, 7, 9, 7, 2), NOW)).toBe('07:02')
    expect(formatConversationLabel(new Date(2026, 1, 3, 15, 30), NOW)).toBe('2/3 15:30')
    expect(formatConversationLabel(new Date(2025, 11, 31, 23, 0), NOW)).toBe('2025/12/31')
  })

  it('非法/空值返回空串', () => {
    expect(formatConversationLabel(Number.NaN, NOW)).toBe('')
  })
})
