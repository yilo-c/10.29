import { useCallback } from 'react'
import { useStore } from '../store/useStore'
import zh, { type Translations } from './zh-CN'
import en from './en'

const locales: Record<string, Translations> = { 'zh-CN': zh, en }

export type SupportedLocale = 'zh-CN' | 'en'

/**
 * Simple template string interpolator.
 * Replaces {key} placeholders with values from the params object.
 */
function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    const val = params[key]
    return val !== undefined ? String(val) : `{${key}}`
  })
}

/**
 * Deep key lookup: gets a nested value from an object using dot-separated path.
 * e.g. getKey(translations, 'todo.sortByCreated') -> "创建时间"
 */
function getKey(obj: Record<string, unknown>, path: string): string | undefined {
  const parts = path.split('.')
  let current: unknown = obj
  for (const part of parts) {
    if (current === null || typeof current !== 'object') return undefined
    current = (current as Record<string, unknown>)[part]
  }
  return typeof current === 'string' ? current : undefined
}

/** Non-hook translation lookup for module-level code (queues, stores, utils). */
export function tStatic(key: string, params?: Record<string, string | number>): string {
  const locale = useStore.getState().locale
  const translations = locales[locale] || zh
  const raw = getKey(translations as unknown as Record<string, unknown>, key)
    ?? getKey(zh as unknown as Record<string, unknown>, key)
  return raw ? interpolate(raw, params) : key
}

export function useTranslation() {
  const locale = useStore(s => s.locale)
  const translations = locales[locale] || zh

  const t = useCallback(
    (key: string, params?: Record<string, string | number>): string => {
      const raw = getKey(translations as unknown as Record<string, unknown>, key)
      if (raw === undefined) {
        // Fallback to Chinese
        const fallback = getKey(zh as unknown as Record<string, unknown>, key)
        return fallback ? interpolate(fallback, params) : key
      }
      return interpolate(raw, params)
    },
    [translations]
  )

  /** For lookups that return arrays (weekday names, month names, etc.) */
  const tArray = useCallback(
    (key: string): readonly string[] => {
      const parts = key.split('.')
      let current: unknown = translations
      for (const part of parts) {
        if (current === null || typeof current !== 'object') break
        current = (current as Record<string, unknown>)[part]
      }
      if (Array.isArray(current)) return current as string[]
      // Fallback to Chinese
      let fallback: unknown = zh
      for (const part of parts) {
        if (fallback === null || typeof fallback !== 'object') break
        fallback = (fallback as Record<string, unknown>)[part]
      }
      if (Array.isArray(fallback)) return fallback as string[]
      return []
    },
    [translations]
  )

  return { t, tArray, locale }
}

/** For non-hook usage (e.g., outside React components) */
export function getCurrentLocale(): SupportedLocale {
  try {
    const state = useStore.getState()
    return state.locale
  } catch {
    return 'zh-CN'
  }
}

/** Get translations for the current locale (imperative, for non-React code) */
export function getTranslations(): Translations {
  const locale = getCurrentLocale()
  return locales[locale] || zh
}

export { locales }
