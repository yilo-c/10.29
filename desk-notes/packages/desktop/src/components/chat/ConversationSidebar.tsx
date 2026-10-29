import { useMemo, useState } from 'react'
import Icon from '../Common/Icon'


export interface ConversationSummary {
  id: string
  title: string
  updatedAt?: string
}

interface ConversationSidebarProps {
  conversations: ConversationSummary[]
  activeId: string | null
  onSelect: (id: string) => void
  onNew: () => void
  onDelete?: (id: string) => void
  onRename?: (id: string, title: string) => void
}

export function ConversationSidebar({ conversations, activeId, onSelect, onNew, onDelete, onRename }: ConversationSidebarProps) {
  const [query, setQuery] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const filteredConversations = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase()
    if (!normalizedQuery) return conversations
    return conversations.filter((conversation) => conversation.title.toLowerCase().includes(normalizedQuery))
  }, [conversations, query])

  const startRename = (conversation: ConversationSummary) => {
    setEditingId(conversation.id)
    setEditingTitle(conversation.title)
  }

  const commitRename = () => {
    if (editingId && onRename && editingTitle.trim()) {
      onRename(editingId, editingTitle.trim())
    }
    setEditingId(null)
  }

  return (
    <aside className="conversation-sidebar" aria-label="会话导航" data-testid="conversation-sidebar">
      <div className="conversation-sidebar-heading">
        <span className="agent-kicker">小问 Agent</span>
        <h2>任务中心</h2>
        <p>把复杂任务变成可审计的步骤</p>
      </div>
      <button type="button" className="conversation-new-button" onClick={onNew}>
        <Icon icon="fa-solid fa-plus text-xs" /> 新建任务
      </button>
      <label className="conversation-search">
        <Icon icon="fa-solid fa-magnifying-glass text-text-muted text-xs" />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索会话..." aria-label="搜索会话和任务" />
      </label>
      <div className="conversation-list">
        <span className="conversation-list-label">最近会话</span>
        {filteredConversations.map((conversation) => (
          <div
            key={conversation.id}
            role="button"
            tabIndex={0}
            className={`conversation-item${activeId === conversation.id ? ' conversation-item-active' : ''}`}
            onClick={() => onSelect(conversation.id)}
            onDoubleClick={() => startRename(conversation)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(conversation.id) } }}
            title="双击重命名"
          >
            <span className="conversation-item-icon">
              <Icon icon="fa-solid fa-comment-dots" />
            </span>
            {editingId === conversation.id ? (
              <span className="conversation-item-copy" onClick={(e) => e.stopPropagation()}>
                <input
                  autoFocus
                  value={editingTitle}
                  onChange={(e) => setEditingTitle(e.target.value)}
                  onBlur={commitRename}
                  onKeyDown={(e) => { if (e.key === 'Enter') commitRename(); if (e.key === 'Escape') setEditingId(null) }}
                  className="conversation-rename-input"
                  aria-label="重命名会话"
                />
              </span>
            ) : (
              <span className="conversation-item-copy">
                <strong>{conversation.title}</strong>
                <small>{conversation.updatedAt ?? '本地会话'}</small>
              </span>
            )}
            {onDelete && (
              <button
                aria-label={`删除 ${conversation.title}`}
                className="conversation-item-delete"
                onClick={(e) => { e.stopPropagation(); onDelete(conversation.id) }}
                title="删除会话"
              >
                <Icon icon="fa-solid fa-trash-can" />
              </button>
            )}
          </div>
        ))}
        {filteredConversations.length === 0 && (
          <p className="agent-empty">{query ? '没有匹配的会话' : '暂无会话'}</p>
        )}
      </div>
    </aside>
  )
}
