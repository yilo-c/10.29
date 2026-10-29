import { describe, test, expect } from 'vitest'
import { generateSummaryQuote } from '../guidance'

describe('generateSummaryQuote', () => {
  const baseGuidance = {
    huangli: null,
    advice: null,
    todoStats: { total: 0, done: 0, pending: 0, overdue: 0, highPriority: 0 },
  }

  test('returns rest quote when no advice and no pending todos', () => {
    const quote = generateSummaryQuote(baseGuidance)
    expect(quote).toContain('无事')
    expect(quote).toContain('静坐')
  })

  test('returns encouragement when no advice but pending todos exist', () => {
    const g = { ...baseGuidance, todoStats: { total: 3, done: 0, pending: 3, overdue: 0, highPriority: 0 } }
    const quote = generateSummaryQuote(g)
    expect(quote).toContain('待办')
    expect(quote).toContain('3')
  })

  test('returns level-based quote with pending count', () => {
    const g = {
      huangli: null,
      advice: {
        level: '吉' as const,
        suitable: ['嫁娶', '开市'],
        avoid: ['动土'],
        luckyDirection: '正东',
        clothingAdvice: '宜穿红色',
        summary: '诸事皆宜',
      },
      todoStats: { total: 2, done: 1, pending: 1, overdue: 0, highPriority: 0 },
    }
    const quote = generateSummaryQuote(g)
    expect(quote).toContain('今日吉')
    expect(quote).toContain('待办')
  })

  test('returns level-only quote when no pending todos', () => {
    const g = {
      huangli: null,
      advice: {
        level: '凶' as const,
        suitable: ['静养'],
        avoid: ['出行'],
        luckyDirection: '正西',
        clothingAdvice: '宜穿黑色',
        summary: '宜静不宜动',
      },
      todoStats: { total: 1, done: 1, pending: 0, overdue: 0, highPriority: 0 },
    }
    const quote = generateSummaryQuote(g)
    expect(quote).toContain('今日凶')
    expect(quote).not.toContain('待办')
  })

  test('uses 平和 fallback for unknown level', () => {
    const g = {
      huangli: null,
      advice: {
        level: 'unknown' as unknown as '吉' | '中吉' | '平和' | '小凶' | '凶',
        suitable: [],
        avoid: [],
        luckyDirection: '',
        clothingAdvice: '',
        summary: '',
      },
      todoStats: { total: 0, done: 0, pending: 1, overdue: 0, highPriority: 0 },
    }
    const quote = generateSummaryQuote(g)
    expect(quote).toContain('待办')
  })
})
