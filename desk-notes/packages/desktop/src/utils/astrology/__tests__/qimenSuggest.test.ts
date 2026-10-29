import { describe, test, expect } from 'vitest'
import { suggestQimenEvent, getDomainLabel } from '../qimenSuggest'
import type { QimenDomain } from '../../../types'

describe('suggestQimenEvent', () => {
  test('returns null for empty text', () => {
    expect(suggestQimenEvent('')).toBeNull()
    expect(suggestQimenEvent(null as unknown as string)).toBeNull()
  })

  test('detects career domain from keywords', () => {
    const result = suggestQimenEvent('明天去面试')
    expect(result).not.toBeNull()
    expect(result!.domain).toBe('career')
    expect(result!.scenario).toBe('jobInterview')
  })

  test('detects wealth domain', () => {
    const result = suggestQimenEvent('基金定投')
    expect(result).not.toBeNull()
    expect(result!.domain).toBe('wealth')
    expect(result!.scenario).toBe('stock')
  })

  test('detects relationship domain', () => {
    const result = suggestQimenEvent('晚上约会')
    expect(result).not.toBeNull()
    expect(result!.domain).toBe('relationship')
    expect(result!.scenario).toBe('date')
  })

  test('detects travel domain', () => {
    const result = suggestQimenEvent('订机票出差')
    expect(result).not.toBeNull()
    expect(result!.domain).toBe('travel')
  })

  test('detects study domain', () => {
    const result = suggestQimenEvent('考研报名')
    expect(result).not.toBeNull()
    expect(result!.domain).toBe('study')
    expect(result!.scenario).toBe('exam')
  })

  test('detects life domain', () => {
    const result = suggestQimenEvent('明天去医院复查')
    expect(result).not.toBeNull()
    expect(result!.domain).toBe('life')
    expect(result!.scenario).toBe('medical')
  })

  test('returns null for unrelated text', () => {
    const result = suggestQimenEvent('遛狗')
    expect(result).toBeNull()
  })

  test('matches promotion keyword', () => {
    const result = suggestQimenEvent('准备晋升主管')
    expect(result).not.toBeNull()
    expect(result!.domain).toBe('career')
    expect(result!.scenario).toBe('promotion')
  })

  test('includes description in result', () => {
    const result = suggestQimenEvent('明天去面试')
    expect(result!.description).toBe('明天去面试')
  })
})

describe('getDomainLabel', () => {
  test('returns correct labels', () => {
    expect(getDomainLabel('career')).toBe('事业')
    expect(getDomainLabel('wealth')).toBe('求财')
    expect(getDomainLabel('relationship')).toBe('感情')
    expect(getDomainLabel('travel')).toBe('出行')
    expect(getDomainLabel('study')).toBe('学业')
    expect(getDomainLabel('life')).toBe('生活')
    expect(getDomainLabel('other')).toBe('其他')
  })

  test('returns 其他 for unknown domain', () => {
    expect(getDomainLabel('unknown' as unknown as QimenDomain)).toBe('其他')
  })
})
