import React, { useState, useEffect, useCallback, useRef } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import { getWsClient } from '../../utils/ai'
import type { Wordbook, Word } from './types'
import WordReview from './WordReview'
import WordDictation from './WordDictation'
import WordListPanel from './WordListPanel'
import WordEditForm from './WordEditForm'
import { PRESET_VOCAB } from './WordbankPresets'
import { swallowError } from '../../utils/swallowError'
import { errorStore } from '../../store/errorStore'

type ReviewType = 'flip' | 'dictation'

const WordbankModule: React.FC = () => {
  const { t } = useTranslation()
  const [wordbooks, setWordbooks] = useState<Wordbook[]>([])
  const [activeWordbookId, setActiveWordbookId] = useState<number | null>(null)
  const [words, setWords] = useState<Word[]>([])
  const [searchKeyword, setSearchKeyword] = useState('')
  const [selectedWord, setSelectedWord] = useState<Word | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [wsStatus, setWsStatus] = useState<'connected' | 'disconnected'>('disconnected')

  // Edit form state
  const [editWord, setEditWord] = useState('')
  const [editMeaning, setEditMeaning] = useState('')
  const [editExample, setEditExample] = useState('')
  const [editExamples, setEditExamples] = useState<any[]>([])
  const [editId, setEditId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)
  const [generatingExamples, setGeneratingExamples] = useState(false)

  // Wordbook management
  const [showNewWordbook, setShowNewWordbook] = useState(false)
  const [newWordbookName, setNewWordbookName] = useState('')
  const [showWordbookMenu, setShowWordbookMenu] = useState<number | null>(null)
  const [renamingWordbook, setRenamingWordbook] = useState<number | null>(null)
  const [renameValue, setRenameValue] = useState('')

  // Review mode
  const [reviewMode, setReviewMode] = useState(false)
  const [reviewType, setReviewType] = useState<ReviewType>('flip')
  const [reviewIndex, setReviewIndex] = useState(0)
  const [reviewFlipped, setReviewFlipped] = useState(false)
  const [reviewStats, setReviewStats] = useState({ remembered: 0, forgotten: 0 })
  const [reviewWords, setReviewWords] = useState<Word[]>([])

  // Due only filter
  const [showDueOnly, setShowDueOnly] = useState(false)

  // Import preset
  const [showImport, setShowImport] = useState(false)
  const [importing, setImporting] = useState(false)

  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    try {
      const client = getWsClient()
      setWsStatus(client.status === 'connected' ? 'connected' : 'disconnected')
      return client.onStatusChange?.((s: string) => {
        setWsStatus(s === 'connected' ? 'connected' : 'disconnected')
      })
    } catch {
      setWsStatus('disconnected')
    }
  }, [])

  useEffect(() => { loadWordbooks() }, [])
  // Reload when WebSocket reconnects (after initial mount or reconnect)
  useEffect(() => { if (wsStatus === 'connected') loadWordbooks() }, [wsStatus])
  useEffect(() => { if (activeWordbookId) loadWords() }, [activeWordbookId, searchKeyword])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowWordbookMenu(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const callRpc = useCallback(async <T,>(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<T> => {
    const client = getWsClient()
    if (client.status !== 'connected') {
      setError(t('wordbank.backendDisconnected'))
      throw new Error('backend disconnected')
    }
    return client.call<T>(method, params || {}, timeoutMs)
  }, [t])

  const loadWordbooks = useCallback(async () => {
    try {
      const list = await callRpc<Wordbook[]>('wordbook.list')
      setWordbooks(list)
      if (list.length > 0 && !activeWordbookId) setActiveWordbookId(list[0].id)
    } catch (e) { /* silent */ swallowError('WordbankModule:99', e) }
  }, [callRpc, activeWordbookId])

  const loadWords = useCallback(async () => {
    if (!activeWordbookId) return
    setLoading(true)
    setError(null)
    try {
      const params: Record<string, unknown> = { wordbook_id: activeWordbookId }
      if (searchKeyword.trim()) params.keyword = searchKeyword.trim()
      const list = await callRpc<Word[]>('word.list', params)
      setWords(list)
      setSelectedWord(null)
      clearForm()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('wordbank.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [activeWordbookId, searchKeyword, callRpc, t])

  const handleImportPreset = async () => {
    if (!activeWordbookId) return
    setImporting(true)
    try {
      const result = await callRpc<{ imported: number; skipped: number }>('wordbook.import_batch', {
        wordbook_id: activeWordbookId, cards: PRESET_VOCAB,
      })
      errorStore.success(t('wordbank.importDoneFmt', { count: result.imported }))
      setShowImport(false)
      await loadWords()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('wordbank.importFailed'))
    } finally { setImporting(false) }
  }

  const [enriching, setEnriching] = useState(false)
  const [detailPopupWord, setDetailPopupWord] = useState<string | null>(null)
  const [detailPopupData, setDetailPopupData] = useState<any>(null)
  const [detailPopupLoading, setDetailPopupLoading] = useState(false)

  const handleWordClick = useCallback(async (word: string) => {
    setDetailPopupWord(word); setDetailPopupData(null); setDetailPopupLoading(true)
    try {
      const result = await callRpc<any>('translate.word_detail', { word })
      setDetailPopupData(result)
    } catch (e) { /* ignore */ swallowError('WordbankModule:147', e) }
    setDetailPopupLoading(false)
  }, [callRpc])

  const handleCloudEnrich = useCallback(async () => {
    if (!selectedWord || enriching) return
    setEnriching(true); setError(null)
    try {
      const result = await callRpc<any>('word.enrich_cloud', { word_id: selectedWord.id, word: selectedWord.word }, 180000)
      if (result?.success) {
        errorStore.success(t('wordbank.enrichSuccess'))
        await loadWords()
      } else {
        setError(result?.error || t('wordbank.enrichFailed'))
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('wordbank.enrichFailed'))
    } finally {
      setEnriching(false)
    }
  }, [selectedWord, enriching, callRpc, loadWords, t])

  const clearForm = () => {
    setEditId(null); setEditWord(''); setEditMeaning('')
    setEditExample(''); setEditExamples([])
  }

  const handleSelectWord = (word: Word) => {
    setSelectedWord(word)
    setEditId(word.id); setEditWord(word.word)
    setEditMeaning(word.meaning || '')
    setEditExample(word.example || '')
    setEditExamples(word.examples || [])
    if (word.examples?.length) {
      const first = word.examples[0]
      const firstExample = typeof first === 'string' ? first : (first.sentence || '')
      if (!editExample.trim()) setEditExample(firstExample)
    }
  }

  const handleSave = async () => {
    if (!editWord.trim() || !activeWordbookId) return
    setSaving(true); setError(null)
    try {
      if (editId) {
        await callRpc('word.update', { word_id: editId, word: editWord.trim(), meaning: editMeaning.trim(), example: editExample.trim(), examples: editExamples })
      } else {
        await callRpc('word.add', { wordbook_id: activeWordbookId, word: editWord.trim(), meaning: editMeaning.trim(), example: editExample.trim(), examples: editExamples })
      }
      errorStore.success(t('wordbank.wordSaved'))
      await loadWords()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('wordbank.saveFailed'))
    } finally { setSaving(false) }
  }

  const handleDelete = async () => {
    if (!editId) return
    if (!confirm(t('wordbank.deleteConfirm'))) return
    setError(null)
    try {
      await callRpc('word.delete', { word_id: editId })
      errorStore.success(t('wordbank.wordDeleted'))
      await loadWords()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('wordbank.deleteFailed'))
    }
  }

  const handleGenerateExamples = async () => {
    if (!editWord.trim()) return
    setGeneratingExamples(true); setError(null)
    try {
      const result = await callRpc<{ success: boolean; examples: any[]; word_id: number }>('word.generate_examples', {
        word: editWord.trim(), meaning: editMeaning.trim(), source: 'en', word_id: editId || 0,
      }, 90000)
      if (result.success && result.examples?.length > 0) {
        setEditExamples(result.examples)
        const firstSentence = typeof result.examples[0] === 'string' ? result.examples[0] : (result.examples[0].sentence || '')
        if (!editExample.trim()) setEditExample(firstSentence)
        errorStore.success(t('wordbank.generatedExamplesFmt', { count: result.examples.length }))
        if (editId) await callRpc('word.update', { word_id: editId, examples: result.examples })
      } else setError(t('wordbank.generateFailed'))
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t('wordbank.generateFailed'))
    } finally { setGeneratingExamples(false) }
  }

  const handleExportWordbook = useCallback(() => {
    if (!words.length) {
      errorStore.info(t('wordbank.emptyExportHint'))
      return
    }
    const activeWb = wordbooks.find(w => w.id === activeWordbookId)
    const fileName = activeWb ? `wordbook-${activeWb.name}-${Date.now()}.json` : `wordbook-${Date.now()}.json`
    const data = {
      name: activeWb?.name ?? 'wordbook', exportedAt: new Date().toISOString(),
      words: words.map(w => ({ word: w.word, meaning: w.meaning, example: w.example, examples: w.examples })),
    }
    const json = JSON.stringify(data, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = fileName
    document.body.appendChild(a); a.click()
    document.body.removeChild(a); URL.revokeObjectURL(url)
    errorStore.success(t('wordbank.exportDone'))
  }, [words, wordbooks, activeWordbookId, t])

  const handleImportJson = useCallback(() => {
    const input = document.createElement('input')
    input.type = 'file'; input.accept = '.json'
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0]
      if (!file) return
      try {
        const text = await file.text()
        const data = JSON.parse(text)
        const cards = data.words || data.cards || []
        if (!Array.isArray(cards) || cards.length === 0) { setError(t('wordbank.jsonNoWords')); return }
        if (!activeWordbookId) { setError(t('wordbank.pickWordbookFirst')); return }
        const result = await callRpc<{ imported: number; skipped: number }>('wordbook.import_batch', {
          wordbook_id: activeWordbookId,
          cards: cards.map((c: Record<string, unknown>) => ({ word: c.word ?? c.front ?? '', meaning: c.meaning ?? c.back ?? '', example: c.example ?? '', tags: c.tags ?? [] })),
        })
        errorStore.success(t('wordbank.importDoneSkippedFmt', { count: result.imported, skipped: result.skipped }))
        await loadWords()
      } catch (err: unknown) { setError(err instanceof Error ? err.message : t('wordbank.importFailed')) }
    }
    input.click()
  }, [activeWordbookId, callRpc, loadWords, t])

  const handleCreateWordbook = async (rawValue?: string) => {
    const v = (rawValue ?? newWordbookName).trim()
    if (!v) return
    try {
      await callRpc('wordbook.create', { name: v })
      setNewWordbookName(''); setShowNewWordbook(false)
      errorStore.success(t('wordbank.wordbookCreated'))
      await loadWordbooks()
    } catch (err: unknown) { setError(err instanceof Error ? err.message : t('wordbank.createFailed')) }
  }

  const handleRenameWordbook = async (wordbookId: number) => {
    if (!renameValue.trim()) return
    try {
      await callRpc('wordbook.rename', { wordbook_id: wordbookId, name: renameValue.trim() })
      setRenamingWordbook(null); setRenameValue('')
      await loadWordbooks()
    } catch (err: unknown) { setError(err instanceof Error ? err.message : t('wordbank.renameFailed')) }
  }

  const handleDeleteWordbook = async (wordbookId: number) => {
    const wb = wordbooks.find(w => w.id === wordbookId)
    if (!wb) return
    if (!confirm(t('wordbank.deleteWordbookConfirm', { name: wb.name }))) return
    try {
      await callRpc('wordbook.delete', { wordbook_id: wordbookId })
      setShowWordbookMenu(null)
      if (activeWordbookId === wordbookId) { setActiveWordbookId(null); setWords([]); setSelectedWord(null); clearForm() }
      await loadWordbooks()
    } catch (err: unknown) { setError(err instanceof Error ? err.message : t('wordbank.deleteFailed')) }
  }

  const startReview = useCallback(async () => {
    if (!activeWordbookId) return
    try {
      const due = await callRpc<Word[]>('word.list_due', { wordbook_id: activeWordbookId, limit: 200 })
      const reviewList = due.length > 0 ? due : words
      setReviewWords(reviewList); setReviewMode(true)
      setReviewIndex(0); setReviewFlipped(false)
      setReviewStats({ remembered: 0, forgotten: 0 })
    } catch {
      setReviewWords(words); setReviewMode(true)
      setReviewIndex(0); setReviewFlipped(false)
      setReviewStats({ remembered: 0, forgotten: 0 })
    }
  }, [activeWordbookId, words, callRpc])

  const handleReviewRating = useCallback(async (quality: number) => {
    const word = reviewWords[reviewIndex]
    if (!word) return
    try { await callRpc('word.review', { word_id: word.id, quality }) } catch (e) { /* silent */ swallowError('WordbankModule:336', e) }
    if (quality >= 3) setReviewStats(s => ({ ...s, remembered: s.remembered + 1 }))
    else setReviewStats(s => ({ ...s, forgotten: s.forgotten + 1 }))
    if (reviewIndex + 1 >= reviewWords.length) setReviewMode(false)
    else { setReviewIndex(i => i + 1); setReviewFlipped(false) }
  }, [reviewWords, reviewIndex, callRpc])

  /** Dictation mode: RPC is already done by word.dictation_check (auto_record=true),
   *  so only update stats and advance index. */
  const handleDictationRate = useCallback((quality: number) => {
    if (quality >= 3) setReviewStats(s => ({ ...s, remembered: s.remembered + 1 }))
    else setReviewStats(s => ({ ...s, forgotten: s.forgotten + 1 }))
    // Note: index advancement is handled inside WordDictation (advanceToNext)
    // We DON'T advance here because the user needs to see the diff result first
  }, [])

  const handleFlip = useCallback(() => setReviewFlipped(f => !f), [])

  const connected = wsStatus === 'connected'

  return (
    <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Connection status — always visible for diagnostics */}
      <div className="px-4 py-0.5 flex items-center gap-2" style={{ fontSize: '9px' }}>
        <span style={{ color: connected ? 'var(--success)' : 'var(--danger)' }}>
          <Icon icon={`fa-solid fa-circle text-[5px] mr-1 ${connected ? '' : 'animate-pulse'}`} />
          {connected ? t('wordbank.connected') : t('wordbank.disconnected')}
        </span>
        <button onClick={async () => {
          try {
            const client = getWsClient()
            const result = await client.call<Wordbook[]>('wordbook.list', {})
            errorStore.success(t('wordbank.testOkFmt', { count: result?.length ?? 0 }))
          } catch (e: any) { errorStore.error(t('wordbank.testFailFmt', { err: e?.message || t('wordbank.testFailUnknown') })) }
        }} className="px-2 py-0.5 rounded" style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}>
          {t('wordbank.testConnection')}
        </button>
      </div>
      {/* Wordbook tabs bar */}
      <div className="flex items-center gap-1 px-3 py-2 border-b border-white/[0.04] flex-shrink-0 overflow-x-auto">
        {wordbooks.map(wb => (
          <div key={wb.id} className="relative flex-shrink-0">
            {renamingWordbook === wb.id ? (
              <input value={renameValue} onChange={e => setRenameValue(e.target.value)}
                onBlur={() => handleRenameWordbook(wb.id)}
                onKeyDown={e => { if (e.key === 'Enter') handleRenameWordbook(wb.id); if (e.key === 'Escape') setRenamingWordbook(null) }}
                className="px-2 py-1 rounded text-xs bg-white/[0.08] border border-white/10 outline-none"
                style={{ color: 'var(--text-primary)', width: 100 }} autoFocus
              />
            ) : (
              <button onClick={() => setActiveWordbookId(wb.id)}
                onContextMenu={e => { e.preventDefault(); setShowWordbookMenu(wb.id) }}
                className={`px-2.5 py-1 rounded text-xs transition-all whitespace-nowrap ${
                  activeWordbookId === wb.id ? 'bg-fluent-blue/15 text-fluent-blue font-medium' : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.06]'
                }`}
              >{wb.name}</button>
            )}
            {showWordbookMenu === wb.id && (
              <div ref={menuRef}
                className="absolute top-full left-0 mt-1 py-1 rounded-lg border z-50 min-w-[100px]"
                style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
              >
                <button onClick={() => { setRenamingWordbook(wb.id); setRenameValue(wb.name); setShowWordbookMenu(null) }}
                  className="w-full px-3 py-1.5 text-xs text-left hover:bg-white/[0.06] transition-colors"
                  style={{ color: 'var(--text-primary)' }}
                >{t('wordbank.renameWordbook')}</button>
                <button onClick={() => handleDeleteWordbook(wb.id)}
                  className="w-full px-3 py-1.5 text-xs text-left hover:bg-white/[0.06] transition-colors text-red-400"
                >{t('wordbank.deleteWordbook')}</button>
              </div>
            )}
          </div>
        ))}
        <button onClick={() => setShowNewWordbook(true)}
          className="px-2 py-1 rounded text-xs text-text-dim hover:text-text-secondary hover:bg-white/[0.06] transition-all flex-shrink-0"
          title={t('wordbank.newWordbook')}
        ><Icon icon="fa-solid fa-plus text-xs" /></button>
        {showNewWordbook && (
          <div className="flex items-center gap-1 flex-shrink-0">
            <input value={newWordbookName} onChange={e => setNewWordbookName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreateWordbook(e.currentTarget.value); if (e.key === 'Escape') { setShowNewWordbook(false); setNewWordbookName('') } }}
              placeholder={t('wordbank.wordbookName')}
              className="px-2 py-1 rounded text-xs bg-white/[0.08] border border-white/10 outline-none w-[80px]"
              style={{ color: 'var(--text-primary)' }} autoFocus
            />
            <button onClick={() => handleCreateWordbook()}
              className="px-1.5 py-1 text-xs text-fluent-blue hover:text-fluent-blue/80"
            ><Icon icon="fa-solid fa-check" /></button>
          </div>
        )}
        {words.length > 0 && !reviewMode && (
          <div className="ml-auto flex items-center gap-1 flex-shrink-0">
            <button onClick={() => setReviewType(t => t === 'flip' ? 'dictation' : 'flip')}
              className="px-1.5 py-1 rounded text-2xs transition-all hover:bg-white/[0.06]"
              style={{ color: reviewType === 'dictation' ? '#c084fc' : 'var(--text-dim)' }}
              title={reviewType === 'flip' ? t('wordbank.switchToDictation') : t('wordbank.switchToFlip')}>
              <Icon icon={`fa-solid ${reviewType === 'dictation' ? 'fa-pen-fancy' : 'fa-clone'} text-[8px] mr-0.5`} />
              {reviewType === 'flip' ? t('wordbank.flipMode') : t('wordbank.dictationMode')}
            </button>
            <button onClick={startReview}
              className="px-2 py-1 rounded text-xs font-medium transition-all text-text-dim hover:text-amber-400/70 hover:bg-white/[0.04]"
              title={t('wordbank.startReview')}>
              <Icon icon="fa-solid fa-brain text-2xs mr-1" />{t('wordbank.review')}
            </button>
          </div>
        )}
        {reviewMode && (
          <button onClick={() => setReviewMode(false)}
            className="ml-auto px-2 py-1 rounded text-xs font-medium transition-all bg-amber-400/12 text-amber-400 flex-shrink-0"
            title={t('wordbank.exitReviewTitle')}>
            <Icon icon="fa-solid fa-xmark text-2xs mr-1" />{t('wordbank.exitReview')}
          </button>
        )}
        {!connected && (
          <span className="ml-auto text-xs text-red-400/60 flex-shrink-0">
            <Icon icon="fa-solid fa-circle text-[6px] mr-1" />{t('wordbank.disconnected')}
          </span>
        )}
      </div>

      {/* Review mode */}
      {reviewMode && reviewWords.length > 0 && (
        reviewType === 'dictation' ? (
          <WordDictation
            reviewWords={reviewWords}
            reviewIndex={reviewIndex}
            reviewStats={reviewStats}
            onRate={handleDictationRate}
            onAdvance={() => {
              if (reviewIndex + 1 >= reviewWords.length) setReviewMode(false)
              else setReviewIndex(i => i + 1)
            }}
            onExit={() => setReviewMode(false)}
            callRpc={callRpc}
          />
        ) : (
          <WordReview
            reviewWords={reviewWords}
            reviewIndex={reviewIndex}
            reviewFlipped={reviewFlipped}
            reviewStats={reviewStats}
            onFlip={handleFlip}
            onRate={handleReviewRating}
            onExit={() => setReviewMode(false)}
          />
        )
      )}

      {!reviewMode && wordbooks.length === 0 ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center">
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('wordbank.noWordbooks')}</p>
          </div>
        </div>
      ) : !reviewMode ? (
        <div className="flex-1 flex min-h-0">
          <WordListPanel
            words={words}
            loading={loading}
            searchKeyword={searchKeyword}
            showDueOnly={showDueOnly}
            selectedWordId={selectedWord?.id ?? null}
            onSearchChange={setSearchKeyword}
            onToggleDueOnly={() => setShowDueOnly(s => !s)}
            onSelectWord={handleSelectWord}
            onAddNew={() => { setSelectedWord(null); clearForm() }}
            onExport={handleExportWordbook}
            onImportJson={handleImportJson}
            onShowImport={() => setShowImport(true)}
          />

          {selectedWord || editId === null ? (
            <div className="flex-1 flex min-h-0">
              <WordEditForm
                editWord={editWord}
                editMeaning={editMeaning}
                editExample={editExample}
                editExamples={editExamples}
                editId={editId}
                saving={saving}
                generatingExamples={generatingExamples}
                error={error}
                showImport={showImport}
                importing={importing}
                onWordChange={setEditWord}
                onMeaningChange={setEditMeaning}
                onExampleChange={setEditExample}
                onRemoveExample={(i) => setEditExamples(prev => prev.filter((_, j) => j !== i))}
                onGenerateExamples={handleGenerateExamples}
                onSave={handleSave}
                onDelete={handleDelete}
                onDismissError={() => setError(null)}
                onImportPreset={handleImportPreset}
                onShowImportChange={setShowImport}
                wordPhonetic={selectedWord?.phonetic}
                wordLevel={selectedWord?.level}
                wordEtymology={selectedWord?.etymology}
                wordSynonyms={selectedWord?.synonyms}
                wordAntonyms={selectedWord?.antonyms}
                wordCollocations={selectedWord?.collocations}
                enriching={enriching}
                onCloudEnrich={handleCloudEnrich}
                onWordClick={handleWordClick}
              />
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center">
              <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('wordbank.selectWordHint')}</p>
            </div>
          )}
        </div>
      ) : null}

      {/* Word Detail Popup (clicked synonym/example word) */}
      {detailPopupWord && (
        <div className="fixed inset-0 z-[99999] flex items-center justify-center" style={{ background: 'rgba(0,0,0,0.4)' }}
          onClick={() => { setDetailPopupWord(null); setDetailPopupData(null) }}>
          <div className="w-[340px] max-h-[70vh] rounded-xl border shadow-2xl overflow-hidden flex flex-col"
            style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--panel-border)' }}>
              <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>{detailPopupWord}</span>
              <button onClick={() => { setDetailPopupWord(null); setDetailPopupData(null) }}
                className="w-6 h-6 flex items-center justify-center rounded text-sm hover:bg-white/[0.06]"
                style={{ color: 'var(--text-muted)' }}><Icon icon="fa-solid fa-xmark" /></button>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              {detailPopupData?.etymology && (
                <div className="text-xs leading-relaxed px-2 py-1 rounded" style={{ background: 'rgba(251,191,36,0.06)', color: '#fbbf24' }}>
                  <Icon icon="fa-solid fa-tree mr-1" />{detailPopupData.etymology}
                </div>
              )}
              {detailPopupLoading ? (
                <div className="text-xs text-center py-8" style={{ color: 'var(--text-muted)' }}>{t('wordbank.detailLoading')}</div>
              ) : detailPopupData?.error ? (
                <div className="text-xs text-center py-8" style={{ color: 'var(--text-muted)' }}>{detailPopupData.error}</div>
              ) : detailPopupData ? (
                <div className="space-y-3">
                  {detailPopupData.phonetic && (
                    <div className="text-base" style={{ color: 'var(--text-tertiary)' }}>
                      /{detailPopupData.phonetic}/
                      {detailPopupData.level && <span className="text-2xs px-1.5 py-0.5 rounded ml-2" style={{ background: 'rgba(96,165,250,0.12)', color: 'var(--brand)' }}>{detailPopupData.level}</span>}
                    </div>
                  )}
                  {detailPopupData.meanings?.length > 0 && (
                    <div>
                      <div className="text-2xs font-medium mb-1" style={{ color: 'var(--text-dim)' }}>{t('wordbank.detailMeaningsTitle')}</div>
                      {detailPopupData.meanings.map((m: string, i: number) => (
                        <div key={i} className="text-sm py-0.5" style={{ color: 'var(--text-secondary)' }}>{m}</div>
                      ))}
                    </div>
                  )}
                  {detailPopupData.synonyms?.length > 0 && (
                    <div>
                      <div className="text-2xs font-medium mb-1" style={{ color: 'var(--text-dim)' }}>{t('wordbank.detailSynonymsTitle')}</div>
                      <div className="flex flex-wrap gap-1">
                        {detailPopupData.synonyms.map((s: any, i: number) => (
                          <span key={i} onClick={() => handleWordClick(s.word || s)}
                            className="px-1.5 py-0.5 rounded text-xs cursor-pointer hover:opacity-70"
                            style={{ background: 'rgba(52,211,153,0.08)', color: '#34d399' }}>
                            {s.word || s}{s.similarity === 'high' ? ' ★' : ''}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                  {detailPopupData.collocations?.length > 0 && (
                    <div>
                      <div className="text-2xs font-medium mb-1" style={{ color: 'var(--text-dim)' }}>{t('wordbank.detailCollocationsTitle')}</div>
                      <div className="flex flex-wrap gap-1">
                        {detailPopupData.collocations.map((c: any, i: number) => (
                          <span key={i} className="px-1.5 py-0.5 rounded text-xs" style={{ background: 'rgba(251,191,36,0.08)', color: '#fbbf24' }}>{c.phrase || c}{c.meaning ? ` — ${c.meaning}` : ''}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {detailPopupData.examples?.length > 0 && (
                    <div>
                      <div className="text-2xs font-medium mb-1" style={{ color: 'var(--text-dim)' }}>{t('wordbank.detailExamplesTitle')}</div>
                      {detailPopupData.examples.map((e: string, i: number) => (
                        <div key={i} className="text-xs italic py-0.5" style={{ color: 'var(--text-tertiary)' }}>"{e}"</div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-xs text-center py-8" style={{ color: 'var(--text-muted)' }}>{t('wordbank.detailEmpty')}</div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default WordbankModule