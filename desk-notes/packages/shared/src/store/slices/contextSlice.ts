import type { StateCreator } from 'zustand'
import type { Store } from '../types'

export interface ContextSlice {
  currentModule: string
  selectedNoteId: string | null
  selectedTodoId: string | null
  setCurrentContext: (module: string, noteId?: string | null, todoId?: string | null) => void
}

export const createContextSlice: StateCreator<Store, [], [], ContextSlice> = (set, _get, _api) => ({
  currentModule: 'chat',
  selectedNoteId: null,
  selectedTodoId: null,
  setCurrentContext: (module, noteId, todoId) => set({
    currentModule: module,
    selectedNoteId: noteId ?? null,
    selectedTodoId: todoId ?? null,
  }),
})
