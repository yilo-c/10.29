import type { WsClient } from '@desk-notes/shared'
import { swallowError } from './swallowError'

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
}

interface Conversation {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: ChatMessage[]
}

/** Tombstone set: IDs the user has explicitly deleted. Prevents re-import. */
function getDeletedIds(): Set<string> {
  try {
    const raw = localStorage.getItem('siyi-deleted-conversations')
    return new Set(raw ? JSON.parse(raw) : [])
  } catch { return new Set() }
}
export function markConversationDeleted(id: string) {
  const ids = getDeletedIds()
  ids.add(id)
  localStorage.setItem('siyi-deleted-conversations', JSON.stringify([...ids].slice(-200)))
}

/**
 * Load backend chat sessions and merge them into local conversations.
 * Returns conversations that exist on the backend but not locally.
 */
export async function loadBackendConversations(
  client: WsClient,
  localConversations: Conversation[],
): Promise<Conversation[]> {
  const imported: Conversation[] = []
  const deletedIds = getDeletedIds()

  // 1. Load from chat_conversations store
  try {
    const summaries = await client.call<Array<{ id: string; title: string; updated_at: string; message_count: number }>>('chat.list_conversations', {})
    if (Array.isArray(summaries)) {
      for (const s of summaries) {
        if (deletedIds.has(s.id)) continue
        if (localConversations.some(c => c.id === s.id)) continue
        const full = await client.call<{ title: string; messages: ChatMessage[] } | null>('chat.get_conversation', { session_id: s.id })
        if (!full || !full.messages) continue
        const messages = full.messages
        imported.push({
          id: s.id,
          title: s.title || full.title || '新对话',
          createdAt: messages[0]?.timestamp || Date.now(),
          updatedAt: s.updated_at ? new Date(s.updated_at).getTime() : (messages[messages.length - 1]?.timestamp || Date.now()),
          messages,
        })
      }
    }
  } catch (e) { /* backend chat_conversations not available */ swallowError('chatSync:45', e) }

  // 2. Load from chat_session_repository (PyQt6 legacy sessions)
  try {
    const sessions = await client.call<Array<{ id: number; title?: string; created_at?: number }>>('chat.history', { limit: 50 })
    if (!Array.isArray(sessions) || sessions.length === 0) return imported

    for (const session of sessions) {
      const sessionId = String(session.id)

      // Skip if already mapped
      if (localConversations.some(c => (c as any).backendSessionId === sessionId) ||
          imported.some(c => c.id === `backend-${sessionId}`)) continue

      const messages = await client.call<ChatMessage[]>('chat.history', { session_id: sessionId, limit: 100 })
      if (!Array.isArray(messages) || messages.length === 0) continue

      const firstMsg = messages.find(m => m.role === 'user')
      const title = firstMsg
        ? (firstMsg.content.length > 30 ? firstMsg.content.slice(0, 30) + '…' : firstMsg.content)
        : `会话 #${sessionId}`

      imported.push({
        id: `backend-${sessionId}`,
        title: session.title || title,
        createdAt: session.created_at || (messages[0]?.timestamp || Date.now()),
        updatedAt: messages[messages.length - 1]?.timestamp || Date.now(),
        messages,
        backendSessionId: sessionId,
      } as Conversation & { backendSessionId: string })
    }
  } catch (e) { /* backend sessions not available */ swallowError('chatSync:76', e) }

  return imported
}
