import React from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import type { Word } from './types'

interface Props {
  words: Word[]
  loading: boolean
  searchKeyword: string
  showDueOnly: boolean
  selectedWordId: number | null
  onSearchChange: (keyword: string) => void
  onToggleDueOnly: () => void
  onSelectWord: (word: Word) => void
  onAddNew: () => void
  onExport: () => void
  onImportJson: () => void
  onShowImport: () => void
}

const WordListPanel: React.FC<Props> = ({
  words, loading, searchKeyword, showDueOnly, selectedWordId,
  onSearchChange, onToggleDueOnly, onSelectWord,
  onAddNew, onExport, onImportJson, onShowImport,
}) => {
  const { t } = useTranslation()
  const now = new Date().toISOString()
  const displayed = showDueOnly
    ? words.filter(w => w.next_review && w.next_review <= now)
    : words

  return (
    <div className="w-[200px] flex-shrink-0 flex flex-col border-r" style={{ borderColor: 'var(--panel-border)' }}>
      <div className="px-2 pt-2 pb-1">
        <input
          value={searchKeyword}
          onChange={e => onSearchChange(e.target.value)}
          placeholder={t('wordList.searchPlaceholder')}
          className="w-full px-2 py-1 rounded text-xs bg-white/[0.06] border border-white/[0.06] outline-none placeholder-white/20"
          style={{ color: 'var(--text-primary)' }}
        />
      </div>
      <div className="px-2 pb-1">
        <button
          onClick={onToggleDueOnly}
          className={`w-full py-1 rounded text-2xs transition-all ${showDueOnly ? 'font-medium' : ''}`}
          style={{
            background: showDueOnly ? 'rgba(234,179,8,0.1)' : 'transparent',
            color: showDueOnly ? '#eab308' : 'var(--text-muted)',
          }}
        >
          <Icon icon="fa-solid fa-clock text-[8px] mr-1" />
          {showDueOnly ? t('wordList.showAll') : t('wordList.dueOnly')}
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-1 pb-1">
        {loading ? (
          <div className="flex items-center justify-center py-4">
            <div className="w-3 h-3 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
          </div>
        ) : displayed.length === 0 ? (
          <p className="text-xs text-center py-4" style={{ color: 'var(--text-muted)' }}>
            {showDueOnly ? t('wordList.noDueWords') : t('wordList.empty')}
          </p>
        ) : (
          displayed.map(w => (
            <button
              key={w.id}
              onClick={() => onSelectWord(w)}
              className={`w-full text-left px-2 py-1.5 rounded text-xs transition-all mb-0.5 ${
                selectedWordId === w.id
                  ? 'bg-fluent-blue/12 text-fluent-blue'
                  : 'hover:bg-white/[0.04]'
              }`}
              style={{ color: selectedWordId === w.id ? undefined : 'var(--text-secondary)' }}
            >
              <span className="font-medium">{w.word}</span>
              {w.meaning && (
                <span className="ml-1 opacity-60 truncate block text-xs">{w.meaning}</span>
              )}
            </button>
          ))
        )}
      </div>
      <div className="px-2 py-1.5 border-t" style={{ borderColor: 'var(--panel-border)' }}>
        <button
          onClick={onAddNew}
          className="w-full py-1 rounded text-xs border border-dashed transition-all"
          style={{ borderColor: 'var(--panel-border)', color: 'var(--text-muted)' }}
        >
          <Icon icon="fa-solid fa-plus mr-1" />
          {t('wordList.addWord')}
        </button>
        <button
          onClick={onShowImport}
          className="w-full py-1 rounded text-xs transition-all mt-1"
          style={{ color: 'var(--text-muted)' }}
        >
          <Icon icon="fa-solid fa-download mr-1" />{t('wordList.importPreset')}
        </button>
        <div className="flex gap-1 mt-1">
          <button
            onClick={onExport}
            className="flex-1 py-1 rounded text-2xs transition-all"
            style={{ color: 'var(--text-muted)', background: 'rgba(255,255,255,0.03)' }}
          >
            <Icon icon="fa-solid fa-upload mr-1" />{t('wordList.exportJson')}
          </button>
          <button
            onClick={onImportJson}
            className="flex-1 py-1 rounded text-2xs transition-all"
            style={{ color: 'var(--text-muted)', background: 'rgba(255,255,255,0.03)' }}
          >
            <Icon icon="fa-solid fa-download mr-1" />{t('wordList.importJson')}
          </button>
        </div>
      </div>
    </div>
  )
}

export default WordListPanel
