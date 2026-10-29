import { swallowError } from './swallowError'
export type HistoryEntry = {
  id: string
  sourceText: string
  result: string
  langDir: string
  mode: string
  ts: number
}

/** 后端 translations.list 返回条目形状（_ai_translate.py，手机端同步协议） */
export interface BackendTranslationItem {
  source?: string
  target?: string
  sourceText?: string
  translatedText?: string
  timestamp?: number
}

const STORAGE_KEY = 'siyi_translation_history'
const MAX_ENTRIES = 20
/** 合并后端跨设备历史后的展示上限 */
const MAX_MERGED_ENTRIES = 50

function load(): HistoryEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const data = JSON.parse(raw)
    if (!Array.isArray(data)) return []
    return data
  } catch { return [] }
}

function save(entries: HistoryEntry[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, MAX_ENTRIES)))
  } catch (e) { /* quota exceeded — silently drop */ swallowError('translationHistory:26', e) }
}

export function addTranslationHistory(
  sourceText: string,
  result: string,
  langDir: string,
  mode: string,
): void {
  if (!sourceText.trim() || !result.trim()) return
  const entries = load()
  // Deduplicate same source text + mode
  const existing = entries.findIndex(e => e.sourceText === sourceText && e.langDir === langDir && e.mode === mode)
  if (existing >= 0) entries.splice(existing, 1)
  entries.unshift({
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    sourceText,
    result,
    langDir,
    mode,
    ts: Date.now(),
  })
  save(entries)
}

export function getTranslationHistory(): HistoryEntry[] {
  return load()
}

export function clearTranslationHistory(): void {
  try { localStorage.removeItem(STORAGE_KEY) } catch (e) { /* ignore */ swallowError('translationHistory:56', e) }
}

/**
 * C-14④-1：后端 translations.list（跨设备同步历史）并入本地历史。
 * 本地条目优先（带准确 mode），按 sourceText+langDir 去重；
 * 后端不下发 mode，统一回落 'learn' 展示。结果按时间倒序，上限 50 条。
 */
export function mergeBackendHistory(local: HistoryEntry[], backend: BackendTranslationItem[]): HistoryEntry[] {
  if (!Array.isArray(backend)) return local
  const merged = [...local]
  for (const item of backend) {
    const sourceText = typeof item?.sourceText === 'string' ? item.sourceText : ''
    const translated = typeof item?.translatedText === 'string' ? item.translatedText : ''
    if (!sourceText.trim() || !translated.trim()) continue
    const langDir = item.source === 'zh' ? 'zh2en' : 'en2zh'
    if (merged.some(e => e.sourceText === sourceText && e.langDir === langDir)) continue
    merged.push({
      id: `srv_${item.timestamp ?? sourceText}`,
      sourceText,
      result: translated,
      langDir,
      mode: 'learn',
      ts: typeof item.timestamp === 'number' ? item.timestamp : 0,
    })
  }
  return merged.sort((a, b) => b.ts - a.ts).slice(0, MAX_MERGED_ENTRIES)
}
