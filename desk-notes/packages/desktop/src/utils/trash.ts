import { readTrashFile, writeTrashFile } from './fileStore'
import { errorStore } from '../store/errorStore'

export interface TrashItem {
  id: string
  type: 'todo' | 'note'
  data: unknown
  deletedAt: number
}

const TRASH_KEY = 'desk-notes-trash'

// Notify subscribers on every mutation so consumers can refresh.
const listeners = new Set<() => void>()
function notify() {
  listeners.forEach(fn => fn())
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/**
 * Initialize trash from file store (Electron).
 * Call once at startup before any trash reads.
 */
export async function initTrash(): Promise<void> {
  const fileData = await readTrashFile()
  if (fileData) {
    try {
      // Validate JSON before writing to localStorage
      JSON.parse(fileData)
      localStorage.setItem(TRASH_KEY, fileData)
    } catch (e: unknown) {
      console.warn('[trash] invalid JSON in trash file, ignoring:', e)
      errorStore.warn('回收站文件异常，已重新初始化')
    }
  }
}

function loadTrash(): TrashItem[] {
  try {
    const raw = localStorage.getItem(TRASH_KEY)
    if (!raw) return []
    return JSON.parse(raw) as TrashItem[]
  } catch (e) {
    console.warn('[trash] failed to parse trash data:', e)
    errorStore.warn('回收站数据读取异常')
    return []
  }
}

function saveTrash(items: TrashItem[]): void {
  try {
    const json = JSON.stringify(items)
    localStorage.setItem(TRASH_KEY, json)
    // Also persist to file in the background (Electron only)
    writeTrashFile(json).catch(e => { console.error('[trash] file write FAILED:', e); errorStore.error('回收站文件写入失败') })
  } catch (e) {
    console.error('[trash] localStorage write FAILED:', e)
    errorStore.error('存储空间不足，请清理回收站')
  }
}

export function moveToTrash(item: TrashItem): void {
  const items = loadTrash()
  const filtered = items.filter(i => i.id !== item.id)
  filtered.push(item)
  saveTrash(filtered)
  notify()
}

export function restoreFromTrash(id: string): TrashItem | null {
  const items = loadTrash()
  const idx = items.findIndex(i => i.id === id)
  if (idx === -1) return null
  const [item] = items.splice(idx, 1)
  saveTrash(items)
  notify()
  return item
}

export function permanentlyDelete(id: string): void {
  const items = loadTrash().filter(i => i.id !== id)
  saveTrash(items)
  notify()
}

export function clearTrash(): void {
  saveTrash([])
  notify()
}

export function getTrashItems(): TrashItem[] {
  return loadTrash()
}
