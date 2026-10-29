import React from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'

export type ExampleEntry = string | { sentence: string; translation?: string }

interface Props {
  editWord: string
  editMeaning: string
  editExample: string
  editExamples: ExampleEntry[]
  editId: number | null
  saving: boolean
  generatingExamples: boolean
  error: string | null
  showImport: boolean
  importing: boolean
  onWordChange: (value: string) => void
  onMeaningChange: (value: string) => void
  onExampleChange: (value: string) => void
  onRemoveExample: (index: number) => void
  onGenerateExamples: () => void
  onSave: () => void
  onDelete: () => void
  onDismissError: () => void
  onImportPreset: () => void
  onShowImportChange: (show: boolean) => void
  wordPhonetic?: string
  wordLevel?: string
  wordEtymology?: string
  wordSynonyms?: { word: string; similarity?: string; nuance?: string }[]
  wordAntonyms?: { word: string; note?: string }[]
  wordCollocations?: { phrase: string; meaning?: string }[]
  enriching?: boolean
  onCloudEnrich?: () => void
  onWordClick?: (word: string) => void
}

const LEVEL_LABEL_KEY: Record<string, string> = {
  A1: 'wordEdit.levelA1', A2: 'wordEdit.levelA2', B1: 'wordEdit.levelB1',
  B2: 'wordEdit.levelB2', C1: 'wordEdit.levelC1', C2: 'wordEdit.levelC2',
}

