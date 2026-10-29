// Task #9 对话附件（2026-09-18）：前端侧附件采集与序列化（纯逻辑，可单测）。
//
// 与后端 backend/handlers/chat_paths/attachments.py 的守卫口径对齐：
// - 单文件 base64 ≤ 6MB（超限直接不收，给用户可见错误而不是静默丢）；
// - 图片 ≤ 4 张（后端每张一次 vision 调用，有真实成本）；
// - 单次发送总附件 ≤ 8 个。
// 后端对未收编类型返回诚实"未解析"标注；前端对明显不支持的二进制
// （非图片且非文本类）直接拦下并提示，避免白占 WS 帧。

export interface PendingAttachment {
  id: string
  name: string
  mime: string
  sizeBytes: number
  dataBase64: string
  isImage: boolean
}

export interface WireAttachment {
  name: string
  mime: string
  data_base64: string
}

export const MAX_B64_CHARS = 6 * 1024 * 1024
export const MAX_IMAGES = 4
export const MAX_ATTACHMENTS = 8

const TEXT_MIMES = /^(text\/|application\/(json|xml|x-yaml|yaml|javascript|x-sh|sql|toml|x-ndjson|csv)$)/i
const TEXT_EXT = /\.(txt|md|markdown|py|js|ts|tsx|jsx|json|ya?ml|toml|ini|cfg|csv|tsv|log|sql|sh|bat|ps1|html|css|xml|rst|go|rs|java|[ch]|cpp|rb|php)$/i

export function isTextLike(name: string, mime: string): boolean {
  if (TEXT_MIMES.test(mime || '')) return true
  return TEXT_EXT.test((name || '').toLowerCase())
}

export function isAcceptable(name: string, mime: string): boolean {
  return (mime || '').startsWith('image/') || isTextLike(name, mime)
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const dataUrl = String(reader.result || '')
      const comma = dataUrl.indexOf(',')
      resolve(comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl)
    }
    reader.onerror = () => reject(reader.error || new Error('read failed'))
    reader.readAsDataURL(file)
  })
}

let _seq = 0

/** 把一批 File 转成待发送附件；返回成功项与逐条拒绝原因（不静默丢）。 */
export async function collectAttachments(
  files: File[],
  existing: PendingAttachment[] = [],
): Promise<{ accepted: PendingAttachment[]; rejected: { name: string; reason: string }[] }> {
  const accepted: PendingAttachment[] = []
  const rejected: { name: string; reason: string }[] = []
  let images = existing.filter((a) => a.isImage).length
  let total = existing.length

  for (const file of files) {
    if (total >= MAX_ATTACHMENTS) {
      rejected.push({ name: file.name, reason: '附件数量已达上限（8 个）' })
      continue
    }
    const isImage = (file.type || '').startsWith('image/')
    if (isImage && images >= MAX_IMAGES) {
      rejected.push({ name: file.name, reason: '图片数量已达上限（4 张）' })
      continue
    }
    if (!isAcceptable(file.name, file.type)) {
      rejected.push({ name: file.name, reason: '暂只支持文本类文件与图片' })
      continue
    }
    // 以文本/图片本体估 base64 体积（4/3 膨胀），超 6MB 直接拒
    if (Math.ceil((file.size * 4) / 3) > MAX_B64_CHARS) {
      rejected.push({ name: file.name, reason: '文件过大（编码后超过 6MB）' })
      continue
    }
    try {
      const dataBase64 = await fileToBase64(file)
      if (dataBase64.length > MAX_B64_CHARS) {
        rejected.push({ name: file.name, reason: '文件过大（编码后超过 6MB）' })
        continue
      }
      accepted.push({
        id: `att-${Date.now()}-${_seq++}`,
        name: file.name,
        mime: file.type || '',
        sizeBytes: file.size,
        dataBase64,
        isImage,
      })
      total += 1
      if (isImage) images += 1
    } catch (err) {
      rejected.push({ name: file.name, reason: `读取失败：${err instanceof Error ? err.message : String(err)}` })
    }
  }
  return { accepted, rejected }
}

/** 序列化成 chat.send params.attachments（无附件时返回 undefined 省字段）。 */
export function toWireParam(attachments: PendingAttachment[]): WireAttachment[] | undefined {
  if (!attachments.length) return undefined
  return attachments.map((a) => ({ name: a.name, mime: a.mime, data_base64: a.dataBase64 }))
}

/** 人类可读大小，用于 chip 标签。 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

/** UTF-8 字符串 → base64（兼容中文等多字节，btoa 只能处理 Latin1）。 */
export function utf8ToBase64(s: string): string {
  const bytes = new TextEncoder().encode(s)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return btoa(bin)
}

/**
 * 由纯文本直接构造一个待发送附件（第二档 #4：复制即候选上下文）。
 * 后端 chat_paths/attachments.py 会把 text/plain 解码为文本正文掺入提示。
 */
export function makeTextAttachment(text: string, name = '剪贴板文本.txt'): PendingAttachment {
  const bytes = new TextEncoder().encode(text)
  return {
    id: `att-txt-${Date.now()}-${_seq++}`,
    name,
    mime: 'text/plain',
    sizeBytes: bytes.length,
    dataBase64: utf8ToBase64(text),
    isImage: false,
  }
}
