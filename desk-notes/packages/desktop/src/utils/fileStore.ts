/**
 * File-based storage module.
 *
 * Provides:
 *  - Custom Zustand storage backend (`fileStorage`) that persists to store.json
 *  - Note-level helpers for reading/writing individual .md files
 *  - Browser fallback: when Electron IPC is unavailable, use localStorage
 *
 * Data directory (Electron):
 *   {app.getPath('userData')}/siyi-notes/
 *   ├── notes/{id}.md     — individual notes with YAML frontmatter
 *   ├── store.json         — Zustand state (todos, categories, settings, …)
 *   └── trash.json         — trash items
 */

// ── Detect platform ─────────────────────────────────────────────────────────

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei?.fileStore


// ── Initialization ──────────────────────────────────────────────────────────

let _initialized = false

export async function initFileStore(): Promise<{ migrated: boolean }> {
  if (!isElectron || _initialized) return { migrated: false }
  _initialized = true
  return await ei!.fileStore.init()
}

// ── Zustand custom storage backend (read/write store.json) ──────────────────
// This implements StateStorage (string-based). In the persist config we wrap
// it with createJSONStorage() which handles JSON parse/stringify.

const STORE_FILE = 'store.json'

export const fileStorage = {
  getItem: async (name: string): Promise<string | null> => {
    if (!isElectron) {
      // Browser fallback: use localStorage
      return localStorage.getItem(name)
    }
    // Read from store.json file
    return await ei!.fileStore.readFile(STORE_FILE)
  },

  setItem: async (name: string, value: string): Promise<void> => {
    if (!isElectron) {
      localStorage.setItem(name, value)
      return
    }
    await ei!.fileStore.writeFile(STORE_FILE, value)
  },

  removeItem: async (name: string): Promise<void> => {
    if (!isElectron) {
      localStorage.removeItem(name)
      return
    }
    await ei!.fileStore.deleteFile(STORE_FILE)
  },
}

// ── Note-level file helpers ─────────────────────────────────────────────────

async function loadNoteFromFile(noteId: string): Promise<Record<string, unknown> | null> {
  if (!isElectron) return null
  return await ei!.fileStore.readNote(noteId)
}

export async function saveNoteToFile(note: Record<string, unknown>): Promise<void> {
  if (!isElectron) return
  if (!note.id || typeof note.id !== 'string') {
    console.warn('[fileStore] saveNoteToFile skipped: note has no valid id', note)
    return
  }
  await ei!.fileStore.writeNote(note.id, note)
}

export async function deleteNoteFile(noteId: string): Promise<void> {
  if (!isElectron) return
  await ei!.fileStore.deleteFile(`notes/${noteId}.md`)
}

export async function listNoteIds(): Promise<string[]> {
  if (!isElectron) return []
  return await ei!.fileStore.listNotes()
}

export async function loadAllNotes(): Promise<Record<string, unknown>[]> {
  if (!isElectron) return []
  const ids = await listNoteIds()
  const notes: Record<string, unknown>[] = []
  for (const id of ids) {
    if (id === 'undefined') continue // 跳过毒文件
    try {
      const note = await loadNoteFromFile(id)
      if (note && note.id && typeof note.id === 'string') notes.push(note)
    } catch (e: unknown) {
      console.error('[fileStore] Failed to load note:', id, e instanceof Error ? e.message : e)
    }
  }
  return notes
}

// ── Trash helpers ───────────────────────────────────────────────────────────

const TRASH_FILE = 'trash.json'

export async function readTrashFile(): Promise<string | null> {
  if (!isElectron) return null
  return await ei!.fileStore.readFile(TRASH_FILE)
}

export async function writeTrashFile(content: string): Promise<void> {
  if (!isElectron) return
  await ei!.fileStore.writeFile(TRASH_FILE, content)
}

