/* ── 附件工具：siyi-file:// URI 解析、类型识别、Markdown 附件提取 ── */

export type AttachmentKind = 'image' | 'video' | 'audio' | 'pdf' | 'document' | 'archive' | 'file'

export interface MarkdownAttachment {
  uri: string
  label: string
  path: string
  kind: AttachmentKind
  markdown: string
}

export interface AttachmentRef {
  uri: string
  path: string
  name: string
  mime?: string
  kind: AttachmentKind
  size?: number
}

/** siyi-file:// URI 前缀 */
const SIYI_FILE_PREFIX = 'siyi-file://'

/** MIME → AttachmentKind 映射 */
const MIME_KIND_MAP: Record<string, AttachmentKind> = {
  'image/jpeg': 'image',
  'image/png': 'image',
  'image/gif': 'image',
  'image/webp': 'image',
  'image/svg+xml': 'image',
  'video/mp4': 'video',
  'video/webm': 'video',
  'video/quicktime': 'video',
  'audio/mpeg': 'audio',
  'audio/wav': 'audio',
  'audio/ogg': 'audio',
  'application/pdf': 'pdf',
  'application/msword': 'document',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'document',
  'application/vnd.ms-excel': 'document',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'document',
  'application/zip': 'archive',
  'application/x-rar-compressed': 'archive',
  'application/x-7z-compressed': 'archive',
  'application/gzip': 'archive',
}

/** 扩展名 → AttachmentKind 映射（MIME 未知时的 fallback） */
const EXT_KIND_MAP: Record<string, AttachmentKind> = {
  jpg: 'image', jpeg: 'image', png: 'image', gif: 'image', webp: 'image', svg: 'image', bmp: 'image',
  mp4: 'video', webm: 'video', mov: 'video', avi: 'video', mkv: 'video',
  mp3: 'audio', wav: 'audio', ogg: 'audio', flac: 'audio', aac: 'audio',
  pdf: 'pdf',
  doc: 'document', docx: 'document', xls: 'document', xlsx: 'document', ppt: 'document', pptx: 'document',
  zip: 'archive', rar: 'archive', '7z': 'archive', tar: 'archive', gz: 'archive',
}

/**
 * 解析 siyi-file:// URI，返回文件路径。
 * 格式: siyi-file://notes/{noteId}/{filename}
 */
export function parseSiyiFileUri(uri: string): { path: string } | null {
  if (!uri || typeof uri !== 'string') return null
  if (!uri.startsWith(SIYI_FILE_PREFIX)) return null
  const path = uri.slice(SIYI_FILE_PREFIX.length)
  if (!path) return null
  return { path }
}

/**
 * 根据文件名和 MIME 推断附件类型。
 */
export function guessAttachmentKind(name: string, mime?: string): AttachmentKind {
  if (mime && MIME_KIND_MAP[mime]) return MIME_KIND_MAP[mime]
  const ext = (name.split('.').pop() || '').toLowerCase()
  return EXT_KIND_MAP[ext] || 'file'
}

/**
 * 从 Markdown 正文中提取所有 siyi-file:// 附件引用。
 * 匹配格式: ![alt](siyi-file://...) 和 [label](siyi-file://...)
 */
export function extractAttachmentsFromMarkdown(content: string): MarkdownAttachment[] {
  if (!content) return []

  const results: MarkdownAttachment[] = []
  const seen = new Set<string>()

  // 匹配 ![alt](siyi-file://path) 图片
  const imgRe = /!\[([^\]]*)\]\(siyi-file:\/\/([^)]+)\)/g
  let match: RegExpExecArray | null
  while ((match = imgRe.exec(content)) !== null) {
    const uri = SIYI_FILE_PREFIX + match[2]
    if (seen.has(uri)) continue
    seen.add(uri)
    const label = match[1] || uri.split('/').pop() || 'attachment'
    results.push({
      uri,
      label,
      path: match[2],
      kind: 'image',
      markdown: match[0],
    })
  }

  // 匹配 [label](siyi-file://path) 文件/链接（排除已匹配的图片）
  const linkRe = /\[([^\]]*)\]\(siyi-file:\/\/([^)]+)\)/g
  while ((match = linkRe.exec(content)) !== null) {
    const uri = SIYI_FILE_PREFIX + match[2]
    if (seen.has(uri)) continue
    seen.add(uri)
    const label = match[1]
    const kind = guessAttachmentKind(match[2])
    results.push({
      uri,
      label,
      path: match[2],
      kind,
      markdown: match[0],
    })
  }

  return results
}

/**
 * 构建附件 Markdown 引用文本。
 * 图片使用 ![]()，其他文件使用 []()。
 */
export function buildAttachmentMarkdown(input: { uri: string; name: string; kind: AttachmentKind }): string {
  const label = input.name
  if (input.kind === 'image') {
    return `![${label}](${input.uri})`
  }
  return `[${label}](${input.uri})`
}

/**
 * 生成 siyi-file:// URI。
 */
export function toSiyiFileUri(path: string): string {
  return SIYI_FILE_PREFIX + path.replace(/\\/g, '/')
}

/**
 * 从 Markdown 中统计附件数量（按类型分组）。
 */
export function countAttachmentsByKind(content: string): Record<AttachmentKind, number> {
  const counts: Record<string, number> = { image: 0, video: 0, audio: 0, pdf: 0, document: 0, archive: 0, file: 0 }
  const attachments = extractAttachmentsFromMarkdown(content)
  for (const a of attachments) {
    counts[a.kind] = (counts[a.kind] || 0) + 1
  }
  return counts as Record<AttachmentKind, number>
}

/**
 * 生成附件摘要文本（用于列表卡片展示）。
 * 例: "2 张图片 · 1 个文件"
 */
export function formatAttachmentSummary(content: string): string {
  const counts = countAttachmentsByKind(content)
  const parts: string[] = []
  const labels: Record<AttachmentKind, string> = {
    image: '张图片', video: '个视频', audio: '个音频',
    pdf: '个 PDF', document: '个文档', archive: '个压缩包', file: '个文件',
  }
  for (const [kind, count] of Object.entries(counts)) {
    if (count > 0) {
      parts.push(`${count} ${labels[kind as AttachmentKind] || '个附件'}`)
    }
  }
  return parts.join(' · ')
}
