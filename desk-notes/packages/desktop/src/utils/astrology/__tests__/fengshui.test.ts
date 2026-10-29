import { describe, test, expect } from 'vitest'
import { getFengshuiAdvice, getAdditionalTips } from '../fengshui'

describe('getFengshuiAdvice', () => {
  const VALID_DOMAINS = ['career', 'wealth', 'relationship', 'travel', 'study', 'life', 'other']
  const DIRECTIONS = ['正东', '正南', '正西', '正北', '东南', '西南', '西北', '东北']

  test('returns complete FengshuiAdvice for every domain', () => {
    for (const domain of VALID_DOMAINS) {
      const advice = getFengshuiAdvice(domain, '正东')
      expect(advice).toBeTruthy()
      expect(typeof advice.deskOrientation).toBe('string')
      expect(typeof advice.seatPlacement).toBe('string')
      expect(typeof advice.enhancement).toBe('string')
      expect(typeof advice.avoid).toBe('string')
      expect(advice.deskOrientation.length).toBeGreaterThan(5)
      expect(advice.seatPlacement.length).toBeGreaterThan(5)
    }
  })

  test('replaces "吉方" placeholder with actual lucky direction in deskOrientation', () => {
    for (const dir of DIRECTIONS) {
      const advice = getFengshuiAdvice('career', dir)
      expect(advice.deskOrientation).toContain(dir)
      expect(advice.deskOrientation).not.toContain('吉方')
    }
  })

  test('falls back to "other" for unknown domain', () => {
    const advice = getFengshuiAdvice('unknown-domain', '正南')
    expect(advice).toBeTruthy()
    expect(typeof advice.deskOrientation).toBe('string')
  })

  test('every domain has extra field', () => {
    for (const domain of VALID_DOMAINS) {
      const advice = getFengshuiAdvice(domain, '西北')
      expect(advice.extra).toBeTruthy()
      expect(advice.extra!.length).toBeGreaterThan(5)
    }
  })

  test('returns different advice for different domains', () => {
    const career = getFengshuiAdvice('career', '正东')
    const wealth = getFengshuiAdvice('wealth', '正东')
    expect(career.seatPlacement).not.toBe(wealth.seatPlacement)
  })

  test('works with all eight directions without error', () => {
    // "other" domain lacks "吉方" placeholder, skip it
    const domainsWithDirection = VALID_DOMAINS.filter(d => d !== 'other')
    for (const dir of DIRECTIONS) {
      for (const domain of domainsWithDirection) {
        const advice = getFengshuiAdvice(domain, dir)
        expect(advice.deskOrientation).toContain(dir)
      }
    }
  })
})

describe('getAdditionalTips', () => {
  const DIRECTIONS_WITH_TIPS = ['正东', '正南', '正西', '正北', '东南', '西南', '西北', '东北']

  test('returns array of strings for every known direction', () => {
    for (const dir of DIRECTIONS_WITH_TIPS) {
      const tips = getAdditionalTips(dir)
      expect(Array.isArray(tips)).toBe(true)
      expect(tips.length).toBeGreaterThanOrEqual(1)
      for (const tip of tips) {
        expect(typeof tip).toBe('string')
        expect(tip.length).toBeGreaterThan(3)
      }
    }
  })

  test('returns fallback tips for unknown direction', () => {
    const tips = getAdditionalTips('未知方向')
    expect(Array.isArray(tips)).toBe(true)
    expect(tips.length).toBe(1)
    expect(tips[0]).toContain('整洁通风')
  })

  test('each known direction has exactly 2 tips', () => {
    for (const dir of DIRECTIONS_WITH_TIPS) {
      const tips = getAdditionalTips(dir)
      expect(tips.length).toBe(2)
    }
  })

  test('direction-specific tips are different from each other', () => {
    const eastTips = getAdditionalTips('正东')
    const westTips = getAdditionalTips('正西')
    expect(eastTips[0]).not.toBe(westTips[0])
  })
})
