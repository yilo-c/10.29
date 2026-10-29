// Re-export store and setTrashHandler from shared
export { useStore, setTrashHandler } from '@desk-notes/shared'

// ── Inject platform-specific adapters ────────────────────────────────────────
import { setNotesLoader, useStore as _useStore } from '@desk-notes/shared'
import { loadAllNotes, saveNoteToFile, deleteNoteFile } from '../utils/fileStore'
setNotesLoader(loadAllNotes)

// ── Sync floatingNotes changes to individual .md files (Electron only) ─────
import { setSaveStatus } from './saveTracker'
import { errorStore } from './errorStore'

let saveTimer: ReturnType<typeof setTimeout> | null = null
let prevNoteIds = new Set<string>()
const dirtyNoteIds = new Set<string>()
// V1-4：本地 .md 落盘串成一条链，flush 时可 await（杜绝并发写互相穿插）
let fileChain: Promise<void> = Promise.resolve()

async function flushSave(notes?: Array<Record<string, unknown>>) {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  const target = notes ?? _useStore.getState().floatingNotes as unknown as Record<string, unknown>[]
  let failed = false
  for (const note of target) {
    if (!note.id || typeof note.id !== 'string') continue // 跳过无 id 笔记
    try {
      await saveNoteToFile(note)
    } catch (e: unknown) {
      failed = true
      console.error('[store] Failed to save note:', note.id, e)
      errorStore.error('便签保存失败，请检查磁盘空间')
    }
  }
  dirtyNoteIds.clear()
  setSaveStatus(failed ? 'error' : 'saved')
}

/** 排队一次本地落盘并返回可 await 的 Promise */
function queueFileFlush(notes?: Array<Record<string, unknown>>): Promise<void> {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  const target = notes ?? (_useStore.getState().floatingNotes as unknown as Array<Record<string, unknown>>)
  fileChain = fileChain.then(() => flushSave(target)).catch((e: unknown) => {
    console.error('[store] file flush chain failed:', e)
  })
  return fileChain
}

/**
 * V1-4「打不丢字」统一 flush 入口：
 * 先等后端 notes.update（noteSlice 内的防抖 + 串行 + 乐观锁链）落定，
 * 再等本地 .md 文件写完。失焦 / 切页 / 关闭编辑器 / 窗口隐藏都走这里。
 */
export async function flushNotePersistence(noteId?: string): Promise<void> {
  try {
    await _useStore.getState().flushNoteSave(noteId)
  } catch (e: unknown) {
    console.error('[store] flushNoteSave failed:', e)
  }
  if (noteId) {
    const note = _useStore.getState().floatingNotes.find(n => n.id === noteId) as unknown as Record<string, unknown> | undefined
    await queueFileFlush(note ? [note] : [])
    return
  }
  await queueFileFlush()
}

// Fire-and-forget save for beforeunload: ipcRenderer.invoke enqueues the message
// synchronously, so the main process will complete the write even if the renderer
// window closes before the Promise resolves.
function flushSaveFireAndForget() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null }
  const notes = _useStore.getState().floatingNotes as unknown as Record<string, unknown>[]
  for (const note of notes) {
    if (!note.id || typeof note.id !== 'string') continue // 跳过无 id 笔记
    saveNoteToFile(note).catch((e) => console.error('[store] Failed to save note:', note.id, e))
  }
  dirtyNoteIds.clear()
}

{
  let prevNotesRef: unknown = null
  _useStore.subscribe((state) => {
    const notes = state.floatingNotes

    // V1-4：只在笔记数组真的变化时重新排防抖定时器。
    // 旧实现在任意 state 变化（聊天流式、focusTimer 等）都会 clearTimeout 重排，
    // 只要界面持续有别的事件跳动，150ms 防抖就永远到不了期 → 本地 .md 一直不落盘。
    if (notes === prevNotesRef) {
      return
    }
    prevNotesRef = notes

    // Track which notes actually changed (shallow compare by id+content+title)
    const currentIds = new Set(notes.map(n => n.id).filter((id) => typeof id === 'string'))
    for (const n of notes) {
      if (n.id && typeof n.id === 'string') dirtyNoteIds.add(n.id)
    }

    // Debounced save to .md files (150ms — balances batching vs close-window data loss)
    if (saveTimer) clearTimeout(saveTimer)
    setSaveStatus('saving')
    const snapshot = notes as unknown as Array<Record<string, unknown>>
    saveTimer = setTimeout(() => { void queueFileFlush(snapshot) }, 150)

    // Detect deleted notes and clean up files
    for (const id of prevNoteIds) {
      if (!currentIds.has(id)) {
        dirtyNoteIds.delete(id)
        deleteNoteFile(id).catch((e) => console.error('[useStore] deleteNoteFile failed:', e))
      }
    }
    prevNoteIds = currentIds
  })
}

// V1-4 ①：切页 / 关闭编辑器（editingNoteId、activeModule 变化）时强制 flush。
// 保存链在 store 侧（组件销毁不影响在途 Promise），这里保证防抖窗口内的字不滞留。
{
  let prevEditingId: string | null | undefined
  let prevModule: string | undefined
  _useStore.subscribe((state) => {
    if (state.editingNoteId === prevEditingId && state.activeModule === prevModule) return
    prevEditingId = state.editingNoteId
    prevModule = state.activeModule
    void flushNotePersistence()
  })
}

// Flush pending saves before window closes
// Uses fire-and-forget: ipcRenderer.invoke sends the IPC message synchronously
// before returning the Promise, so main process will complete writes.
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => {
    // 后端写入链先同步排队（fire-and-forget，IPC 入队即由主进程完成落盘）
    void _useStore.getState().flushNoteSave().catch((e: unknown) => console.error('[store] flushNoteSave on unload failed:', e))
    flushSaveFireAndForget()
  })

  // V1-4 ③：窗口 hidden（最小化 / 切到其他程序 / 锁屏）时常驻保存，
  // 不等用户手动切页，避免后台被冻结前还有防抖窗口内的编辑没落盘。
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'hidden') return
    void flushNotePersistence()
  })
}
