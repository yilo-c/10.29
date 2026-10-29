import React, { useState } from 'react'
import Icon from '../Common/Icon'

import { useStore, type ProactiveSuggestion } from '@desk-notes/shared'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useTranslation } from '../../i18n'

interface Props {
  onClose: () => void
}

const SuggestionPanel: React.FC<Props> = ({ onClose }) => {
  const { t } = useTranslation()
  const proactiveBrief = useStore(s => s.proactiveBrief)
  const proactiveBriefLoading = useStore(s => s.proactiveBriefLoading)
  const suggestions = useStore(s => s.proactiveSuggestions)
  const setActiveModule = useStore(s => s.setActiveModule)
  const proactiveEnabled = useStore(s => s.proactiveEnabled)
  const setProactiveEnabled = useStore(s => s.setProactiveEnabled)
  const [showBriefing, setShowBriefing] = useState(true)

  const handleAction = (sug: ProactiveSuggestion) => {
    if (sug.actionType === 'navigate' && sug.actionPayload) {
      setActiveModule(sug.actionPayload as 'dashboard' | 'chat' | 'notes' | 'todo' | 'calendar' | 'info' | 'agent' | 'settings')
      onClose()
    } else if (sug.actionType === 'create_note') {
      useStore.getState().addFloatingNote({
        id: crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`,
        type: 'text',
        title: sug.actionPayload || t('suggestionPanel.quickNoteTitle'),
        content: '',
        x: 120 + Math.random() * 60,
        y: 120 + Math.random() * 60,
        width: 260,
        height: 200,
        zIndex: Date.now(),
      })
      onClose()
    }
  }

  const handleDismissSuggestion = (id: string) => {
    useStore.getState().setProactiveSuggestions(suggestions.filter(s => s.id !== id))
  }

  return (
    <div className="flex flex-col">
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 py-3 border-b sticky top-0 z-10"
        style={{ borderColor: 'var(--panel-border, rgba(255,255,255,0.08))', background: 'var(--panel-bg, var(--surface))' }}
      >
        <div className="flex items-center gap-2">
          <Icon icon="fa-regular fa-lightbulb text-xs" style={{ color: '#8b5cf6' }} />
          <span className="text-xs font-medium" style={{ color: 'var(--text-primary, var(--text-secondary))' }}>
            {t('suggestionPanel.header')}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setProactiveEnabled(!proactiveEnabled)}
            className="px-2 py-0.5 rounded text-2xs transition-all"
            style={{
              background: proactiveEnabled ? 'rgba(52,211,153,0.15)' : 'rgba(239,68,68,0.15)',
              color: proactiveEnabled ? 'var(--success)' : 'var(--danger)',
            }}
          >
            {proactiveEnabled ? t('suggestionPanel.enabled') : t('suggestionPanel.disabled')}
          </button>
          <button
            onClick={onClose}
            className="w-5 h-5 flex items-center justify-center rounded text-xs hover:bg-white/[0.08] transition-all"
            style={{ color: 'var(--text-muted, var(--text-secondary))' }}
          >
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>
      </div>

      <div className="p-3 space-y-3">
        {/* Morning brief */}
        {proactiveBrief && showBriefing && (
          <div
            className="rounded-xl p-3.5 border"
            style={{
              background: 'linear-gradient(135deg, rgba(99,102,241,0.08), rgba(139,92,246,0.05))',
              borderColor: 'rgba(99,102,241,0.2)',
            }}
          >
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-1.5">
                <Icon icon="fa-regular fa-sun text-xs" style={{ color: 'var(--warning)' }} />
                <span className="text-xs font-medium" style={{ color: 'var(--text-primary, var(--text-secondary))' }}>
                  {t('suggestionPanel.morningBrief')}
                </span>
              </div>
              <button
                onClick={() => setShowBriefing(false)}
                className="text-2xs opacity-40 hover:opacity-80 transition-all"
                style={{ color: 'var(--text-muted, var(--text-secondary))' }}
              >
                {t('suggestionPanel.collapse')}
              </button>
            </div>
            {proactiveBriefLoading ? (
              <div className="flex items-center gap-2 py-2">
                <div className="w-3 h-3 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
                <span className="text-xs" style={{ color: 'var(--text-muted, var(--text-secondary))' }}>{t('suggestionPanel.generating')}</span>
              </div>
            ) : (
              <div className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary, var(--text-muted))' }}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{proactiveBrief}</ReactMarkdown>
              </div>
            )}
          </div>
        )}

        {/* Suggestions */}
        {suggestions.length === 0 && !proactiveBrief && (
          <div className="flex flex-col items-center justify-center py-8 gap-2 text-center">
            <div className="text-2xl opacity-20">
              <Icon icon="fa-regular fa-lightbulb" />
            </div>
            <div className="text-xs" style={{ color: 'var(--text-muted, var(--text-secondary))' }}>
              {t('suggestionPanel.empty')}
            </div>
          </div>
        )}

        {suggestions.map(sug => (
          <SuggestionRow
            key={sug.id}
            suggestion={sug}
            onAction={handleAction}
            onDismiss={handleDismissSuggestion}
          />
        ))}
      </div>
    </div>
  )
}

interface RowProps {
  suggestion: ProactiveSuggestion
  onAction: (sug: ProactiveSuggestion) => void
  onDismiss: (id: string) => void
}

const SuggestionRow: React.FC<RowProps> = ({ suggestion: sug, onAction, onDismiss }) => {
  const { t } = useTranslation()
  return (
    <div
      className="flex items-start gap-3 px-3 py-2.5 rounded-xl border transition-all cursor-pointer hover:bg-white/[0.04]"
      style={{ borderColor: 'var(--panel-border, rgba(255,255,255,0.08))' }}
      onClick={() => onAction(sug)}
    >
      <div
        className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0 mt-0.5"
        style={{ background: 'rgba(139,92,246,0.12)', color: '#8b5cf6' }}
      >
        <Icon icon={`${sug.icon} text-xs`} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium truncate" style={{ color: 'var(--text-primary, var(--text-secondary))' }}>
          {sug.title}
        </div>
        <div className="text-xs mt-0.5 leading-relaxed line-clamp-2" style={{ color: 'var(--text-muted, var(--text-secondary))' }}>
          {sug.description}
        </div>
        {sug.actionType && (
          <div className="flex items-center gap-2 mt-1.5">
            <span className="text-2xs px-1.5 py-0.5 rounded" style={{ background: 'rgba(96,165,250,0.12)', color: 'var(--brand)' }}>
              {sug.actionType === 'navigate' ? t('suggestionPanel.navigateTo', { target: sug.actionPayload ?? '' }) : sug.actionType === 'create_note' ? t('suggestionPanel.createNote') : ''}
            </span>
          </div>
        )}
      </div>
      <button
        onClick={e => { e.stopPropagation(); onDismiss(sug.id) }}
        className="w-5 h-5 flex items-center justify-center rounded text-2xs opacity-0 hover:opacity-100 hover:bg-white/[0.08] transition-all shrink-0"
        style={{ color: 'var(--text-muted, var(--text-secondary))' }}
      >
        <Icon icon="fa-solid fa-xmark" />
      </button>
    </div>
  )
}

export default SuggestionPanel
