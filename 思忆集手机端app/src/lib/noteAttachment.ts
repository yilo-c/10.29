/* ── 手机端笔记附件工具：原生文件选择 + 上传/下载/保存/缓存 ── */

import type { AttachmentKind, AttachmentRef } from '@desk-notes/shared'
import { tokenManager } from './tokenManager'
import { swallowError } from './swallowError'

// ═══════════════════════════════════════════════════════════════
// IndexedDB 离线缓存
// ═══════════════════════════════════════════════════════════════

const DB_NAME = 'siyi-attachment-cache'
// v2: switch payload from raw dataUrl string to {dataUrl, ts} for LRU.
// onupgradeneeded migrates v1 → v2 by wrapping any existing entries.
const DB_VERSION = 2
const STORE_NAME = 'attachments'
const TS_INDEX = 'ts'
// P1-5 Cache size cap. We delete OLDEST_DELETE_BATCH stale entries every
// time count > MAX_ENTRIES so the I/O is amortized — not "wipe-everything"
// like the previous bug.
const MAX_ENTRIES = 200
const OLDEST_DELETE_BATCH = 20

type CacheEntry = { dataUrl: string; ts: number }

function _openDB(): Promise<IDBPDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      const tx = req.transaction!
      let store: IDBObjectStore
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        store = db.createObjectStore(STORE_NAME)
      } else {
        store = tx.objectStore(STORE_NAME)
        // v1 → v2: wrap raw dataUrl strings into {dataUrl, ts}
        const cursorReq = store.openCursor()
        cursorReq.onsuccess = () => {
          const cursor = cursorReq.result
          if (!cursor) return
          const v = cursor.value
          if (typeof v === 'string') {
            const entry: CacheEntry = { dataUrl: v, ts: Date.now() }
            cursor.update(entry)
          }
          cursor.continue()
        }
      }
      // Add the 'ts' index if missing — used to evict the oldest N entries
      if (!store.indexNames.contains(TS_INDEX)) {
        store.createIndex(TS_INDEX, 'ts')
      }
    }
    req.onsuccess = () => resolve(req.result as unknown as IDBPDatabase)
    req.onerror = () => reject(req.error)
  })
}

interface IDBPDatabase extends IDBDatabase {
  // typed shim
}

export async function getCachedAttachment(path: string): Promise<string | null> {
  try {
    const db = await _openDB()
    return new Promise((resolve) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      const req = store.get(path)
      req.onsuccess = () => {
        const value = req.result
        if (!value) return resolve(null)
        // Support both v1 (raw string) and v2 ({dataUrl, ts}) shapes.
        if (typeof value === 'string') {
          // Promote to v2 with current timestamp (LRU "touch")
          store.put({ dataUrl: value, ts: Date.now() }, path)
          return resolve(value)
        }
        // LRU touch: bump ts so recently-read entries survive eviction
        store.put({ dataUrl: value.dataUrl, ts: Date.now() }, path)
        resolve(value.dataUrl ?? null)
      }
      req.onerror = () => resolve(null)
    })
  } catch { return null }
}

export async function cacheAttachment(path: string, dataUrl: string): Promise<void> {
  try {
    const db = await _openDB()
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const entry: CacheEntry = { dataUrl, ts: Date.now() }
    store.put(entry, path)

    // P1-5 修复：超出阈值时只删最旧的 N 条而不是清空全部。
    // 历史 bug：count > 200 时遍历整个 store 全删 → 缓存超限即全清，
    // 下次又得整个网络重拉。
    const countReq = store.count()
    countReq.onsuccess = () => {
      if (countReq.result <= MAX_ENTRIES) return
      // Delete OLDEST_DELETE_BATCH oldest entries by ts ascending.
      const index = store.index(TS_INDEX)
      const cursorReq = index.openCursor()
      let removed = 0
      cursorReq.onsuccess = () => {
        const cursor = cursorReq.result
        if (!cursor || removed >= OLDEST_DELETE_BATCH) return
        cursor.delete()
        removed++
        cursor.continue()
      }
    }
  } catch (e) { /* silent */ swallowError('noteAttachment:113', e) }
}

// ═══════════════════════════════════════════════════════════════
// 原生文件选择（Capacitor）
// ═══════════════════════════════════════════════════════════════

