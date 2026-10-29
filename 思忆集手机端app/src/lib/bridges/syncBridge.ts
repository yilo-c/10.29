/**
 * 同步 / 传输 / 阅读 桥 — 桌面 `sync.*` / `transfer.*` / `reading.*`。
 *
 * `sync.*` 是桌面云同步配置（不同于手机端本地的 contentSync.ts，那是 push 数据）。
 * `transfer.*` 是双端传送（截图/文件/文本）。
 * `reading.*` 是文章稍后读 + 推送内容。
 */
import { callRpc } from '../rpcClient'

/* ── Sync ── */
export interface SyncStatusRemote {
  enabled?: boolean
  configured?: boolean
  running?: boolean
  last_sync_at?: string | number
  [k: string]: unknown
}

export async function getSyncStatus(): Promise<SyncStatusRemote | null> {
  try { return await callRpc<SyncStatusRemote>('sync.status', {}) } catch { return null }
}

export async function syncNow(): Promise<boolean> {
  try { await callRpc('sync.sync_now', {}); return true } catch { return false }
}

export async function configureSync(args: {
  worker_url?: string
  token?: string
  register_secret?: string
  interval_seconds?: number
}): Promise<boolean> {
  try { await callRpc('sync.configure', args as Record<string, unknown>); return true } catch { return false }
}

export async function enableSync(): Promise<boolean> {
  try { await callRpc('sync.enable', {}); return true } catch { return false }
}
export async function disableSync(): Promise<boolean> {
  try { await callRpc('sync.disable', {}); return true } catch { return false }
}
export async function registerSyncDevice(name: string): Promise<unknown> {
  try { return await callRpc('sync.register_device', { name }) } catch { return null }
}

/* ── Transfer ── */
export interface TransferTokenInfo {
  token?: string
  expires_at?: string | number
  device_id?: string
  [k: string]: unknown
}

export async function transferTokenInfo(): Promise<TransferTokenInfo | null> {
  try { return await callRpc<TransferTokenInfo>('transfer.token_info', {}) } catch { return null }
}

export async function transferSend(type: string, data: unknown, targetDevice?: string): Promise<boolean> {
  try { await callRpc('transfer.send', { type, data, target_device: targetDevice }); return true } catch { return false }
}

export async function transferUpload(fileData: string, filename: string): Promise<{ path?: string; file_id?: string; size?: number; success?: boolean; error?: string } | null> {
  try {
    return await callRpc<{ path?: string; file_id?: string; size?: number; success?: boolean; error?: string }>(
      'transfer.upload',
      { file_data: fileData, filename },
    )
  } catch { return null }
}

export async function transferDownload(fileId: string): Promise<unknown> {
  try { return await callRpc('transfer.download', { file_id: fileId }) } catch { return null }
}

export async function transferHistory(limit = 50): Promise<unknown[]> {
  try {
    const res = await callRpc<unknown>('transfer.history', { limit })
    return Array.isArray(res) ? res : []
  } catch { return [] }
}

/* ── Reading（push/list/get/delete + article save/list/delete） ── */
export interface ReadingItem {
  id?: string | number
  url?: string
  title?: string
  content?: string
  created_at?: string | number
  [k: string]: unknown
}

export async function pushReading(args: { url: string; title?: string; content?: string }): Promise<ReadingItem | null> {
  try { return await callRpc<ReadingItem>('reading.push', args as Record<string, unknown>) } catch { return null }
}
export async function getReading(readingId: string | number): Promise<ReadingItem | null> {
  try { return await callRpc<ReadingItem>('reading.get', { reading_id: readingId }) } catch { return null }
}
export async function listReading(limit = 50): Promise<ReadingItem[]> {
  try {
    const res = await callRpc<unknown>('reading.list', { limit })
    return Array.isArray(res) ? (res as ReadingItem[]) : []
  } catch { return [] }
}
export async function deleteReading(readingId: string | number): Promise<boolean> {
  try { await callRpc('reading.delete', { reading_id: readingId }); return true } catch { return false }
}

export async function saveArticle(args: { url: string; title?: string; content?: string }): Promise<ReadingItem | null> {
  try { return await callRpc<ReadingItem>('reading.save_article', args as Record<string, unknown>) } catch { return null }
}
export async function listArticles(limit = 50): Promise<ReadingItem[]> {
  try {
    const res = await callRpc<unknown>('reading.list_articles', { limit })
    return Array.isArray(res) ? (res as ReadingItem[]) : []
  } catch { return [] }
}
export async function deleteArticle(articleId: string | number): Promise<boolean> {
  try { await callRpc('reading.delete_article', { article_id: articleId }); return true } catch { return false }
}
