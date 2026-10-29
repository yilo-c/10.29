import React from 'react'
import Icon from '../Common/Icon'
import { useTranslation } from '../../i18n'
import MorningBrief from '../Todo/MorningBrief'
import type { ChatMessage } from './chatHelpers'

interface ChatMessageListProps {
  showMorningBrief: boolean
  hasMessages: boolean
  showHistory: boolean
  messages: ChatMessage[]
  loading: boolean
  streamingContent: string
  agentStatus: string[]
  messagesEndRef: React.RefObject<HTMLDivElement>
}

export const ChatMessageList: React.FC<ChatMessageListProps> = ({
  showMorningBrief, hasMessages, showHistory, messages,
  loading, streamingContent, agentStatus, messagesEndRef,
}) => {
  const { t } = useTranslation()
  return (
    <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2.5">
      {/* Morning Brief — only when no messages and no history open */}
      {showMorningBrief && !hasMessages && !showHistory && (
        <div className="flex-shrink-0">
          <MorningBrief />
        </div>
      )}
      {messages.length === 0 && !showMorningBrief && !showHistory && (
        <div className="flex flex-col items-center justify-center h-full text-center gap-3">
          <Icon icon="fa-solid fa-comment-dots text-3xl" style={{ color: 'var(--text-dim)' }} />
          <div className="text-xs" style={{ color: 'var(--text-muted)' }}>
            {t('ai.chat.placeholder')}
          </div>
        </div>
      )}
      {messages.map((msg, i) => (
        <div key={i} className={`flex items-start gap-2 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
          {msg.role === 'assistant' && (
            <div className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5"
              style={{ background: 'var(--hover-bg)' }}>
              <Icon icon="fa-solid fa-comment-dots text-xs text-fluent-blue/60" />
            </div>
          )}
          <div
            className={`max-w-[75%] rounded-xl px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap break-words ${
              msg.role === 'user'
                ? 'bg-fluent-blue/15 text-text-primary rounded-br-md'
                : 'bg-white/[0.04] text-text-secondary rounded-bl-md'
            }`}
          >
            {msg.content}
          </div>
        </div>
      ))}
      {/* Streaming message */}
      {(loading && streamingContent) ? (
        <div className="flex items-start gap-2 justify-start">
          <div className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5"
            style={{ background: 'var(--hover-bg)' }}>
            <Icon icon="fa-solid fa-comment-dots text-xs text-fluent-blue/60" />
          </div>
          <div className="max-w-[75%] rounded-xl px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap break-words bg-white/[0.04] text-text-secondary rounded-bl-md">
            {streamingContent}
          </div>
        </div>
      ) : null}
      {/* Agent / tool-calling status lines — metadata chunks from backend */}
      {loading && agentStatus.length > 0 && (
        <div className="flex items-start gap-2 justify-start pl-8">
          <div className="max-w-[75%] rounded-lg px-2.5 py-1.5 text-xs leading-relaxed whitespace-pre-wrap break-words bg-white/[0.02] border border-white/[0.04] text-text-secondary rounded-bl-md space-y-0.5">
            {agentStatus.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        </div>
      )}
      {loading && !streamingContent ? (
        <div className="flex items-start gap-2 justify-start">
          <div className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 mt-0.5"
            style={{ background: 'var(--hover-bg)' }}>
            <Icon icon="fa-solid fa-comment-dots text-xs text-fluent-blue/60" />
          </div>
          <div className="max-w-[75%] rounded-xl px-3 py-2 bg-white/[0.04] rounded-bl-md">
            <span className="inline-flex gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-white/40 animate-bounce" style={{ animationDelay: '300ms' }} />
            </span>
          </div>
        </div>
      ) : null}
      <div ref={messagesEndRef} />
    </div>
  )
}

export default ChatMessageList
