/* ── 桌面端 siyi-file:// URI 解析器 ── */

import { getWsClient } from '@desk-notes/shared'

/**
 * 检查 URI 是否为 siyi-file:// 格式。
 */
export function isSiyiFileUri(uri: string): boolean {
  return typeof uri === 'string' && uri.startsWith('siyi-file://')
}

/**
 * 从 siyi-file:// URI 解析文件路径。
 */
export function parseSiyiFilePath(uri: string): string | null {
  if (!isSiyiFileUri(uri)) return null
  const path = uri.slice('siyi-file://'.length)
  return path || null
}

/**
 * 通过后端 transfer.download RPC 获取文件内容。
 * 返回 base64 data URL。
 */
export async function resolveSiyiFile(uri: string): Promise<string | null> {
  const path = parseSiyiFilePath(uri)
  if (!path) return null

  try {
    const client = getWsClient()
    if (client.status !== 'connected') {
      console.warn('[siyi-file] Backend not connected, cannot resolve:', path)
      return null
    }
    const result = await client.call<{ success?: boolean; data?: string; error?: string }>('transfer.download', { path })
    if (result?.success && result.data) {
      return result.data
    }
    console.warn('[siyi-file] Download failed:', result?.error)
    return null
  } catch (err) {
    console.warn('[siyi-file] Resolve error:', err)
    return null
  }
}

/**
 * 从路径推断文件名。
 */
export function guessFileName(path: string): string {
  return path.split('/').pop() || path.split('\\').pop() || 'file'
}

/**
 * 从文件名推断是否为图片。
 */
export function isImageFile(name: string): boolean {
  return /\.(jpg|jpeg|png|gif|webp|svg|bmp)$/i.test(name)
}

/**
 * 是否为笔记资产相对路径（方案 A，2026-09-19）：`assets/<name>` 或 `./assets/<name>`。
 * 排除协议URI / 绝对路径 / 远程 / data URI。
 */
export function isRelativeAssetPath(uri: string): boolean {
  if (!uri || isSiyiFileUri(uri)) return false
  if (/^[a-z][a-z0-9+.-]*:/i.test(uri)) return false // http:/data:/siyi-file: 等一切协议
  if (/^[A-Za-z]:[\/]/.test(uri) || uri.startsWith('/')) return false
  return /^\.?\.?\/?assets\//i.test(uri)
}

/**
 * 相对路径资产 → 经 notes.asset.get RPC 取回 data URL。
 */
export async function resolveRelativeAsset(uri: string): Promise<string | null> {
  const name = uri.replace(/^\.\//, '').replace(/^\.?\.?\/?assets\//i, '')
  if (!name) return null
  try {
    const client = getWsClient()
    if (client.status !== 'connected') return null
    const result = await client.call<{ success?: boolean; data?: string; mime?: string }>('notes.asset.get', { name })
    if (result?.success && result.data) {
      return `data:${result.mime || 'image/png'};base64,${result.data}`
    }
    return null
  } catch {
    return null
  }
}
