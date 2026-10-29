import { useStore } from './useStore'
import { undoManager } from './undoManager'
import { getTranslations } from '../i18n'

/**
 * Tracks content edits with a 2s debounce.
 * Coalesces edits to the same note into one undo step.
 * When the user stops typing for 2s, the edit session finalizes and
 * an undo entry is pushed (if content actually changed).
 */

let editTimer: ReturnType<typeof setTimeout> | null = null
let pendingNoteId: string | null = null
let pendingSnapshot: string | null = null

function finalizeEdit(): void {
  editTimer = null
  if (!pendingNoteId || pendingSnapshot === null) {
    pendingNoteId = null
    pendingSnapshot = null
    return
  }

  const noteId = pendingNoteId
  const snapshot = pendingSnapshot

  pendingNoteId = null
  pendingSnapshot = null

  // If note was deleted during edit session, discard the pending edit
  const note = useStore.getState().floatingNotes.find(n => n.id === noteId)
  if (!note) return

  const currentContent = note.content || ''

  if (currentContent !== snapshot) {
    const redoSnapshot = currentContent
    undoManager.push(
      getTranslations().undoLabels.editNote,
      () => { useStore.getState().updateFloatingNote(noteId, { content: snapshot }) },
      () => { useStore.getState().updateFloatingNote(noteId, { content: redoSnapshot }) },
    )
  }
}

/**
 * Called on every content edit keystroke.
 * Starts or extends a 2s edit session for the given note.
 */
export function onContentEdit(noteId: string, preEditContent?: string): void {
  if (pendingNoteId !== noteId) {
    // Switched to a different note — finalize previous session
    finalizeEdit()
    pendingNoteId = noteId
    pendingSnapshot = preEditContent !== undefined
      ? preEditContent
      : (() => { const note = useStore.getState().floatingNotes.find(n => n.id === noteId); return note ? note.content || '' : '' })()
  } else if (pendingSnapshot === null) {
    pendingSnapshot = preEditContent !== undefined
      ? preEditContent
      : (() => { const note = useStore.getState().floatingNotes.find(n => n.id === noteId); return note ? note.content || '' : '' })()
  }

  if (editTimer) clearTimeout(editTimer)
  editTimer = setTimeout(finalizeEdit, 2000)
}

/**
 * Force-finalize any pending edit session (e.g. on unmount).
 */
export function flushPendingEdit(): void {
  if (editTimer) {
    clearTimeout(editTimer)
    finalizeEdit()
  }
}

