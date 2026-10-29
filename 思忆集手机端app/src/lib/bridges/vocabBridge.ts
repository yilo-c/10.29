/**
 * 词库/翻译桥 — 桌面 `word.*` / `wordbook.*` / `translate.*` RPC 的薄封装。
 *
 * 主要复刻能力（手机端原先大量直接 fetch /rpc，这里统一走 callRpc）：
 *   - 听写检查 `word.dictation_check`（含 SM-2 评分建议）；
 *   - 单词深度查询 `translate.word_detail`；
 *   - 整段文本提取生词 `translate.extract_words`；
 *   - 翻译历史保存 `translations.save`；
 *   - 词库 + 单词的全集 CRUD。
 */
import { callRpc } from '../rpcClient'

export interface Wordbook {
  id: string | number
  name: string
  created_at?: string | number
}

export interface WordEntry {
  id: string | number
  word: string
  definition?: string
  example?: string
  meaning?: string
  phonetic?: string
  next_review_at?: string | number
  ease?: number
}

export interface DictationCheckResult {
  correct: boolean
  user_input: string
  expected: string
  hint?: string
  sm2_quality?: number
  feedback?: string
  next_interval?: number
}

export interface WordDetail {
  word: string
  phonetic?: string
  definitions?: string[]
  examples?: string[]
  synonyms?: string[]
  antonyms?: string[]
  translation?: string
}

/* ── Wordbook CRUD ── */
export async function listWordbooks(): Promise<Wordbook[]> {
  try {
    const res = await callRpc<unknown>('wordbook.list', {})
    if (Array.isArray(res)) return res as Wordbook[]
    return []
  } catch {
    return []
  }
}

export async function createWordbook(name: string): Promise<Wordbook | null> {
  try {
    return await callRpc<Wordbook>('wordbook.create', { name })
  } catch {
    return null
  }
}

export async function renameWordbook(wordbookId: string | number, name: string): Promise<boolean> {
  try {
    await callRpc('wordbook.rename', { wordbook_id: wordbookId, name })
    return true
  } catch {
    return false
  }
}

export async function deleteWordbook(wordbookId: string | number): Promise<boolean> {
  try {
    await callRpc('wordbook.delete', { wordbook_id: wordbookId })
    return true
  } catch {
    return false
  }
}

/* ── Word CRUD ── */
export async function listWords(wordbookId: string | number, limit = 500): Promise<WordEntry[]> {
  try {
    const res = await callRpc<unknown>('word.list', { wordbook_id: wordbookId, limit })
    if (Array.isArray(res)) return res as WordEntry[]
    return []
  } catch {
    return []
  }
}

export async function addWord(args: {
  wordbook_id: string | number
  word: string
  definition?: string
  example?: string
  meaning?: string
}): Promise<WordEntry | null> {
  try {
    return await callRpc<WordEntry>('word.add', args as Record<string, unknown>)
  } catch {
    return null
  }
}

export async function updateWord(wordId: string | number, patch: Partial<WordEntry>): Promise<boolean> {
  try {
    await callRpc('word.update', { word_id: wordId, ...patch })
    return true
  } catch {
    return false
  }
}

export async function deleteWord(wordId: string | number): Promise<boolean> {
  try {
    await callRpc('word.delete', { word_id: wordId })
    return true
  } catch {
    return false
  }
}

/** 听写检查；带 SM-2 评分。 */
export async function dictationCheck(wordId: string | number, userInput: string): Promise<DictationCheckResult | null> {
  try {
    const res = await callRpc<unknown>('word.dictation_check', { word_id: wordId, user_input: userInput })
    if (res && typeof res === 'object') return res as DictationCheckResult
    return null
  } catch {
    return null
  }
}

/* ── Translate ── */

export async function translateText(text: string, source = 'auto', target = 'zh'): Promise<string> {
  try {
    const res = await callRpc<unknown>('translate.text', { text, source, target })
    if (typeof res === 'string') return res
    if (res && typeof res === 'object') {
      const obj = res as Record<string, unknown>
      if (typeof obj.translated === 'string') return obj.translated
      if (typeof obj.translation === 'string') return obj.translation
    }
    return ''
  } catch {
    return ''
  }
}

export async function translateLanguages(): Promise<string[]> {
  try {
    const res = await callRpc<unknown>('translate.languages', {})
    if (Array.isArray(res)) return res.filter((x): x is string => typeof x === 'string')
    return []
  } catch {
    return []
  }
}

export async function extractWords(text: string): Promise<string[]> {
  try {
    const res = await callRpc<unknown>('translate.extract_words', { text })
    if (Array.isArray(res)) return res.filter((x): x is string => typeof x === 'string')
    if (res && typeof res === 'object' && Array.isArray((res as { words?: unknown[] }).words)) {
      return (res as { words: unknown[] }).words.filter((x): x is string => typeof x === 'string')
    }
    return []
  } catch {
    return []
  }
}

export async function wordDetail(word: string, source = 'en', target = 'zh'): Promise<WordDetail | null> {
  try {
    const res = await callRpc<unknown>('translate.word_detail', { word, source, target })
    if (res && typeof res === 'object') return res as WordDetail
    return null
  } catch {
    return null
  }
}

export async function saveTranslation(args: {
  source_text: string
  translated_text: string
  source_lang: string
  target_lang: string
  mode?: string
}): Promise<boolean> {
  try {
    await callRpc('translations.save', args as Record<string, unknown>)
    return true
  } catch {
    return false
  }
}
