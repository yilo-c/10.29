import React, { useState, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import { getWsClient } from '@desk-notes/shared'
import { swallowError } from '../../utils/swallowError'

export interface WordDetail {
  word: string
  phonetic: string
  phonetic_us: string
  phonetic_uk: string
  meanings: string[]
  synonyms: { word: string; similarity: string; nuance: string }[]
  antonyms: { word: string; note: string }[]
  collocations: { phrase: string; meaning: string }[]
  examples: string[]
  level: string
}

interface Props {
  word: string
  source?: string
  target?: string
  onClose: () => void
  onSave?: (word: string, meaning: string) => void
}

const LEVEL_LABEL_KEY: Record<string, string> = {
  A1: 'wordDetail.levelA1', A2: 'wordDetail.levelA2', B1: 'wordDetail.levelB1',
  B2: 'wordDetail.levelB2', C1: 'wordDetail.levelC1', C2: 'wordDetail.levelC2',
}

const WordDetailPanel: React.FC<Props> = ({ word, source = 'en', target = 'zh', onClose, onSave }) => {
  const { t } = useTranslation()
  const [detail, setDetail] = useState<WordDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (!word) return
    let cancelled = false
    const fetchDetail = async () => {
      setLoading(true)
      setError(null)
      try {
        const client = getWsClient()
        if (client.status !== 'connected') {
          setError(t('wordDetail.backendDisconnected'))
          return
        }
        const result = await client.call<WordDetail>('translate.word_detail', {
          word, source, target,
        })
        if (!cancelled) {
          const errMsg = (result as unknown as Record<string, unknown>).error
        if (errMsg) {
            setError(String(errMsg))
          } else {
            setDetail(result)
          }
        }
      } catch {
        if (!cancelled) setError(t('wordDetail.fetchFailed'))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetchDetail()
    return () => { cancelled = true }
  }, [word, source, target])

  const handleSave = useCallback(async () => {
    if (!detail || saved) return
    const meaning = detail.meanings[0] || detail.word
    try {
      const client = getWsClient()
      const wordbooks = await client.call<Array<{ id: number; name: string }>>('wordbook.list', {})
      let wbId: number
      if (wordbooks && wordbooks.length > 0) {
        wbId = wordbooks[0].id
      } else {
        const created = await client.call<{ id: number }>('wordbook.create', { name: t('wordDetail.defaultWordbook') })
        wbId = created.id
      }
      await client.call('word.add', { wordbook_id: wbId, word: detail.word, meaning })
      setSaved(true)
      onSave?.(detail.word, meaning)
    } catch (e) { /* ignore */ swallowError('WordDetailPanel:84', e) }
  }, [detail, saved, onSave])

  return (
    <div
      className="fixed inset-0 z-[99999] flex justify-end"
      style={{ background: 'rgba(0,0,0,0.3)' }}
      onClick={onClose}
    >
      <div
        className="w-[340px] max-w-[90vw] h-full overflow-y-auto border-l shadow-2xl animate-slide-in"
        style={{
          background: 'var(--panel-bg)',
          borderColor: 'var(--panel-border)',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b sticky top-0 z-10"
          style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg)' }}>
          <span className="text-xs font-medium" style={{ color: 'var(--text-dim)' }}>
            {t('wordDetail.title')}
          </span>
          <button onClick={onClose} className="w-6 h-6 flex items-center justify-center rounded text-sm hover:bg-white/[0.06]"
            style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="p-4 flex flex-col gap-4">
          {loading ? (
            <div className="flex items-center justify-center py-12 gap-2">
              <div className="w-4 h-4 border-2 border-purple-400/30 border-t-purple-400 rounded-full animate-spin" />
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('wordDetail.loading')}</span>
            </div>
          ) : error ? (
            <div className="text-center py-8 text-xs" style={{ color: 'var(--text-muted)' }}>{error}</div>
          ) : detail ? (
            <>
              {/* Word + Phonetic */}
              <div className="flex flex-col items-center gap-1">
                <div className="flex items-center gap-2">
                  <h2 className="text-xl font-bold" style={{ color: 'var(--text-primary)' }}>
                    {detail.word}
                  </h2>
                  {detail.level && LEVEL_LABEL_KEY[detail.level] && (
                    <span className="px-1.5 py-0.5 rounded text-2xs font-medium"
                      style={{ background: 'rgba(168,85,247,0.12)', color: '#a855f7' }}>
                      {t(LEVEL_LABEL_KEY[detail.level])} {detail.level}
                    </span>
                  )}
                </div>
                {(detail.phonetic_us || detail.phonetic_uk || detail.phonetic) && (
                  <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--text-muted)' }}>
                    {detail.phonetic_uk && <span>🇬🇧 /{detail.phonetic_uk}/</span>}
                    {detail.phonetic_us && <span>🇺🇸 /{detail.phonetic_us}/</span>}
                    {!detail.phonetic_uk && !detail.phonetic_us && detail.phonetic && (
                      <span>/{detail.phonetic}/</span>
                    )}
                  </div>
                )}
              </div>

              {/* Meanings */}
              {detail.meanings.length > 0 && (
                <section>
                  <div className="text-2xs font-medium mb-2 flex items-center gap-1" style={{ color: 'var(--text-dim)' }}>
                    <Icon icon="fa-solid fa-book-open text-[8px]" />{t('wordDetail.meaning')}
                  </div>
                  <div className="space-y-1">
                    {detail.meanings.map((m, i) => (
                      <div key={i} className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                        {m}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Synonyms */}
              {detail.synonyms.length > 0 && (
                <section>
                  <div className="text-2xs font-medium mb-2 flex items-center gap-1" style={{ color: 'var(--text-dim)' }}>
                    <Icon icon="fa-solid fa-rotate text-[8px]" />{t('wordDetail.synonyms')}
                  </div>
                  <div className="space-y-1.5">
                    {detail.synonyms.map((s, i) => (
                      <div key={i} className="flex items-center gap-2 px-2 py-1 rounded" style={{ background: 'rgba(96,165,250,0.04)' }}>
                        <span className="text-xs font-medium" style={{ color: 'var(--brand)' }}>{s.word}</span>
                        <span className="text-[8px] px-1 py-0.5 rounded"
                          style={{
                            background: s.similarity === 'high' ? 'rgba(34,197,94,0.12)' : s.similarity === 'medium' ? 'rgba(250,204,21,0.12)' : 'rgba(255,255,255,0.06)',
                            color: s.similarity === 'high' ? 'var(--success)' : s.similarity === 'medium' ? '#eab308' : 'var(--text-muted)',
                          }}>
                          {s.similarity === 'high' ? t('wordDetail.similarityHigh') : s.similarity === 'medium' ? t('wordDetail.similarityMedium') : s.similarity === 'low' ? t('wordDetail.similarityLow') : s.similarity}
                        </span>
                        {s.nuance && (
                          <span className="text-2xs ml-auto" style={{ color: 'var(--text-muted)' }}>{s.nuance}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Antonyms */}
              {detail.antonyms.length > 0 && (
                <section>
                  <div className="text-2xs font-medium mb-2 flex items-center gap-1" style={{ color: 'var(--text-dim)' }}>
                    <Icon icon="fa-solid fa-arrows-left-right text-[8px]" />{t('wordDetail.antonyms')}
                  </div>
                  <div className="space-y-1.5">
                    {detail.antonyms.map((a, i) => (
                      <div key={i} className="flex items-center gap-2 px-2 py-1 rounded" style={{ background: 'rgba(239,68,68,0.04)' }}>
                        <span className="text-xs font-medium" style={{ color: '#f87171' }}>{a.word}</span>
                        {a.note && (
                          <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{a.note}</span>
                        )}
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Collocations */}
              {detail.collocations.length > 0 && (
                <section>
                  <div className="text-2xs font-medium mb-2 flex items-center gap-1" style={{ color: 'var(--text-dim)' }}>
                    <Icon icon="fa-solid fa-link text-[8px]" />{t('wordDetail.collocations')}
                  </div>
                  <div className="space-y-1.5">
                    {detail.collocations.map((c, i) => (
                      <div key={i} className="flex items-baseline gap-2 px-2 py-1 rounded" style={{ background: 'rgba(34,197,94,0.04)' }}>
                        <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>{c.phrase}</span>
                        <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{c.meaning}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Examples */}
              {detail.examples.length > 0 && (
                <section>
                  <div className="text-2xs font-medium mb-2 flex items-center gap-1" style={{ color: 'var(--text-dim)' }}>
                    <Icon icon="fa-solid fa-quote-right text-[8px]" />{t('wordDetail.examples')}
                  </div>
                  <div className="space-y-2">
                    {detail.examples.map((e, i) => (
                      <div key={i} className="text-xs leading-relaxed px-2 py-1 rounded" style={{ background: 'rgba(255,255,255,0.02)' }}>
                        <div style={{ color: 'var(--text-secondary)' }}>{e}</div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Save button */}
              <button
                onClick={handleSave}
                disabled={saved}
                className="flex items-center justify-center gap-1.5 w-full py-2 rounded-lg text-xs font-medium transition-all disabled:opacity-40"
                style={{ background: saved ? 'rgba(34,197,94,0.08)' : 'rgba(168,85,247,0.12)', color: saved ? 'var(--success)' : '#a855f7' }}
              >
                <Icon icon={`fa-solid ${saved ? 'fa-check' : 'fa-plus'} text-2xs`} />
                {saved ? t('wordDetail.saved') : t('wordDetail.addToWordbook')}
              </button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  )
}

export default WordDetailPanel