const WordEditForm: React.FC<Props> = ({
  editWord, editMeaning, editExample, editExamples, editId,
  saving, generatingExamples, error, showImport, importing,
  onWordChange, onMeaningChange, onExampleChange, onRemoveExample,
  onGenerateExamples, onSave, onDelete, onDismissError,
  onImportPreset, onShowImportChange,
  wordPhonetic, wordLevel, wordEtymology, wordSynonyms, wordAntonyms, wordCollocations,
  enriching, onCloudEnrich, onWordClick,
}) => {
  const { t } = useTranslation()
  return (
    <div className="flex-1 flex flex-col min-h-0 p-3 overflow-y-auto">
      {error && (
        <div className="mb-2 px-2 py-1 rounded text-xs bg-red-500/10 text-red-400">
          {error}
          <button onClick={onDismissError} className="float-right"><Icon icon="fa-solid fa-xmark" /></button>
        </div>
      )}

      {showImport && (
        <div className="mb-3 p-3 rounded-xl border" style={{ borderColor: 'var(--panel-border)', background: 'rgba(168,85,247,0.04)' }}>
          <div className="text-xs font-medium mb-1" style={{ color: '#a855f7' }}>
            {t('wordEdit.presetTitle')}
          </div>
          <div className="text-2xs mb-2" style={{ color: 'var(--text-muted)' }}>
            {t('wordEdit.presetHint')}
          </div>
          <button
            onClick={onImportPreset}
            disabled={importing}
            className="flex items-center gap-1 px-3 py-1 rounded text-xs font-medium transition-all disabled:opacity-40"
            style={{ background: 'rgba(168,85,247,0.12)', color: '#a855f7' }}
          >
            {importing ? (
              <><div className="w-3 h-3 border-2 border-purple-400/30 border-t-purple-400 rounded-full animate-spin" />{t('wordEdit.importing')}</>
            ) : (
              <><Icon icon="fa-solid fa-cloud-arrow-down text-[8px]" />{t('wordEdit.startImport')}</>
            )}
          </button>
          <button onClick={() => onShowImportChange(false)} className="ml-2 text-xs text-text-dim hover:text-text-secondary">
            {t('wordEdit.cancel')}
          </button>
        </div>
      )}

      <div className="flex flex-col gap-2">
        <div>
          <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--text-muted)' }}>{t('wordEdit.fieldWord')}</label>
          <input
            value={editWord}
            onChange={e => onWordChange(e.target.value)}
            placeholder={t('wordEdit.placeholderWord')}
            className="w-full px-2.5 py-1.5 rounded text-base bg-white/[0.06] border border-white/[0.06] outline-none placeholder-white/20"
            style={{ color: 'var(--text-primary)' }}
          />
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--text-muted)' }}>{t('wordEdit.fieldMeaning')}</label>
          <textarea
            value={editMeaning}
            onChange={e => onMeaningChange(e.target.value)}
            placeholder={t('wordEdit.placeholderMeaning')}
            rows={3}
            className="w-full px-2.5 py-1.5 rounded text-sm bg-white/[0.06] border border-white/[0.06] outline-none resize-none placeholder-white/20"
            style={{ color: 'var(--text-primary)' }}
          />
        </div>
        <div>
          <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--text-muted)' }}>{t('wordEdit.fieldExample')}</label>
          <textarea
            value={editExample}
            onChange={e => onExampleChange(e.target.value)}
            placeholder={t('wordEdit.placeholderExample')}
            rows={2}
            className="w-full px-2.5 py-1.5 rounded text-sm bg-white/[0.06] border border-white/[0.06] outline-none resize-none placeholder-white/20"
            style={{ color: 'var(--text-primary)' }}
          />
        </div>

        {editExamples.length > 0 && (
          <div>
            <label className="text-xs font-medium mb-1 block" style={{ color: 'var(--text-muted)' }}>
              {t('wordEdit.moreExamplesFmt', { count: editExamples.length })}
            </label>
            <div className="flex flex-col gap-1">
              {editExamples.map((ex, i) => {
                const sentence = typeof ex === 'string' ? ex : (ex.sentence || '')
                const translation = typeof ex === 'object' ? (ex.translation || '') : ''
                return (
                <div key={i} className="flex items-start gap-1 px-2 py-1.5 rounded"
                  style={{ background: 'rgba(255,255,255,0.03)' }}>
                  <span className="text-2xs shrink-0 mt-0.5" style={{ color: 'var(--text-dim)' }}>{i + 1}.</span>
                  <div className="flex-1 min-w-0">
                    <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{sentence}</span>
                    {translation && <div className="text-xs mt-0.5" style={{ color: 'var(--text-tertiary)' }}>{translation}</div>}
                  </div>
                  <button
                    onClick={() => onRemoveExample(i)}
                    className="ml-auto shrink-0 w-4 h-4 flex items-center justify-center rounded text-[8px] hover:bg-red-500/10"
                    style={{ color: 'rgba(239,68,68,0.5)' }}
                    title={t('wordEdit.removeExample')}
                  >
                    <Icon icon="fa-solid fa-xmark" />
                  </button>
                </div>
              );
            })}
            </div>
          </div>
        )}

        <div className="flex items-center gap-2 pt-1">
          <button
            onClick={onGenerateExamples}
            disabled={generatingExamples || !editWord.trim()}
            className="flex items-center gap-1 px-2.5 py-1 rounded text-xs font-medium transition-all disabled:opacity-40"
            style={{ background: 'rgba(168,85,247,0.1)', color: '#a855f7' }}
          >
            <Icon icon={`fa-solid ${generatingExamples ? 'fa-spinner animate-spin' : 'fa-wand-magic-sparkles'} text-[8px]`} />
            {generatingExamples ? t('wordEdit.aiGenerating') : t('wordEdit.aiGenerateExamples')}
          </button>
          <button
            onClick={onSave}
            disabled={saving || !editWord.trim()}
            className="px-3 py-1 rounded text-xs bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 transition-all disabled:opacity-40"
          >
            {saving ? t('wordEdit.saving') : t('wordEdit.save')}
          </button>
          {editId && (
            <button onClick={onDelete} className="px-3 py-1 rounded text-xs text-red-400 hover:bg-red-500/10 transition-all">
              <Icon icon="fa-regular fa-trash-can mr-1" />{t('wordEdit.delete')}
            </button>
          )}
        </div>
      </div>

      {/* Enriched word details (phonetic, synonyms, etc.) */}
      {editId && (wordPhonetic || wordLevel || wordEtymology || wordSynonyms?.length || wordAntonyms?.length || wordCollocations?.length) && (
        <div className="mt-3 pt-3 border-t space-y-2" style={{ borderColor: 'var(--panel-border)' }}>
          {wordPhonetic && (
            <div className="text-base" style={{ color: 'var(--text-tertiary)' }}>
              /{wordPhonetic}/ {wordLevel && <span className="text-2xs px-1.5 py-0.5 rounded ml-1" style={{ background: 'rgba(96,165,250,0.12)', color: 'var(--brand)' }}>{LEVEL_LABEL_KEY[wordLevel] ? t(LEVEL_LABEL_KEY[wordLevel]) : wordLevel}</span>}
            </div>
          )}
          {wordEtymology && (
            <div className="text-xs leading-relaxed px-2 py-1 rounded" style={{ background: 'rgba(251,191,36,0.06)', color: '#fbbf24' }}>
              <Icon icon="fa-solid fa-tree mr-1" />{wordEtymology}
            </div>
          )}
          {wordSynonyms && wordSynonyms.length > 0 && (
            <div>
              <span className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('wordEdit.synonyms')}</span>
              <div className="flex flex-wrap gap-1 mt-1">
                {wordSynonyms.map((s, i) => (
                  <span key={i} onClick={() => onWordClick?.(s.word)} className="px-1.5 py-0.5 rounded text-xs cursor-pointer hover:opacity-70 transition-opacity" style={{ background: 'rgba(52,211,153,0.08)', color: '#34d399' }}>{s.word}{s.similarity === 'high' ? ' ★' : ''}</span>
                ))}
              </div>
            </div>
          )}
          {wordAntonyms && wordAntonyms.length > 0 && (
            <div>
              <span className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('wordEdit.antonyms')}</span>
              <div className="flex flex-wrap gap-1 mt-1">
                {wordAntonyms.map((a, i) => (
                  <span key={i} onClick={() => onWordClick?.(a.word)} className="px-1.5 py-0.5 rounded text-xs cursor-pointer hover:opacity-70 transition-opacity" style={{ background: 'rgba(239,68,68,0.08)', color: 'var(--danger)' }}>{a.word}</span>
                ))}
              </div>
            </div>
          )}
          {wordCollocations && wordCollocations.length > 0 && (
            <div>
              <span className="text-2xs font-medium" style={{ color: 'var(--text-dim)' }}>{t('wordEdit.collocations')}</span>
              <div className="flex flex-wrap gap-1 mt-1">
                {wordCollocations.map((c, i) => (
                  <span key={i} className="px-1.5 py-0.5 rounded text-xs" style={{ background: 'rgba(251,191,36,0.08)', color: '#fbbf24' }}>{c.phrase}{c.meaning ? ` — ${c.meaning}` : ''}</span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      {editId && onCloudEnrich && (
        <button
          onClick={onCloudEnrich}
          disabled={enriching}
          className="mt-2 w-full py-1.5 rounded text-xs transition-all disabled:opacity-40"
          style={{ background: enriching ? 'rgba(96,165,250,0.1)' : 'rgba(96,165,250,0.06)', color: 'var(--brand)', border: '1px dashed rgba(96,165,250,0.2)' }}
        >
          <Icon icon={`fa-solid ${enriching ? 'fa-spinner animate-spin' : 'fa-cloud'} text-[8px] mr-1`} />
          {enriching ? t('wordEdit.enriching') : (wordPhonetic ? t('wordEdit.cloudRegenerate') : t('wordEdit.cloudEnrich'))}
        </button>
      )}
    </div>
  )
}

export default WordEditForm
