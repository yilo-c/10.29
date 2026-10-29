import React, { useState, useEffect, useRef, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import type { Word, ReviewStats } from './types'
import { swallowError } from '../../utils/swallowError'

/** Single character diff entry returned by word.dictation_check */
interface DiffEntry {
  op: string // "=" | "+" | "-"
  ch: string
}

interface DictationResult {
  correct: boolean
  exact: boolean
  diff: DiffEntry[]
  expected: string
  actual: string
  suggested_quality: number
  suggested_rating: number
  review?: { repetitions: number; efactor: number; interval: number; next_review: string }
  review_error?: string
}

interface Props {
  reviewWords: Word[]
  reviewIndex: number
  reviewStats: ReviewStats
  onRate: (quality: number) => void
  onAdvance: () => void
  onExit: () => void
  callRpc: <T,>(method: string, params?: Record<string, unknown>, timeoutMs?: number) => Promise<T>
}

/** Render character-level diff with color coding */
const DiffDisplay: React.FC<{ diff: DiffEntry[] }> = ({ diff }) => (
  <div className="flex flex-wrap gap-0 justify-center">
    {diff.map((entry, i) => {
      const { op, ch } = entry
      if (op === '=') {
        return <span key={i} className="text-base font-bold" style={{ color: 'var(--success)' }}>{ch}</span>
      }
      if (op === '-') {
        // Expected char that user missed — show as underlined placeholder
        return (
          <span key={i} className="text-base font-bold relative" style={{ color: 'var(--danger)' }}>
            <span className="opacity-40">{ch}</span>
            <span className="absolute bottom-0 left-0 right-0 h-0.5" style={{ background: 'var(--danger)' }} />
          </span>
        )
      }
      // op === '+': extra char typed by user
      return (
        <span key={i} className="text-base font-bold line-through" style={{ color: 'var(--danger)' }}>
          {ch}
        </span>
      )
    })}
  </div>
)

const WordDictation: React.FC<Props> = ({ reviewWords, reviewIndex, reviewStats, onRate, onAdvance, onExit, callRpc }) => {
  const { t } = useTranslation()
  const currentWord = reviewWords[reviewIndex]
  const total = reviewWords.length
  const progress = reviewStats.remembered + reviewStats.forgotten
  const isDone = reviewIndex >= total

  const [input, setInput] = useState('')
  const [result, setResult] = useState<DictationResult | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const [autoPlayed, setAutoPlayed] = useState(false)

  // Auto-focus input on new word
  useEffect(() => {
    if (!isDone && !result) {
      setInput('')
      setResult(null)
      setAutoPlayed(false)
      setTimeout(() => inputRef.current?.focus(), 100)
    }
  }, [reviewIndex, isDone]) // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-play TTS on new word
  useEffect(() => {
    if (!isDone && !result && !autoPlayed && currentWord?.word) {
      setAutoPlayed(true)
      // Small delay to ensure audio context is ready
      const timer = setTimeout(() => {
        try {
          const utterance = new SpeechSynthesisUtterance(currentWord.word)
          utterance.lang = 'en-US'
          utterance.rate = 0.85
          speechSynthesis.speak(utterance)
        } catch (e) { /* TTS best-effort */ swallowError('WordDictation:92', e) }
      }, 300)
      return () => clearTimeout(timer)
    }
  }, [reviewIndex, isDone, result, autoPlayed, currentWord])

  // Keyboard: Enter to submit / Next
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (isDone) return
      if (e.repeat) return
      if (e.key === 'Enter') {
        e.preventDefault()
        if (result) {
          // Already checked — advance to next
          advanceToNext()
        } else if (input.trim()) {
          checkAnswer()
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  })

  const checkAnswer = useCallback(async () => {
    if (!currentWord || submitting) return
    setSubmitting(true)
    try {
      const res = await callRpc<DictationResult>('word.dictation_check', {
        word_id: currentWord.id,
        actual: input.trim(),
        auto_record: true,
      })
      setResult(res)
      // Update stats
      if (res.suggested_quality >= 3) {
        onRate(res.suggested_quality)
      } else {
        onRate(res.suggested_quality)
      }
    } catch {
      // Fallback: simple local comparison
      const correct = input.trim().toLowerCase() === currentWord.word.toLowerCase()
      const quality = correct ? 5 : 1
      onRate(quality)
      setResult({
        correct,
        exact: input.trim() === currentWord.word,
        diff: [],
        expected: currentWord.word,
        actual: input.trim(),
        suggested_quality: quality,
        suggested_rating: correct ? 4 : 1,
      })
    } finally {
      setSubmitting(false)
    }
  }, [currentWord, input, submitting, callRpc, onRate])

  const advanceToNext = useCallback(() => {
    // onRate was already called in checkAnswer; advance parent index
    onAdvance()
    setResult(null)
    setInput('')
    setAutoPlayed(false)
  }, [onAdvance])

  const replayTTS = useCallback(() => {
    if (!currentWord?.word) return
    try {
      const utterance = new SpeechSynthesisUtterance(currentWord.word)
      utterance.lang = 'en-US'
      utterance.rate = 0.85
      speechSynthesis.speak(utterance)
    } catch (e) { /* best-effort */ swallowError('WordDictation:167', e) }
  }, [currentWord])

  // ── Done screen ──
  if (isDone) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-4 p-4 min-h-0">
        <div className="w-full max-w-[280px] flex flex-col items-center gap-3">
          <div className="text-lg font-semibold" style={{ color: 'var(--text-primary)' }}>
            {t('wordDictation.done')}
          </div>
          <div className="flex items-center gap-4 text-base">
            <span style={{ color: 'var(--success)' }}>
              <Icon icon="fa-solid fa-check mr-1" />{t('wordDictation.correctFmt', { count: reviewStats.remembered })}
            </span>
            <span style={{ color: 'var(--danger)' }}>
              <Icon icon="fa-solid fa-xmark mr-1" />{t('wordDictation.misspelledFmt', { count: reviewStats.forgotten })}
            </span>
          </div>
          <div className="w-full h-1.5 rounded-full bg-white/[0.06]">
            <div className="h-full rounded-full bg-amber-400" style={{ width: '100%' }} />
          </div>
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {t('wordDictation.accuracyFmt', { percent: total > 0 ? Math.round((reviewStats.remembered / total) * 100) : 0 })}
          </div>
          <button onClick={onExit}
            className="px-4 py-1.5 rounded-lg text-xs font-medium transition-all mt-2"
            style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}>
            {t('wordDictation.finish')}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 flex flex-col items-center justify-center gap-4 p-4 min-h-0">
      {/* Progress bar */}
      <div className="flex items-center gap-2 w-full max-w-[280px]">
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {progress} / {total}
        </span>
        <div className="flex-1 h-1 rounded-full" style={{ background: 'rgba(255,255,255,0.06)' }}>
          <div className="h-full rounded-full transition-all" style={{ background: 'var(--warning)', width: `${(progress / total) * 100}%` }} />
        </div>
        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--success)' }}>
          <Icon icon="fa-solid fa-check text-[8px]" />{reviewStats.remembered}
        </span>
        <span className="text-xs flex items-center gap-1" style={{ color: 'var(--danger)' }}>
          <Icon icon="fa-solid fa-xmark text-[8px]" />{reviewStats.forgotten}
        </span>
      </div>

      {/* Prompt area */}
      <div className="w-full max-w-[280px] flex flex-col items-center gap-3">
        {/* Mode indicator + replay button */}
        <div className="flex items-center gap-2">
          <span className="text-2xs font-medium px-2 py-0.5 rounded"
            style={{ background: 'rgba(168,85,247,0.12)', color: '#c084fc' }}>
            <Icon icon="fa-solid fa-pen-fancy mr-1" />{t('wordDictation.modeLabel')}
          </span>
          <button onClick={replayTTS}
            className="w-6 h-6 flex items-center justify-center rounded transition-all hover:bg-white/[0.08]"
            title={t('wordDictation.replayTitle')}>
            <Icon icon="fa-solid fa-volume-high text-xs" style={{ color: 'var(--brand)' }} />
          </button>
        </div>

        {/* Meaning hint (Chinese → spell English) */}
        <div className="min-h-[80px] flex flex-col items-center justify-center gap-2 w-full rounded-xl border p-4"
          style={{
            borderColor: result
              ? result.correct ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'
              : 'var(--panel-border)',
            background: result
              ? result.correct ? 'rgba(34,197,94,0.04)' : 'rgba(239,68,68,0.04)'
              : 'rgba(255,255,255,0.02)',
          }}>
          {currentWord?.meaning && (
            <div className="text-sm font-medium text-center" style={{ color: 'var(--text-primary)' }}>
              {currentWord.meaning}
            </div>
          )}
          {currentWord?.phonetic && (
            <div className="text-xs" style={{ color: 'var(--text-tertiary)' }}>
              /{currentWord.phonetic}/
            </div>
          )}
          {/* Example as context (word masked) */}
          {currentWord?.example && !result && (
            <div className="text-2xs italic mt-1 text-center" style={{ color: 'var(--text-dim)' }}>
              {currentWord.example.replace(
                new RegExp(currentWord.word, 'gi'),
                '____'
              )}
            </div>
          )}
        </div>

        {/* Result feedback */}
        {result && (
          <div className="w-full flex flex-col items-center gap-2">
            {/* Diff display */}
            {result.diff.length > 0 && (
              <div className="px-4 py-2 rounded-lg" style={{ background: 'rgba(255,255,255,0.03)' }}>
                <DiffDisplay diff={result.diff} />
              </div>
            )}
            {/* Verdict */}
            <div className="text-xs font-medium" style={{
              color: result.correct ? 'var(--success)' : 'var(--danger)'
            }}>
              {result.exact
                ? t('wordDictation.perfect')
                : result.correct
                  ? t('wordDictation.correctCase')
                  : t('wordDictation.wrongAnswerFmt', { word: result.expected })}
            </div>
            {result.correct && !result.exact && (
              <div className="text-2xs" style={{ color: 'var(--text-dim)' }}>
                {t('wordDictation.yourInputFmt', { actual: result.actual })}
              </div>
            )}
          </div>
        )}

        {/* Input area */}
        {!result && (
          <div className="w-full flex gap-2">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && input.trim()) checkAnswer() }}
              placeholder={t('wordDictation.inputPlaceholder')}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              className="flex-1 px-3 py-2 rounded-lg text-base outline-none border transition-all"
              style={{
                background: 'rgba(255,255,255,0.04)',
                borderColor: 'rgba(255,255,255,0.1)',
                color: 'var(--text-primary)',
              }}
            />
            <button
              onClick={checkAnswer}
              disabled={!input.trim() || submitting}
              className="px-3 py-2 rounded-lg text-xs font-medium transition-all disabled:opacity-30"
              style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}>
              {submitting ? '...' : t('wordDictation.submit')}
            </button>
          </div>
        )}

        {/* Next word button after result */}
        {result && (
          <button
            onClick={advanceToNext}
            className="px-4 py-2 rounded-lg text-xs font-medium transition-all"
            style={{
              background: result.correct ? 'rgba(34,197,94,0.12)' : 'rgba(251,191,36,0.12)',
              color: result.correct ? 'var(--success)' : 'var(--warning)',
            }}>
            {reviewIndex + 1 >= total ? t('wordDictation.finish') : t('wordDictation.next')} <span className="opacity-50 ml-1">Enter</span>
          </button>
        )}
      </div>

      {/* Hint */}
      {!result && (
        <div className="text-[8px]" style={{ color: 'var(--text-dim)' }}>
          {t('wordDictation.hint')}
        </div>
      )}
    </div>
  )
}

export default WordDictation