export async function pickFromCamera(): Promise<{ dataUrl: string; name: string } | null> {
  try {
    const { Camera, CameraResultType, CameraSource } = await import('@capacitor/camera')
    const photo = await Camera.getPhoto({
      resultType: CameraResultType.DataUrl,
      source: CameraSource.Camera,
      quality: 85,
    })
    if (!photo?.dataUrl) return null
    const ext = photo.format || 'jpeg'
    return { dataUrl: photo.dataUrl, name: `camera_${Date.now()}.${ext}` }
  } catch (e) {
    console.warn('[camera] pick failed:', e)
    return null
  }
}

export async function pickFromGallery(multiple = false): Promise<{ dataUrl: string; name: string }[]> {
  try {
    const { Camera, CameraResultType, CameraSource } = await import('@capacitor/camera')
    const photo = await Camera.getPhoto({
      resultType: CameraResultType.DataUrl,
      source: CameraSource.Photos,
      quality: 85,
      ...(multiple ? { allowEditing: false } : {}),
    })
    if (!photo?.dataUrl) return []
    const ext = photo.format || 'jpeg'
    return [{ dataUrl: photo.dataUrl, name: `photo_${Date.now()}.${ext}` }]
  } catch (e) {
    console.warn('[camera] gallery pick failed:', e)
    return []
  }
}

export async function pickVideo(): Promise<{ dataUrl: string; name: string } | null> {
  try {
    // Note: Capacitor Filesystem would be used here for native video pick
    // Fall back to file input for video (Capacitor Filesystem pick is limited)
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'video/*'
    const file = await new Promise<File | null>((resolve) => {
      input.onchange = () => resolve(input.files?.[0] ?? null)
      input.click()
    })
    if (!file) return null

    const dataUrl = await fileToDataUrl(file)
    return { dataUrl, name: file.name }
  } catch (e) {
    console.warn('[video] pick failed:', e)
    return null
  }
}

export async function pickAnyFile(): Promise<{ dataUrl: string; name: string } | null> {
  const input = document.createElement('input')
  input.type = 'file'
  const file = await new Promise<File | null>((resolve) => {
    input.onchange = () => resolve(input.files?.[0] ?? null)
    input.click()
  })
  if (!file) return null
  const dataUrl = await fileToDataUrl(file)
  return { dataUrl, name: file.name }
}

// ═══════════════════════════════════════════════════════════════
// 文件保存到设备
// ═══════════════════════════════════════════════════════════════

export async function saveToDevice(dataUrl: string, fileName: string): Promise<boolean> {
  try {
    const { Filesystem, Directory } = await import('@capacitor/filesystem')
    const base64 = (dataUrl.split(',')[1] || dataUrl)
    await Filesystem.writeFile({
      path: fileName,
      data: base64,
      directory: Directory.Documents,
    })
    return true
  } catch (e) {
    console.warn('[save] failed:', e)
    // Fallback: try share
    return shareFile(dataUrl, fileName)
  }
}

export async function saveToGallery(dataUrl: string, fileName: string): Promise<boolean> {
  try {
    const { Filesystem, Directory } = await import('@capacitor/filesystem')
    const base64 = (dataUrl.split(',')[1] || dataUrl)
    await Filesystem.writeFile({
      path: fileName,
      data: base64,
      directory: Directory.Data,  // app-internal storage; gallery access needs MediaStore on newer Android
    })
    // Try sharing as fallback
    return true
  } catch {
    return shareFile(dataUrl, fileName)
  }
}

// ═══════════════════════════════════════════════════════════════
// 文件分享
// ═══════════════════════════════════════════════════════════════

export async function shareFile(dataUrl: string, fileName: string): Promise<boolean> {
  try {
    const { Share } = await import('@capacitor/share')
    await Share.share({
      title: fileName,
      text: fileName,
      url: dataUrl,
      dialogTitle: '分享文件',
    })
    return true
  } catch { return false }
}

// ═══════════════════════════════════════════════════════════════
// 复制到剪贴板
// ═══════════════════════════════════════════════════════════════

export async function copyFileToClipboard(dataUrl: string, kind: AttachmentKind): Promise<boolean> {
  try {
    if (kind === 'image') {
      // Copy image to clipboard
      const blob = await (await fetch(dataUrl)).blob()
      await navigator.clipboard.write([
        new ClipboardItem({ [blob.type]: blob }),
      ])
    } else {
      // Copy text link
      await navigator.clipboard.writeText(dataUrl)
    }
    return true
  } catch { return false }
}

