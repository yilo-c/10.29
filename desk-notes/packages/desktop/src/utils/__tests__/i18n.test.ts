import { describe, it, expect, beforeEach } from 'vitest'
import { getCurrentLocale, getTranslations, locales } from '../../i18n'
import { useStore } from '../../store/useStore'

describe('locales', () => {
  it('has zh-CN translations', () => {
    expect(locales['zh-CN']).toBeDefined()
    expect(locales['zh-CN'].app.title).toBe('思忆集')
  })

  it('has en translations', () => {
    expect(locales.en).toBeDefined()
    expect(locales.en.app.title).toBe('Siyi Collection')
  })

  it('zh-CN has todo section', () => {
    const zh = locales['zh-CN']
    expect(zh.todo?.addPlaceholder).toBeDefined()
    expect(zh.todo?.searchPlaceholder).toBeDefined()
    expect(zh.todo?.sortByCreated).toBeDefined()
    expect(zh.todo?.sortByDueDate).toBeDefined()
  })

  it('en has todo section', () => {
    const en = locales.en
    expect(en.todo?.addPlaceholder).toBeDefined()
    expect(en.todo?.searchPlaceholder).toBe('Search')
  })
})

describe('getCurrentLocale', () => {
  beforeEach(() => {
    // Reset locale to default
    useStore.getState().setLocale('zh-CN')
  })

  it('returns zh-CN by default', () => {
    expect(getCurrentLocale()).toBe('zh-CN')
  })

  it('returns en after switching locale', () => {
    useStore.getState().setLocale('en')
    expect(getCurrentLocale()).toBe('en')
  })
})

describe('getTranslations', () => {
  beforeEach(() => {
    useStore.getState().setLocale('zh-CN')
  })

  it('returns zh-CN translations by default', () => {
    const t = getTranslations()
    expect(t.app.title).toBe('思忆集')
  })

  it('returns en translations after switching locale', () => {
    useStore.getState().setLocale('en')
    const t = getTranslations()
    expect(t.app.title).toBe('Siyi Collection')
  })

  it('has matching top-level keys between locales', () => {
    const zhKeys = Object.keys(locales['zh-CN']).sort()
    const enKeys = Object.keys(locales.en).sort()
    expect(zhKeys).toEqual(enKeys)
  })
})
