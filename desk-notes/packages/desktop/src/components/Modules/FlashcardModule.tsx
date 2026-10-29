import React, { useState, useEffect, useCallback, useMemo } from 'react'
import Icon from '../Common/Icon'

import { getWsClient, useTranslation } from '@desk-notes/shared'
import { swallowError } from '../../utils/swallowError'
import { errorStore } from '../../store/errorStore'
import { SUBJECT_CONFIG, QUALITY_CONFIG, PRESET_DECKS } from './FlashcardData'

// ── Types ──

interface Deck {
  id: number
  name: string
  description?: string
  subject?: string
  cardCount: number
  dueCount: number
}

interface Card {
  id: number
  word: string
  meaning: string
  example: string
  tags?: string[]
  source?: string
  repetitions: number
  efactor: number
  interval: number
  next_review?: string
  last_review?: string
  wordbook_id: number
}

// ── Component ──

const FlashcardModule: React.FC = () => {
  const { t } = useTranslation()
  const [decks, setDecks] = useState<Deck[]>([])
  const [activeDeckId, setActiveDeckId] = useState<number | null>(null)
  const [cards, setCards] = useState<Card[]>([])
  const [selectedCard, setSelectedCard] = useState<Card | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editFront, setEditFront] = useState('')
  const [editBack, setEditBack] = useState('')
  const [editExample, setEditExample] = useState('')
  const [editTags, setEditTags] = useState('')
  const [editId, setEditId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)

  // Deck management
  const [showNewDeck, setShowNewDeck] = useState(false)
  const [newDeckName, setNewDeckName] = useState('')
  const [newDeckSubject, setNewDeckSubject] = useState('general')
  const [showImportPreset, setShowImportPreset] = useState(false)
  const [importing, setImporting] = useState(false)

  // Review mode
  const [reviewMode, setReviewMode] = useState(false)
  const [reviewCards, setReviewCards] = useState<Card[]>([])
  const [reviewIndex, setReviewIndex] = useState(0)
  const [reviewFlipped, setReviewFlipped] = useState(false)
  const [reviewStats, setReviewStats] = useState({ reviewed: 0, totalDue: 0 })
  const [searchKeyword, setSearchKeyword] = useState('')

  // Subject labels from i18n
  const subjectLabels: Record<string, string> = useMemo(() => ({
    politics: t('flashcard.subjectPolitics'),
    english: t('flashcard.subjectEnglish'),
    math: t('flashcard.subjectMath'),
    mechanics: t('flashcard.subjectMechanics'),
    agriculture: t('flashcard.subjectAgriculture'),
    general: t('flashcard.subjectGeneral'),
  }), [t])

  // Quality labels from i18n
  const qualityLabels = useMemo(() => [
    t('flashcard.quality0'),
    t('flashcard.quality1'),
    t('flashcard.quality2'),
    t('flashcard.quality3'),
    t('flashcard.quality4'),
    t('flashcard.quality5'),
  ], [t])

  // ── Data loading ──

  const callRpc = useCallback(async <T,>(method: string, params?: Record<string, unknown>): Promise<T> => {
    const client = getWsClient()
    if (client.status !== 'connected') {
      throw new Error('backend disconnected')
    }
    return client.call<T>(method, params || {})
  }, [])

  const loadDecks = useCallback(async () => {
    try {
      const wblist = await callRpc<Array<{ id: number; name: string; description?: string }>>('wordbook.list')
      const withCounts: Deck[] = []
      for (const wb of wblist) {
        const allCards = await callRpc<Card[]>('word.list', { wordbook_id: wb.id })
        const dueCards = await callRpc<Card[]>('word.list_due', { wordbook_id: wb.id })
        const subject = wb.description && wb.description in SUBJECT_CONFIG ? wb.description : 'general'
        withCounts.push({
          ...wb,
          subject,
          cardCount: allCards.length,
          dueCount: dueCards.length,
        })
      }
      setDecks(withCounts)
      if (withCounts.length > 0 && !activeDeckId) {
        setActiveDeckId(withCounts[0].id)
      }
    } catch {
      setError(t('flashcard.loadDecksFailed'))
    }
  }, [callRpc, activeDeckId, t])

  const loadCards = useCallback(async () => {
    if (!activeDeckId) return
    setLoading(true)
    setError(null)
    try {
      const list = await callRpc<Card[]>('word.list', { wordbook_id: activeDeckId })
      setCards(list)
      setSelectedCard(null)
      clearForm()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('flashcard.loadCardsFailed'))
    } finally {
      setLoading(false)
    }
  }, [activeDeckId, callRpc, t])

  useEffect(() => { loadDecks() }, [])
  useEffect(() => { if (activeDeckId) loadCards() }, [activeDeckId])

  // ── Form ──

  const clearForm = () => {
    setEditId(null)
    setEditFront('')
    setEditBack('')
    setEditExample('')
    setEditTags('')
  }

  const handleSelectCard = (card: Card) => {
    setSelectedCard(card)
    setEditId(card.id)
    setEditFront(card.word)
    setEditBack(card.meaning || '')
    setEditExample(card.example || '')
    setEditTags((card.tags || []).join(', '))
  }

  const handleSave = async () => {
    if (!editFront.trim() || !activeDeckId) return
    setSaving(true)
    setError(null)
    try {
      if (editId) {
        await callRpc('word.update', {
          word_id: editId,
          word: editFront.trim(),
          meaning: editBack.trim(),
          example: editExample.trim(),
        })
      } else {
        await callRpc('word.add', {
          wordbook_id: activeDeckId,
          word: editFront.trim(),
          meaning: editBack.trim(),
          example: editExample.trim(),
        })
      }
      errorStore.success(t('flashcard.saved'))
      await loadCards()
      loadDecks()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('flashcard.saveFailed'))
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!editId) return
    if (!window.confirm(t('flashcard.deleteCardPrompt'))) return
    try {
      await callRpc('word.delete', { word_id: editId })
      errorStore.success(t('flashcard.deleted'))
      await loadCards()
      loadDecks()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('flashcard.deleteFailed'))
    }
  }

  // ── Deck ops ──

  const handleCreateDeck = async () => {
    if (!newDeckName.trim()) return
    try {
      await callRpc('wordbook.create', { name: newDeckName.trim(), description: newDeckSubject })
      setNewDeckName('')
      setShowNewDeck(false)
      setShowImportPreset(false)
      await loadDecks()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('flashcard.createFailed'))
    }
  }

  const handleDeleteDeck = async (deckId: number) => {
    const deck = decks.find(d => d.id === deckId)
    if (!deck) return
    if (!window.confirm(t('flashcard.deleteDeckPromptFmt', { name: deck.name }))) return
    try {
      await callRpc('wordbook.delete', { wordbook_id: deckId })
      if (activeDeckId === deckId) {
        setActiveDeckId(null)
        setCards([])
        setSelectedCard(null)
        clearForm()
      }
      await loadDecks()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('flashcard.deleteFailed'))
    }
  }

  const handleImportPreset = async (presetIdx: number) => {
    if (!activeDeckId) return
    setImporting(true)
    const preset = PRESET_DECKS[presetIdx]
    if (!preset) return
    try {
      const result = await callRpc<{ imported: number; skipped: number }>('wordbook.import_batch', {
        wordbook_id: activeDeckId,
        cards: preset.cards,
      })
      errorStore.success(t('flashcard.importDoneFmt', { count: result.imported }))
      setShowImportPreset(false)
      await loadCards()
      loadDecks()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('flashcard.importFailed'))
    } finally {
      setImporting(false)
    }
  }

  // ── Review ──

  const startReview = useCallback(async () => {
    if (!activeDeckId) return
    try {
      const due = await callRpc<Card[]>('word.list_due', { wordbook_id: activeDeckId, limit: 100 })
      if (due.length === 0) {
        errorStore.info(t('flashcard.noDueCardsMsg'))
        return
      }
      setReviewCards(due)
      setReviewIndex(0)
      setReviewFlipped(false)
      setReviewStats({ reviewed: 0, totalDue: due.length })
      setReviewMode(true)
    } catch {
      setError(t('flashcard.loadReviewFailed'))
    }
  }, [activeDeckId, callRpc, t])

  const handleReviewQuality = async (quality: number) => {
    const card = reviewCards[reviewIndex]
    if (!card) return
    setError(null)
    try {
      await callRpc('word.review', { word_id: card.id, quality })
    } catch (e) {
      // review update is non-critical
      swallowError('FlashcardModule:342', e)
    }
    const nextIdx = reviewIndex + 1
    setReviewStats(s => ({ ...s, reviewed: s.reviewed + 1 }))
    if (nextIdx >= reviewCards.length) {
      setReviewMode(false)
      loadDecks() // refresh due counts
    } else {
      setReviewIndex(nextIdx)
      setReviewFlipped(false)
    }
  }

  // ── Render helpers ──

  const activeDeck = decks.find(d => d.id === activeDeckId)
  const subjCfg = SUBJECT_CONFIG[activeDeck?.subject || 'general']

  const filteredCards = searchKeyword.trim()
    ? cards.filter(c =>
        c.word.toLowerCase().includes(searchKeyword.toLowerCase()) ||
        c.meaning.toLowerCase().includes(searchKeyword.toLowerCase())
      )
    : cards

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Deck tabs bar */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-white/[0.04] flex-shrink-0 overflow-x-auto">
        {decks.map(d => {
          const ds = SUBJECT_CONFIG[d.subject || 'general']
          const isActive = activeDeckId === d.id
          return (
            <button
              key={d.id}
              onClick={() => { setActiveDeckId(d.id); setReviewMode(false) }}
              className={`relative flex items-center gap-1 px-2.5 py-1 rounded text-xs transition-all flex-shrink-0 whitespace-nowrap ${
                isActive ? 'bg-fluent-blue/15 text-fluent-blue font-medium' : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]'
              }`}
            >
              <span className="text-2xs" style={{ color: isActive ? undefined : ds.color }}>{d.name}</span>
              {d.dueCount > 0 && (
                <span className="px-1 py-0.5 rounded text-2xs font-medium" style={{ background: 'rgba(234,179,8,0.15)', color: '#eab308' }}>
                  {d.dueCount}
                </span>
              )}
            </button>
          )
        })}
        <button
          onClick={() => { setShowNewDeck(true); setShowImportPreset(false) }}
          className="px-2 py-1 rounded text-xs text-text-dim hover:text-text-secondary hover:bg-white/[0.06] flex-shrink-0"
        >
          <Icon icon="fa-solid fa-plus text-xs" />
        </button>
        {activeDeckId && (
          <>
            <button
              onClick={() => setShowImportPreset(!showImportPreset)}
              className="px-2 py-1 rounded text-xs text-purple-400/60 hover:text-purple-400 hover:bg-white/[0.06] flex-shrink-0"
              title={t('flashcard.importPresetTitle')}
            >
              <Icon icon="fa-solid fa-download text-2xs mr-0.5" />{t('flashcard.importBtn')}
            </button>
            <button
              onClick={handleDeleteDeck.bind(null, activeDeckId)}
              className="px-1.5 py-1 rounded text-xs text-red-400/40 hover:text-red-400 hover:bg-white/[0.06] flex-shrink-0"
              title={t('flashcard.deleteDeckTitle')}
            >
              <Icon icon="fa-solid fa-trash-can text-2xs" />
            </button>
          </>
        )}
        {/* Review button */}
        {activeDeckId && (
          <button onClick={reviewMode ? () => setReviewMode(false) : startReview}
            className={`ml-auto px-2 py-1 rounded text-xs font-medium transition-all flex-shrink-0 ${
              reviewMode ? 'bg-amber-400/12 text-amber-400' : 'text-text-dim hover:text-amber-400/70 hover:bg-white/[0.04]'
            }`}>
            <Icon icon={`fa-solid ${reviewMode ? 'fa-xmark' : 'fa-brain'} text-2xs mr-1`} />
            {reviewMode
              ? t('flashcard.exitReview')
              : activeDeck?.dueCount
                ? t('flashcard.reviewBtnFmt', { count: activeDeck.dueCount })
                : t('flashcard.reviewBtn')}
          </button>
        )}
      </div>

      {/* New deck dialog */}
      {showNewDeck && (
        <div className="px-3 py-2 border-b border-white/[0.04] flex items-center gap-2 flex-shrink-0">
          <input
            value={newDeckName}
            onChange={e => setNewDeckName(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') handleCreateDeck(); if (e.key === 'Escape') setShowNewDeck(false) }}
            placeholder={t('flashcard.deckNamePlaceholder')}
            className="px-2 py-1 rounded text-xs bg-white/[0.08] border border-white/10 outline-none w-[120px]"
            style={{ color: 'var(--text-primary)' }}
            autoFocus
          />
          <select
            value={newDeckSubject}
            onChange={e => setNewDeckSubject(e.target.value)}
            className="px-2 py-1 rounded text-xs bg-white/[0.08] border border-white/10 outline-none"
            style={{ color: 'var(--text-primary)' }}
          >
            {Object.entries(SUBJECT_CONFIG).map(([k, v]) => (
              <option key={k} value={k}><Icon icon={v.icon} /> {subjectLabels[k]}</option>
            ))}
          </select>
          <button onClick={handleCreateDeck} className="px-2 py-1 rounded text-xs text-fluent-blue hover:text-fluent-blue/80">
            <Icon icon="fa-solid fa-check" />
          </button>
          <button onClick={() => setShowNewDeck(false)} className="px-2 py-1 rounded text-xs text-text-dim hover:text-text-secondary">
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>
      )}

      {/* Import preset dialog */}
      {showImportPreset && (
        <div className="px-3 py-2 border-b border-white/[0.04] flex flex-col gap-1.5 flex-shrink-0">
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {t('flashcard.presetDialogFmt', { name: activeDeck?.name || '' })}
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            {PRESET_DECKS.map((preset, i) => {
              const ps = SUBJECT_CONFIG[preset.subject]
              return (
                <button
                  key={i}
                  onClick={() => handleImportPreset(i)}
                  disabled={importing}
                  className="flex items-center gap-1 px-2 py-1 rounded text-xs transition-all disabled:opacity-40 hover:bg-white/[0.06]"
                  style={{ color: ps?.color, border: `1px solid ${ps?.color}20` }}
                >
                  <Icon icon={`${ps?.icon} text-[8px]`} />
                  {t('flashcard.presetCardCountFmt', { name: preset.name, count: preset.cards.length })}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Deck stats bar */}
      {activeDeck && (
        <div className="flex items-center gap-3 px-3 py-1.5 flex-shrink-0 text-xs" style={{ color: 'var(--text-muted)' }}>
          <span style={{ color: subjCfg?.color }}>
            <Icon icon={`${subjCfg?.icon} text-[8px] mr-1`} />{subjectLabels[activeDeck?.subject || 'general']}
          </span>
          <span>{t('flashcard.cardCountFmt', { count: activeDeck.cardCount })}</span>
          <span style={{ color: activeDeck.dueCount > 0 ? '#eab308' : undefined }}>
            {t('flashcard.dueCountFmt', { count: activeDeck.dueCount })}
          </span>
        </div>
      )}

      {/* Review mode */}
      {reviewMode && reviewCards.length > 0 && (() => {
        const card = reviewCards[reviewIndex]
        const total = reviewCards.length
        const progress = reviewStats.reviewed
        return (
          <div className="flex-1 flex flex-col items-center justify-center gap-4 p-4 min-h-0">
            {/* Progress */}
            <div className="flex items-center gap-2 w-full max-w-[320px]">
              <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{progress}/{total}</span>
              <div className="flex-1 h-1 rounded-full" style={{ background: 'rgba(255,255,255,0.06)' }}>
                <div className="h-full rounded-full transition-all" style={{ background: '#eab308', width: `${(progress/total)*100}%` }} />
              </div>
            </div>

            {/* Card */}
            <div
              onClick={() => setReviewFlipped(!reviewFlipped)}
              className="w-full max-w-[320px] min-h-[180px] flex flex-col items-center justify-center rounded-xl border cursor-pointer transition-all p-6 gap-3 select-none"
              style={{
                borderColor: reviewFlipped ? 'rgba(234,179,8,0.3)' : 'var(--panel-border)',
                background: reviewFlipped ? 'rgba(234,179,8,0.04)' : 'rgba(255,255,255,0.02)',
              }}
            >
              {!reviewFlipped ? (
                <>
                  <div className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('flashcard.clickToFlip')}</div>
                  <div className="text-base font-semibold text-center leading-relaxed" style={{ color: 'var(--text-primary)' }}>
                    {card?.word}
                  </div>
                </>
              ) : (
                <>
                  <div className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('flashcard.answerLabel')}</div>
                  <div className="text-sm text-center leading-relaxed" style={{ color: 'var(--text-primary)' }}>
                    {card?.meaning}
                  </div>
                  {card?.example && (
                    <div className="text-xs italic px-3 py-1.5 rounded mt-2" style={{ background: 'rgba(255,255,255,0.03)', color: 'var(--text-muted)' }}>
                      {card.example}
                    </div>
                  )}
                </>
              )}
            </div>

            {/* SM-2 quality buttons */}
            {reviewFlipped && (
              <div className="flex flex-wrap items-center justify-center gap-1.5 max-w-[340px]">
                {QUALITY_CONFIG.map(btn => (
                  <button
                    key={btn.q}
                    onClick={() => handleReviewQuality(btn.q)}
                    className="px-2.5 py-1.5 rounded-lg text-xs font-medium transition-all hover:scale-105"
                    style={{ background: `${btn.color}18`, color: btn.color, border: `1px solid ${btn.color}30` }}
                  >
                    {btn.q} · {qualityLabels[btn.q]}
                  </button>
                ))}
              </div>
            )}
          </div>
        )
      })()}

      {/* Browse mode */}
      {!reviewMode && (
        <div className="flex-1 flex min-h-0">
          {/* Left: card list */}
          <div className="w-[200px] flex-shrink-0 flex flex-col border-r" style={{ borderColor: 'var(--panel-border)' }}>
            <div className="px-2 pt-2 pb-1">
              <input
                value={searchKeyword}
                onChange={e => setSearchKeyword(e.target.value)}
                placeholder={t('flashcard.searchPlaceholder')}
                className="w-full px-2 py-1 rounded text-xs bg-white/[0.06] border border-white/[0.06] outline-none placeholder-white/20"
                style={{ color: 'var(--text-primary)' }}
              />
            </div>
            <div className="flex-1 overflow-y-auto px-1 pb-1">
              {loading ? (
                <div className="flex items-center justify-center py-4">
                  <div className="w-3 h-3 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
                </div>
              ) : filteredCards.length === 0 ? (
                <div className="text-xs text-center py-6" style={{ color: 'var(--text-muted)' }}>
                  {cards.length === 0 ? t('flashcard.emptyGuide') : t('flashcard.noMatch')}
                </div>
              ) : (
                filteredCards.map(c => (
                  <button
                    key={c.id}
                    onClick={() => handleSelectCard(c)}
                    className={`w-full text-left px-2 py-1.5 rounded text-xs transition-all mb-0.5 ${
                      selectedCard?.id === c.id ? 'bg-fluent-blue/12 text-fluent-blue' : 'hover:bg-white/[0.04]'
                    }`}
                    style={{ color: selectedCard?.id === c.id ? undefined : 'var(--text-secondary)' }}
                  >
                    <span className="font-medium line-clamp-1">{c.word}</span>
                    <span className="ml-1 opacity-60 truncate block text-2xs line-clamp-1">{c.meaning}</span>
                    {c.next_review && c.next_review <= new Date().toISOString() && (
                      <span className="inline-block w-1.5 h-1.5 rounded-full mt-0.5" style={{ background: '#eab308' }} />
                    )}
                  </button>
                ))
              )}
            </div>
            <div className="px-2 py-1.5 border-t" style={{ borderColor: 'var(--panel-border)' }}>
              <button
                onClick={() => { setSelectedCard(null); clearForm() }}
                className="w-full py-1 rounded text-xs border border-dashed transition-all"
                style={{ borderColor: 'var(--panel-border)', color: 'var(--text-muted)' }}
              >
                <Icon icon="fa-solid fa-plus mr-1" />{t('flashcard.addCardBtn')}
              </button>
            </div>
          </div>

          {/* Right: card detail / edit */}
          <div className="flex-1 flex flex-col min-h-0 p-3 overflow-y-auto">
            {error && (
              <div className="mb-2 px-2 py-1 rounded text-xs bg-red-500/10 text-red-400">
                {error}
                <button onClick={() => setError(null)} className="float-right"><Icon icon="fa-solid fa-xmark" /></button>
              </div>
            )}

            {selectedCard || editId === null ? (
              <div className="flex flex-col gap-2">
                <div>
                  <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--text-muted)' }}>{t('flashcard.frontLabel')}</label>
                  <textarea
                    value={editFront}
                    onChange={e => setEditFront(e.target.value)}
                    placeholder={t('flashcard.frontPlaceholder')}
                    rows={2}
                    className="w-full px-2.5 py-1.5 rounded text-sm bg-white/[0.06] border border-white/[0.06] outline-none resize-none placeholder-white/20"
                    style={{ color: 'var(--text-primary)' }}
                  />
                </div>
                <div>
                  <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--text-muted)' }}>{t('flashcard.backLabel')}</label>
                  <textarea
                    value={editBack}
                    onChange={e => setEditBack(e.target.value)}
                    placeholder={t('flashcard.backPlaceholder')}
                    rows={4}
                    className="w-full px-2.5 py-1.5 rounded text-sm bg-white/[0.06] border border-white/[0.06] outline-none resize-none placeholder-white/20"
                    style={{ color: 'var(--text-primary)' }}
                  />
                </div>
                <div>
                  <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--text-muted)' }}>{t('flashcard.hintLabel')}</label>
                  <textarea
                    value={editExample}
                    onChange={e => setEditExample(e.target.value)}
                    placeholder={t('flashcard.hintPlaceholder')}
                    rows={2}
                    className="w-full px-2.5 py-1.5 rounded text-sm bg-white/[0.06] border border-white/[0.06] outline-none resize-none placeholder-white/20"
                    style={{ color: 'var(--text-primary)' }}
                  />
                </div>
                <div>
                  <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--text-muted)' }}>{t('flashcard.tagsLabel')}</label>
                  <input
                    value={editTags}
                    onChange={e => setEditTags(e.target.value)}
                    placeholder={t('flashcard.tagsPlaceholder')}
                    className="w-full px-2.5 py-1.5 rounded text-xs bg-white/[0.06] border border-white/[0.06] outline-none placeholder-white/20"
                    style={{ color: 'var(--text-primary)' }}
                  />
                </div>
                {editId && (
                  <div className="flex items-center gap-2 text-2xs" style={{ color: 'var(--text-muted)' }}>
                    <span>{t('flashcard.reviewCountFmt', { count: selectedCard?.repetitions || 0 })}</span>
                    <span>{t('flashcard.intervalFmt', { days: selectedCard?.interval || 0 })}</span>
                    {selectedCard?.next_review && (
                      <span>{t('flashcard.nextReviewFmt', { date: new Date(selectedCard.next_review).toLocaleDateString('zh-CN') })}</span>
                    )}
                  </div>
                )}
                <div className="flex items-center gap-2 pt-1">
                  <button
                    onClick={handleSave}
                    disabled={saving || !editFront.trim()}
                    className="px-3 py-1 rounded text-xs bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 transition-all disabled:opacity-40"
                  >
                    {saving ? t('flashcard.saving') : t('flashcard.saveBtn')}
                  </button>
                  {editId && (
                    <button onClick={handleDelete}
                      className="px-3 py-1 rounded text-xs text-red-400 hover:bg-red-500/10 transition-all">
                      <Icon icon="fa-regular fa-trash-can mr-1" />{t('flashcard.deleteBtn')}
                    </button>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center">
                <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
                  {decks.length === 0 ? t('flashcard.emptyStateDeck') : t('flashcard.emptyStateSelect')}
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

export default FlashcardModule