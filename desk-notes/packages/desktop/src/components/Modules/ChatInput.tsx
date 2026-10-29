import React from 'react'
import Icon from '../Common/Icon'
import { useTranslation } from '../../i18n'

interface ChatInputProps {
  input: string
  onInputChange: (v: string) => void
  onKeyDown: (e: React.KeyboardEvent) => void
  loading: boolean
  onStop: () => void
  onSend: () => void
  searchMode: boolean
  onToggleSearch: () => void
  multiAgentMode: boolean
  onToggleMultiAgent: () => void
  deepMode: boolean
  onToggleDeep: () => void
  inputRef: React.RefObject<HTMLTextAreaElement>
}

export const ChatInput: React.FC<ChatInputProps> = ({
  input, onInputChange, onKeyDown, loading, onStop, onSend,
  searchMode, onToggleSearch, multiAgentMode, onToggleMultiAgent,
  deepMode, onToggleDeep, inputRef,
}) => {
  const { t } = useTranslation()
  return (
    <div className="flex-shrink-0 px-3 pb-3 pt-1">
      <div className="flex items-end gap-2 rounded-xl border px-3 py-2 transition-all"
        style={{
          background: 'var(--input-bg)',
          borderColor: 'var(--panel-border)',
        }}>
        <div className="relative group flex-shrink-0">
          <button
            onClick={onToggleSearch}
            className={`w-6 h-6 flex items-center justify-center rounded-md transition-all text-xs ${
              searchMode
                ? 'bg-fluent-blue/20 text-fluent-blue'
                : 'text-text-dim hover:text-text-secondary'
            }`}
            title={searchMode ? t('chatModule.searchModeOn') : t('chatModule.searchModeOff')}
          >
            <Icon icon="fa-solid fa-globe" />
          </button>
        </div>
        {/* Multi-Agent collaboration toggle — routes chat.send through Planner + Worker */}
        <div className="relative group flex-shrink-0">
          <button
            onClick={onToggleMultiAgent}
            className={`w-6 h-6 flex items-center justify-center rounded-md transition-all text-xs ${
              multiAgentMode
                ? 'bg-purple-500/20 text-purple-300'
                : 'text-text-dim hover:text-text-secondary'
            }`}
            title={multiAgentMode ? t('chatModule.multiAgentOn') : t('chatModule.multiAgentOff')}
          >
            <Icon icon="fa-solid fa-users" />
          </button>
        </div>
        {/* Deep kernel mode toggle — forces chat.send with params.kernel=true (AI gateway kernel) */}
        <div className="relative group flex-shrink-0">
          <button
            onClick={onToggleDeep}
            className={`w-6 h-6 flex items-center justify-center rounded-md transition-all text-xs ${
              deepMode
                ? 'bg-indigo-500/20 text-indigo-300'
                : 'text-text-dim hover:text-text-secondary'
            }`}
            title={t(deepMode ? 'chatModule.deepModeOn' : 'chatModule.deepModeOff')}
          >
            <Icon icon="fa-solid fa-brain" />
          </button>
        </div>
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => onInputChange(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={t('ai.chat.inputPlaceholder')}
          rows={1}
          className="flex-1 bg-transparent border-none outline-none resize-none text-sm leading-relaxed"
          style={{ color: 'var(--text-primary)', maxHeight: 120 }}
        />
        {loading ? (
          <button onClick={onStop}
            className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg bg-fluent-blue/15 text-fluent-blue hover:bg-fluent-blue/25 transition-all text-xs">
            <Icon icon="fa-solid fa-stop" />
          </button>
        ) : (
          <button onClick={onSend} disabled={!input.trim()}
            className="flex-shrink-0 w-7 h-7 flex items-center justify-center rounded-lg bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-30 disabled:cursor-not-allowed transition-all text-xs">
            <Icon icon={`fa-solid ${searchMode ? 'fa-magnifying-glass-arrow-right' : 'fa-arrow-up'}`} />
          </button>
        )}
      </div>
    </div>
  )
}

export default ChatInput
