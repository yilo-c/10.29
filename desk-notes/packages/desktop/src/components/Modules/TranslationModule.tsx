import React, { useState, useCallback, useMemo, useRef, useEffect } from 'react'
import Icon from '../Common/Icon'

import { useStore, getWsClient, type AIAction } from '@desk-notes/shared'
import { useTranslation } from '../../i18n'
import { getAIProvider, DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL } from '../../utils/ai'
import WordCards, { type WordEntry } from '../../FloatingBubble/WordCards'
import WordDetailPanel from './WordDetailPanel'
import SpeakButton from '../Common/SpeakButton'
import { TranslationCache } from '../../utils/translationCache'
import { addTranslationHistory, getTranslationHistory, clearTranslationHistory, mergeBackendHistory, type HistoryEntry, type BackendTranslationItem } from '../../utils/translationHistory'
import { swallowError } from '../../utils/swallowError'
import { errorStore } from '../../store/errorStore'

type TranslateFullResult = { translated: string; words?: WordEntry[]; grammar?: GrammarResult }
const translateCache = new TranslationCache<TranslateFullResult>()

type LangDir = 'zh2en' | 'en2zh'
type LearnMode = 'quick' | 'learn' | 'deep'

interface GrammarResult {
  tenses?: string[]
  structure?: string
  voice?: string
  mood?: string
  clauses?: { text: string; type: string; role: string }[]
  parts?: { text: string; role: string; description: string }[]
  grammarPoints?: { pattern: string; explanation: string; example: string }[]
  difficulty?: string
}

const MODE_TAB_IDS: LearnMode[] = ['quick', 'learn', 'deep']
const MODE_TAB_ICONS: Record<LearnMode, string> = {
  quick: 'fa-solid fa-bolt',
  learn: 'fa-solid fa-book',
  deep: 'fa-solid fa-microscope',
}