export async function copyAttachmentMarkdown(ref: AttachmentRef): Promise<void> {
  const md = ref.kind === 'image'
    ? `![${ref.name}](${ref.uri})`
    : `[${ref.name}](${ref.uri})`
  try {
    await navigator.clipboard.writeText(md)
  } catch (e) { /* silent */ swallowError('noteAttachment:268', e) }
}

// ═══════════════════════════════════════════════════════════════
// 通用工具
// ═══════════════════════════════════════════════════════════════

export function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('Failed to read file'))
    reader.readAsDataURL(file)
  })
}

export function normalizeFilename(name: string): string {
  return name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 200)
}

export function guessAttachmentKind(name: string, mime?: string): AttachmentKind {
  const ext = (name.split('.').pop() || '').toLowerCase()
  const mimeMap: Record<string, AttachmentKind> = {
    'image/jpeg': 'image', 'image/png': 'image', 'image/gif': 'image', 'image/webp': 'image',
    'video/mp4': 'video', 'video/webm': 'video', 'video/quicktime': 'video',
    'audio/mpeg': 'audio', 'audio/wav': 'audio',
    'application/pdf': 'pdf',
    'application/msword': 'document',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'document',
  }
  if (mime && mimeMap[mime]) return mimeMap[mime]
  const extMap: Record<string, AttachmentKind> = {
    jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', webp: 'image',
    mp4: 'video', mov: 'video', pdf: 'pdf',
    doc: 'document', docx: 'document', zip: 'archive', rar: 'archive',
  }
  return extMap[ext] || 'file'
}

// ═══════════════════════════════════════════════════════════════
// 上传到后端 + 本地缓存
// ═══════════════════════════════════════════════════════════════


// Get configured WsClient using tokenManager baseUrl
function _getWsUrl(): string {
  const baseUrl = tokenManager.baseUrl
  const port = tokenManager.backendPort
  if (baseUrl) {
    try {
      const ip = new URL(baseUrl).hostname
      return `ws://${ip}:${port}/ws`
    } catch (e) { swallowError('noteAttachment:320', e) }
  }
  return 'ws://127.0.0.1:18765/ws'
}
export async function uploadNoteAttachment(
  noteId: string,
  file: File | { dataUrl: string; name: string },
  options?: { onProgress?: (pct: number) => void }
): Promise<AttachmentRef> {
  const dataUrl = file instanceof File ? await fileToDataUrl(file) : file.dataUrl
  const name = normalizeFilename(file instanceof File ? file.name : file.name)
  options?.onProgress?.(30)

  // Cache locally first
  const path = `notes/${noteId}/${name}`
  await cacheAttachment(path, dataUrl)
  options?.onProgress?.(60)


  // Upload to backend if available
  try {
    const { getWsClient: getClient }: any = await import('@desk-notes/shared'); const client = getClient({ url: _getWsUrl() })
    const result: any = await client.call('transfer.upload', {
        name,
        data: dataUrl,
        folder: `notes/${noteId}`,
      })
      if (result && result.success) {
        options?.onProgress?.(100)
        return {
          uri: `siyi-file://${path}`,
          path: result.path || path,
          name,
          kind: guessAttachmentKind(name, file instanceof File ? file.type : undefined),
          size: file instanceof File ? file.size : undefined,
          mime: file instanceof File ? file.type : undefined,
        }
      }
  } catch (e) { /* offline: return local-only ref */ swallowError('noteAttachment:358', e) }
  options?.onProgress?.(100)

  return {
    uri: `siyi-file://${path}`,
    path,
    name,
    kind: guessAttachmentKind(name, file instanceof File ? file.type : undefined),
    size: file instanceof File ? file.size : undefined,
  }
}

export async function downloadAttachment(path: string): Promise<string | null> {
  // Check local cache first
  const cached = await getCachedAttachment(path)
  if (cached) return cached

  // Try backend
  try {
    const { getWsClient: getClient }: any = await import('@desk-notes/shared'); const client = getClient({ url: _getWsUrl() })
    const result: any = await client.call('transfer.download', { path })
      if (result.success && result.data) {
        // Cache for offline use
        await cacheAttachment(path, result.data)
        return result.data
      }
  } catch (e) { /* silent */ swallowError('noteAttachment:384', e) }

  return null
}

export function toSiyiFileUri(path: string): string {
  return `siyi-file://${path.replace(/\\/g, '/')}`
}

export function parseSiyiFileUri(uri: string): { path: string } | null {
  if (!uri || !uri.startsWith('siyi-file://')) return null
  const path = uri.slice('siyi-file://'.length)
  return path ? { path } : null
}
