import { describe, it, expect } from 'vitest'
import {
  uid, formatRelativeTime, stripHtml, countText,
  hexToRgb, COLOR_PRESETS,
} from '../helpers'

describe('uid', () => {
  it('returns a string', () => {
    expect(typeof uid()).toBe('string')
  })

  it('returns unique values', () => {
    const ids = new Set(Array.from({ length: 100 }, () => uid()))
    expect(ids.size).toBe(100)
  })
})

describe('formatRelativeTime', () => {
  it('returns time string for today', () => {
    const result = formatRelativeTime(Date.now())
    expect(result).toMatch(/^\d{2}:\d{2}$/)
  })

  it('returns "昨天" for yesterday (Chinese)', () => {
    const yesterday = Date.now() - 86400000
    expect(formatRelativeTime(yesterday)).toBe('昨天')
  })

  it('returns "Yesterday" for yesterday (English)', () => {
    const yesterday = Date.now() - 86400000
    expect(formatRelativeTime(yesterday, 'en')).toBe('Yesterday')
  })

  it('returns MM/DD for older dates', () => {
    const old = new Date('2024-01-15').getTime()
    expect(formatRelativeTime(old)).toMatch(/^\d{2}\/\d{2}$/)
  })
})

describe('stripHtml', () => {
  it('strips HTML tags', () => {
    expect(stripHtml('<p>Hello <b>world</b></p>')).toBe('Hello world')
  })

  it('returns empty string for empty input', () => {
    expect(stripHtml('')).toBe('')
  })
})

describe('countText', () => {
  it('counts Chinese characters with 字 label', () => {
    const result = countText('你好世界')
    expect(result.count).toBe(4)
    expect(result.label).toBe('字')
  })

  it('counts English words with 字符 label', () => {
    const result = countText('hello world foo bar')
    expect(result.count).toBe(4)
    expect(result.label).toBe('字符')
  })
})

describe('hexToRgb', () => {
  it('converts 6-digit hex', () => {
    expect(hexToRgb('#ff0000')).toEqual({ r: 255, g: 0, b: 0 })
  })

  it('converts 3-digit hex', () => {
    expect(hexToRgb('#f00')).toEqual({ r: 255, g: 0, b: 0 })
  })

  it('returns null for invalid hex', () => {
    expect(hexToRgb('#xyz')).toBeNull()
  })
})

describe('COLOR_PRESETS', () => {
  it('has expected presets', () => {
    expect(COLOR_PRESETS.length).toBe(8)
    expect(COLOR_PRESETS[0].label).toBe('默认')
    expect(COLOR_PRESETS[4].label).toBe('青草')
  })
})
