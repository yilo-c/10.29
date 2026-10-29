import React from 'react'
import Icon from '../Common/Icon'
import { useTranslation } from '../../i18n'
import { formatTime, type Conversation } from './chatHelpers'

interface ChatHistorySidebarProps {
  show: boolean
  onClose: () => void
  searchQuery: string
  onSearchChange: (v: string) => void
  grouped: { label: string; items: Conversation[] }[]
  filteredCount: number
  activeConversationId: string | null
  onNewChat: () => void
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}

export const ChatHistorySidebar: React.FC<ChatHistorySidebarProps> = ({
  show, searchQuery, onSearchChange, grouped, filteredCount,
  activeConversationId, onNewChat, onSelect, onDelete, onClose,
}) => {
  const { t } = useTranslation()
  if (!show) return null
  return (
    <div
      className="w-56 flex-shrink-0 border-r flex flex-col"
      style={{ borderColor: 'var(--separator)', background: 'var(--panel-bg-solid)' }}
    >
      <div className="px-3 py-2.5 border-b flex items-center justify-between" style={{ borderColor: 'var(--separator)' }}>
        <span className="text-xs font-medium uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>
          {t('chatModule.sidebarHistory')}
        </span>
        <button onClick={onClose}
          className="w-5 h-5 flex items-center justify-center rounded text-text-muted hover:text-text-secondary hover:bg-white/[0.06] transition-all text-2xs">
          <Icon icon="fa-solid fa-xmark" />
        </button>
      </div>

      {/* Search */}
      <div className="px-2 pt-2 pb-1">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg border text-xs"
          style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)' }}>
          <Icon icon="fa-solid fa-search text-text-dim text-2xs" />
          <input value={searchQuery} onChange={e => onSearchChange(e.target.value)}
            placeholder={t('chatModule.sidebarSearchPlaceholder')}
            className="w-full bg-transparent border-none outline-none text-xs"
            style={{ color: 'var(--text-primary)' }} />
        </div>
      </div>

      {/* New chat button */}
      <div className="px-2 py-1">
        <button onClick={onNewChat}
          className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary">
          <Icon icon="fa-solid fa-plus text-2xs" />
          {t('chatModule.sidebarNewChat')}
        </button>
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-2">
        {grouped.map(group => (
          <div key={group.label}>
            <div className="text-2xs font-medium uppercase tracking-wider px-1 py-1.5" style={{ color: 'var(--text-muted)' }}>
              {group.label}
            </div>
            {group.items.map(c => (
              <div key={c.id}
                onClick={() => onSelect(c.id)}
                className={`group flex items-center gap-1.5 px-2 py-1.5 rounded-lg cursor-pointer text-xs transition-all ${
                  c.id === activeConversationId ? 'bg-white/[0.08] text-text-primary' : 'text-white/55 hover:bg-white/[0.04] hover:text-white/75'
                }`}>
                <Icon icon="fa-regular fa-comment text-[8px] flex-shrink-0 opacity-50" />
                <span className="flex-1 truncate">{c.title}</span>
                <span className="text-[8px] opacity-40 flex-shrink-0">{formatTime(c.updatedAt)}</span>
                <button onClick={e => { e.stopPropagation(); onDelete(c.id) }}
                  className="opacity-0 group-hover:opacity-100 w-4 h-4 flex items-center justify-center rounded text-red-400/50 hover:text-red-400 hover:bg-red-400/[0.08] transition-all text-[8px] flex-shrink-0">
                  <Icon icon="fa-solid fa-trash-can" />
                </button>
              </div>
            ))}
          </div>
        ))}
        {filteredCount === 0 && (
          <div className="text-center py-6 text-xs" style={{ color: 'var(--text-muted)' }}>
            {searchQuery ? t('chatModule.sidebarNoMatch') : t('chatModule.sidebarEmpty')}
          </div>
        )}
      </div>
    </div>
  )
}

export default ChatHistorySidebar