/** Split text into clickable tokens for word lookup */
function renderClickableText(
  text: string,
  isEnglish: boolean,
  onWordClick: (word: string) => void,
  wordTitleFmt: (w: string) => string,
): React.ReactNode {
  if (!isEnglish) return text
  // Split on whitespace while preserving punctuation
  const tokens = text.split(/(\s+)/)
  return tokens.map((token, i) => {
    if (/^\s+$/.test(token)) return token
    // Extract word from punctuation wrapper
    const match = /^([^a-zA-Z]*)([a-zA-Z]+[-']?[a-zA-Z]*)([^a-zA-Z]*)$/.exec(token)
    if (match) {
      const [, before, word, after] = match
      return (
        <React.Fragment key={i}>
          {before}
          <span
            className="cursor-pointer border-b border-dashed border-purple-400/30 hover:bg-purple-400/[0.1] rounded-sm px-0.5 -mx-0.5 transition-colors"
            onClick={() => onWordClick(word)}
            title={wordTitleFmt(word)}
          >
            {word}
          </span>
          {after}
        </React.Fragment>
      )
    }
    return token
  })
}

const TranslationModule: React.FC = () => {
  const { t } = useTranslation()
  const [sourceText, setSourceText] = useState('')
  const [result, setResult] = useState('')
  const [langDir, setLangDir] = useState<LangDir>('en2zh')
  const [loading, setLoading] = useState(false)
  const [copied, setCopied] = useState(false)
  const copiedTimerRef = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => () => clearTimeout(copiedTimerRef.current), [])
  const [words, setWords] = useState<WordEntry[]>([])
  const [mode, setMode] = useState<LearnMode>('learn')
  const [selectedWord, setSelectedWord] = useState<string | null>(null)
  const [grammar, setGrammar] = useState<GrammarResult | null>(null)
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [showHistory, setShowHistory] = useState(false)

  const reloadHistory = useCallback(() => { setHistory(getTranslationHistory()) }, [])

  /**
   * C-14④-1：打开历史面板时把后端跨设备历史（translations.list）并入展示——
   * 此前后端"存而不读"，只有 localStorage 本地历史可见。失败静默保留本地。
   */
  const openHistory = useCallback(() => {
    setShowHistory(true)
    reloadHistory()
    const client = getWsClient()
    if (client.status !== 'connected') return
    client.call<BackendTranslationItem[]>('translations.list', { limit: 50 })
      .then(items => setHistory(mergeBackendHistory(getTranslationHistory(), Array.isArray(items) ? items : [])))
      .catch((e) => { swallowError('TranslationModule:openHistory', e) })
  }, [reloadHistory])

  const isEnglishSource = langDir === 'en2zh'

  const modeLabel = useCallback((id: LearnMode): string => {
    if (id === 'quick') return t('translation.modeQuick')
    if (id === 'learn') return t('translation.modeLearn')
    return t('translation.modeDeep')
  }, [t])

  const historyModeLabel = useCallback((id: LearnMode): string => {
    if (id === 'quick') return t('translation.historyModeQuick')
    if (id === 'learn') return t('translation.historyModeLearn')
    return t('translation.historyModeDeep')
  }, [t])

  const wordTitleFmt = useCallback((w: string) => t('translation.clickWordTitleFmt', { word: w }), [t])

  const handleTranslate = useCallback(async () => {
    const text = sourceText.trim()
    if (!text || loading) return

    const source = langDir === 'zh2en' ? 'zh' : 'en'
    const target = langDir === 'zh2en' ? 'en' : 'zh'

    // Check cache first
    let cached: TranslateFullResult | null = null
    try { cached = translateCache.get(text, source, target, mode) ?? null } catch (e) { /* cache corrupted */ swallowError('TranslationModule:95', e) }
    if (cached) {
      setResult(cached.translated ?? '')
      if (cached.words?.length) setWords(cached.words)
      if (cached.grammar && !(cached.grammar as unknown as Record<string, unknown>).error) setGrammar(cached.grammar)
      errorStore.info(t('translation.cacheHit'))
      return
    }

    setLoading(true)
    setResult('')
    setWords([])
    setGrammar(null)
    setSelectedWord(null)

    try {
      const client = getWsClient()

      if (client.status === 'connected') {
        // Use translate.full for learn/deep (combined translation + words + grammar in one AI call)
        // Use translate.text for quick (fast, single translation only)
        let translated = ''
        let words: any[] = []
        let grammar: any = null

        if (mode === 'quick') {
          const transResp = await client.call<{ translated: string }>('translate.text', { text, source, target }, 30000)
          translated = transResp?.translated ?? ''
        } else {
          const fullResp = await client.call<TranslateFullResult>('translate.full', { text, source, target, mode }, 90000)
          translated = fullResp?.translated ?? ''
          if (fullResp?.words?.length) words = fullResp.words
          if (fullResp?.grammar && !(fullResp.grammar as unknown as Record<string, unknown>).error) grammar = fullResp.grammar
        }

        setResult(translated)
        if (words.length) setWords(words)
        if (grammar) setGrammar(grammar)

        // Cache successful result & save to history
        translateCache.set(text, source, target, mode, {
          translated,
          words,
          grammar,
        })
        addTranslationHistory(text, translated, langDir, mode)
        reloadHistory()
        // Persist to backend for cross-device history（C-14④-2：失败不再无声吞掉）
        client.call('translations.save', { source_text: text, translated_text: translated, source_lang: source, target_lang: target, mode })
          .catch((e) => { swallowError('TranslationModule:164', e) })
      } else {
        // Fallback: backend offline, use local AI for translation only
        const store = useStore.getState()
        const activeProfile = store.activeProfileId
          ? store.aiProfiles.find(p => p.id === store.activeProfileId)
          : null
        const config = activeProfile
          ? { enabled: true, apiUrl: activeProfile.apiUrl, model: activeProfile.model, protocol: activeProfile.protocol as 'ollama' | 'openai', autoTagging: false }
          : { enabled: true, apiUrl: DEFAULT_LOCAL_API_URL, model: DEFAULT_LOCAL_MODEL, protocol: 'ollama' as const, autoTagging: false }

        const provider = getAIProvider(config)
        const action: AIAction = langDir === 'zh2en' ? 'translate_en' : 'translate_zh'
        const fullResult = await provider.processAction(action, text)
        setResult(fullResult)
        addTranslationHistory(text, fullResult, langDir, mode)
        reloadHistory()
      }
    } catch (err: unknown) {
      setResult(err instanceof Error ? err.message : t('translation.translateFailed'))
    } finally {
      setLoading(false)
    }
  }, [sourceText, langDir, loading, mode, t, reloadHistory])

  const handleCopy = useCallback(async () => {
    if (!result) return
    try {
      await navigator.clipboard.writeText(result)
      setCopied(true)
      clearTimeout(copiedTimerRef.current)
      copiedTimerRef.current = setTimeout(() => setCopied(false), 2000)
    } catch (e) { /* ignore */ swallowError('TranslationModule:175', e) }
  }, [result])

  const handleWordClick = useCallback((word: string) => {
    if (word.length < 2) return
    setSelectedWord(word.toLowerCase())
  }, [])

  const resultDisplay = useMemo(() => {
    if (!result) return null
    return renderClickableText(result, isEnglishSource, handleWordClick, wordTitleFmt)
  }, [result, isEnglishSource, handleWordClick, wordTitleFmt])

  const sourceDisplay = useMemo(() => {
    if (!sourceText) return null
    return renderClickableText(sourceText, isEnglishSource, handleWordClick, wordTitleFmt)
  }, [sourceText, isEnglishSource, handleWordClick, wordTitleFmt])

  return (
    <div className="flex-1 flex flex-col min-h-0 p-4 gap-3">
      {/* Header */}
      <div className="flex items-center gap-2">
        <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'rgba(52,211,153,0.12)', color: '#34d399' }}>
          <Icon icon="fa-solid fa-language text-xs" />
        </div>
        <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{t('translation.title')}</span>
      </div>

      {/* Mode tabs */}
      <div className="flex items-center gap-1 rounded-lg p-0.5 border" style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg)' }}>
        {MODE_TAB_IDS.map(id => (
          <button
            key={id}
            onClick={() => setMode(id)}
            className="flex items-center gap-1 px-3 py-1 rounded-md text-xs font-medium transition-all"
            style={{
              background: mode === id ? 'rgba(52,211,153,0.12)' : 'transparent',
              color: mode === id ? '#34d399' : 'var(--text-muted)',
            }}
          >
            <Icon icon={`${MODE_TAB_ICONS[id]} text-2xs`} />
            {modeLabel(id)}
          </button>
        ))}
      </div>

      {/* Language direction toggle */}
      <div className="flex items-center gap-2">
        <span className={`text-xs font-medium transition-colors ${langDir === 'zh2en' ? '' : 'text-text-muted'}`}
          style={{ color: langDir === 'zh2en' ? 'var(--text-primary)' : undefined }}
        >{t('translation.langZh')}</span>
        <button
          onClick={() => { setLangDir(langDir === 'zh2en' ? 'en2zh' : 'zh2en'); setSelectedWord(null) }}
          className="w-7 h-7 flex items-center justify-center rounded-lg transition-all text-text-muted hover:text-text-secondary hover:bg-white/[0.06]"
        >
          <Icon icon={`fa-solid fa-arrow-right-arrow-left text-xs transition-transform ${langDir === 'zh2en' ? 'scale-x-[-1]' : ''}`} />
        </button>
        <span className={`text-xs font-medium transition-colors ${langDir === 'en2zh' ? '' : 'text-text-muted'}`}
          style={{ color: langDir === 'en2zh' ? 'var(--text-primary)' : undefined }}
        >{t('translation.langEn')}</span>
      </div>

      {/* Source text */}
      <textarea
        value={sourceText}
        onChange={e => { setSourceText(e.target.value); setSelectedWord(null) }}
        placeholder={langDir === 'en2zh' ? t('translation.placeholderEn2Zh') : t('translation.placeholderZh2En')}
        className="w-full h-[72px] px-3 py-2 rounded-xl text-sm leading-relaxed bg-white/[0.04] border border-white/[0.06] outline-none resize-none placeholder-white/20"
        style={{ color: 'var(--text-primary)' }}
      />

      {/* Translate button */}
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={handleTranslate}
          disabled={loading || !sourceText.trim()}
          className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
          style={{ background: 'rgba(52,211,153,0.15)', color: '#34d399' }}
        >
          {loading ? (
            <><div className="w-3 h-3 border-2 border-emerald-400/30 border-t-emerald-400 rounded-full animate-spin" />{t('translation.translating')}</>
          ) : (
            <><Icon icon="fa-solid fa-language text-xs" />{t('translation.translate')}</>
          )}
        </button>
        {result && (
          <button
            onClick={handleCopy}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all"
            style={{ background: 'rgba(255,255,255,0.07)', color: 'var(--text-secondary)' }}
          >
            <Icon icon={`fa-solid ${copied ? 'fa-check' : 'fa-copy'} text-2xs`} />
            {copied ? t('translation.copied') : t('translation.copy')}
          </button>
        )}
      </div>

      {/* Result */}
      {result && (
        <div className="flex-1 flex flex-col gap-2 min-h-0 overflow-y-auto">
          {/* Source text clickable (learn/deep mode only) */}
          {mode !== 'quick' && isEnglishSource && sourceText && (
            <div className="rounded-xl border p-3" style={{ borderColor: 'var(--panel-border)' }}>
              <div className="text-2xs font-medium mb-1.5 flex items-center gap-1.5" style={{ color: 'var(--text-dim)' }}>
                {t('translation.sourceHeader')} <span className="opacity-50">{t('translation.sourceClickHint')}</span>
                <span className="ml-auto"><SpeakButton text={sourceText} lang="en" variant="icon-sm" /></span>
              </div>
              <div className="text-sm leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                {sourceDisplay}
              </div>
            </div>
          )}

          {/* Translation result */}
          <div className="rounded-xl border p-3" style={{ borderColor: 'var(--panel-border)' }}>
            <div className="text-2xs font-medium mb-1.5 flex items-center gap-1.5" style={{ color: 'var(--text-dim)' }}>
              {langDir === 'en2zh' ? t('translation.resultHeaderEn2Zh') : t('translation.resultHeaderZh2En')}
              {mode !== 'quick' && (
                <span className="opacity-50 ml-1">
                  {mode === 'deep' ? t('translation.deepClickHint') : isEnglishSource ? '' : t('translation.sourceClickHint')}
                </span>
              )}
              <span className="ml-auto"><SpeakButton text={result} lang={langDir === 'en2zh' ? 'zh' : 'en'} variant="icon-sm" /></span>
            </div>
            <div className="text-sm leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text-secondary)' }}>
              {resultDisplay}
              {loading && <span className="inline-block w-2 h-4 ml-0.5 bg-emerald-400/60 animate-pulse" />}
            </div>
          </div>

          {/* Grammar analysis (deep mode only) */}
          {mode === 'deep' && (
            <>
              {grammar && (
                <div className="rounded-xl border p-3 flex flex-col gap-3" style={{ borderColor: 'var(--panel-border)' }}>
                  <div className="text-2xs font-medium flex items-center gap-1" style={{ color: 'var(--text-dim)' }}>
                    <Icon icon="fa-solid fa-diagram-project text-[8px]" />{t('translation.grammarTitle')}
                    {grammar.difficulty && (
                      <span className="px-1 py-0.5 rounded text-[8px] ml-auto"
                        style={{ background: 'rgba(250,204,21,0.1)', color: '#eab308' }}>
                        {grammar.difficulty}
                      </span>
                    )}
                  </div>

                  {/* Overview row */}
                  <div className="flex flex-wrap gap-2 text-xs">
                    {grammar.tenses && grammar.tenses.length > 0 && (
                      <span className="px-2 py-0.5 rounded" style={{ background: 'rgba(96,165,250,0.08)', color: 'var(--brand)' }}>
                        {t('translation.grammarTensesFmt', { value: grammar.tenses.join(', ') })}
                      </span>
                    )}
                    {grammar.voice && (
                      <span className="px-2 py-0.5 rounded" style={{ background: 'rgba(52,211,153,0.08)', color: '#34d399' }}>
                        {t('translation.grammarVoiceFmt', { value: grammar.voice === 'active' ? t('translation.grammarVoiceActive') : grammar.voice === 'passive' ? t('translation.grammarVoicePassive') : grammar.voice })}
                      </span>
                    )}
                    {grammar.mood && (
                      <span className="px-2 py-0.5 rounded" style={{ background: 'rgba(168,85,247,0.08)', color: '#a855f7' }}>
                        {t('translation.grammarMoodFmt', { value: grammar.mood })}
                      </span>
                    )}
                  </div>

                  {/* Structure */}
                  {grammar.structure && (
                    <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
                      <span className="font-medium" style={{ color: 'var(--text-dim)' }}>{t('translation.grammarStructureLabel')}</span>
                      {grammar.structure}
                    </div>
                  )}

                  {/* Parts */}
                  {grammar.parts && grammar.parts.length > 0 && (
                    <div className="space-y-1">
                      <div className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('translation.grammarPartsTitle')}</div>
                      <div className="flex flex-wrap gap-1">
                        {grammar.parts.map((p, i) => (
                          <span key={i} className="px-2 py-0.5 rounded text-2xs border"
                            style={{ borderColor: 'var(--panel-border)', color: 'var(--text-secondary)' }}
                            title={p.description}>
                            <span style={{ color: 'var(--text-primary)' }}>{p.text}</span>
                            <span className="ml-1 opacity-50">({p.role})</span>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Clauses */}
                  {grammar.clauses && grammar.clauses.length > 1 && (
                    <div className="space-y-1">
                      <div className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('translation.grammarClausesTitle')}</div>
                      {grammar.clauses.map((c, i) => (
                        <div key={i} className="flex items-start gap-2 text-xs">
                          <span className="px-1 py-0.5 rounded text-[8px] shrink-0"
                            style={{ background: 'rgba(96,165,250,0.08)', color: 'var(--brand)' }}>
                            {c.type}
                          </span>
                          <span style={{ color: 'var(--text-secondary)' }}>{c.text}</span>
                          <span className="ml-auto shrink-0" style={{ color: 'var(--text-muted)' }}>{c.role}</span>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Grammar points */}
                  {grammar.grammarPoints && grammar.grammarPoints.length > 0 && (
                    <div className="space-y-1.5">
                      <div className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('translation.grammarPointsTitle')}</div>
                      {grammar.grammarPoints.map((gp, i) => (
                        <div key={i} className="px-2 py-1.5 rounded" style={{ background: 'rgba(250,204,21,0.04)' }}>
                          <div className="text-xs font-medium" style={{ color: '#eab308' }}>{gp.pattern}</div>
                          <div className="text-2xs mt-0.5" style={{ color: 'var(--text-muted)' }}>{gp.explanation}</div>
                          {gp.example && (
                            <div className="text-2xs mt-0.5 italic" style={{ color: 'var(--text-secondary)' }}>{t('translation.grammarExampleFmt', { value: gp.example })}</div>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}

          {/* Word extraction (learn/deep mode) */}
          {mode !== 'quick' && (
            <>
              {words.length > 0 && <WordCards words={words} quickSave />}
            </>
          )}
        </div>
      )}

      {/* Empty state */}
      {!result && !loading && history.length === 0 && (
        <div className="flex-1 flex items-center justify-center">
          <div className="flex flex-col items-center gap-2 text-center">
            <div className="text-2xl opacity-20"><Icon icon="fa-solid fa-language" /></div>
            <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {t('translation.emptyHint')}
            </div>
            <div className="flex items-center gap-3 mt-2 text-2xs" style={{ color: 'var(--text-muted)' }}>
              <span>{t('translation.emptyQuickHint')}</span>
              <span>{t('translation.emptyLearnHint')}</span>
              <span>{t('translation.emptyDeepHint')}</span>
            </div>
          </div>
        </div>
      )}

      {/* Translation history */}
      <div className="border-t shrink-0" style={{ borderColor: 'var(--panel-border)' }}>
        {!showHistory ? (
          <button
            onClick={openHistory}
            className="flex items-center gap-1.5 w-full px-1 py-2 text-xs transition-colors hover:text-text-secondary"
            style={{ color: 'var(--text-muted)' }}
          >
            <Icon icon="fa-solid fa-clock-rotate-left text-2xs" />
            {t('translation.historyTitle')}
            {history.length > 0 && (
              <span className="text-2xs px-1.5 py-0.5 rounded ml-auto" style={{ background: 'rgba(255,255,255,0.04)' }}>
                {history.length}
              </span>
            )}
          </button>
        ) : (
          <div>
            <div className="flex items-center gap-2 px-1 py-2">
              <button
                onClick={() => setShowHistory(false)}
                className="text-xs transition-colors hover:text-text-secondary"
                style={{ color: 'var(--text-muted)' }}
              >
                <Icon icon="fa-solid fa-chevron-up text-2xs" />
              </button>
              <span className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('translation.historyTitle')}</span>
              {history.length > 0 && (
                <button
                  onClick={() => { clearTranslationHistory(); reloadHistory() }}
                  className="text-[8px] ml-auto hover:text-red-400 transition-colors"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {t('translation.historyClear')}
                </button>
              )}
            </div>
            {history.length === 0 ? (
              <div className="text-center py-3 text-xs" style={{ color: 'var(--text-muted)' }}>{t('translation.historyEmpty')}</div>
            ) : (
              <div className="space-y-0.5 max-h-[200px] overflow-y-auto pb-2">
                {history.map(entry => (
                  <button
                    key={entry.id}
                    onClick={() => {
                      setSourceText(entry.sourceText)
                      setResult(entry.result)
                      setLangDir(entry.langDir as LangDir)
                      setMode(entry.mode as LearnMode)
                      setShowHistory(false)
                    }}
                    className="w-full text-left px-2 py-1.5 rounded-lg transition-all hover:bg-white/[0.04]"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-xs truncate flex-1" style={{ color: 'var(--text-secondary)' }}>
                        {entry.sourceText.slice(0, 40)}
                      </span>
                      <span className="text-[8px] px-1 py-0.5 rounded shrink-0" style={{ background: 'rgba(52,211,153,0.08)', color: '#34d399' }}>
                        {entry.langDir === 'en2zh' ? t('translation.historyEn2Zh') : t('translation.historyZh2En')}
                      </span>
                      <span className="text-[8px] px-1 py-0.5 rounded shrink-0" style={{ background: 'rgba(255,255,255,0.04)', color: 'var(--text-muted)' }}>
                        {historyModeLabel(entry.mode as LearnMode)}
                      </span>
                    </div>
                    <div className="text-xs mt-0.5 truncate" style={{ color: 'var(--text-muted)' }}>
                      → {entry.result.slice(0, 50)}
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Word detail panel */}
      {selectedWord && (
        <WordDetailPanel
          word={selectedWord}
          source={isEnglishSource ? 'en' : 'zh'}
          target={isEnglishSource ? 'zh' : 'en'}
          onClose={() => setSelectedWord(null)}
        />
      )}
    </div>
  )
}

export default TranslationModule
