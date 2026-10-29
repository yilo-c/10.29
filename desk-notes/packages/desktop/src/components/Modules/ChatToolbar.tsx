import React from 'react'
import Icon from '../Common/Icon'
import { useTranslation } from '../../i18n'
import { ModelSelector } from './ModelSelector'
import type { ModelInfo } from '@desk-notes/shared'

interface ChatToolbarProps {
  showHistory: boolean
  onToggleHistory: () => void
  onNewChat: () => void
  backendConnected: boolean
  selectedModel: ModelInfo | null
  onModelSelect: (m: ModelInfo | null) => void
  loading: boolean
  activeConv: { title: string } | null
  hasMessages: boolean
  onExportJSON: () => void
  onExportText: () => void
  onClear: () => void
}

export const ChatToolbar: React.FC<ChatToolbarProps> = ({
  showHistory, onToggleHistory, onNewChat, backendConnected,
  selectedModel, onModelSelect, loading, activeConv, hasMessages,
  onExportJSON, onExportText, onClear,
}) => {
  const { t } = useTranslation()
  return (
    <div className="flex items-center justify-between px-3 h-9 border-b flex-shrink-0" style={{ borderColor: 'var(--separator)' }}>
      <div className="flex items-center gap-1">
        <button onClick={onToggleHistory}
          className={`w-6 h-6 flex items-center justify-center rounded-md transition-all text-xs ${
            showHistory ? 'bg-white/[0.08] text-text-secondary' : 'text-text-muted hover:text-text-secondary hover:bg-white/[0.06]'
          }`}
          title={t('chatModule.toolbarHistory')}>
          <Icon icon="fa-solid fa-clock-rotate-left" />
        </button>
        <button onClick={onNewChat}
          className="w-6 h-6 flex items-center justify-center rounded-md text-text-muted hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
          title={t('chatModule.toolbarNewChat')}>
          <Icon icon="fa-solid fa-plus" />
        </button>
        {/* Model selector */}
        {backendConnected && (
          <ModelSelector selected={selectedModel} onSelect={onModelSelect} disabled={loading} />
        )}
      </div>
      {activeConv && hasMessages && (
        <div className="flex items-center gap-1">
          <button onClick={onExportJSON}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text-muted hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
            title={t('chatModule.toolbarExportJson')}>
            <Icon icon="fa-solid fa-code" />
          </button>
          <button onClick={onExportText}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text-muted hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
            title={t('chatModule.toolbarExportText')}>
            <Icon icon="fa-solid fa-file-lines" />
          </button>
          <button onClick={onClear}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text-muted hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
            title={t('chatModule.toolbarClear')}>
            <Icon icon="fa-solid fa-eraser" />
          </button>
        </div>
      )}
    </div>
  )
}

export default ChatToolbar
