import type { TodoSlice } from './slices/todoSlice'
import type { NoteSlice } from './slices/noteSlice'
import type { ConversationSlice } from './slices/conversationSlice'
import type { ChatSessionSlice } from './slices/chatSessionSlice'
import type { UiSlice } from './slices/uiSlice'
import type { ContextSlice } from './slices/contextSlice'
import type { PermissionSlice } from './slices/permissionSlice'

export interface Store extends TodoSlice, NoteSlice, ConversationSlice, ChatSessionSlice, UiSlice, ContextSlice, PermissionSlice {}
