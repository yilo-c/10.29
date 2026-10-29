/* ── 手机端笔记附件工具栏：原生 Capacitor 拍照/相册/视频/文件/OCR ── */

import { useState, useCallback } from 'react'
import type { AttachmentKind } from '@desk-notes/shared'

interface Props {
  noteId: string
  onInsert: (markdown: string) => void
  onUploadStart?: () => void
  onUploadEnd?: () => void
  onError?: (msg: string) => void
}

export function NoteAttachmentToolbar({ noteId, onInsert, onUploadStart, onUploadEnd, onError }: Props) {
  const [uploading, setUploading] = useState(false)

  const startUpload = useCallback(() => {
    setUploading(true)
    onUploadStart?.()
  }, [onUploadStart])

  const endUpload = useCallback(() => {
    setUploading(false)
    onUploadEnd?.()
  }, [onUploadEnd])

  const handleInsertFile = useCallback(async (file: File | { dataUrl: string; name: string }, _kind: AttachmentKind) => {
    startUpload()
    try {
      const { uploadNoteAttachment } = await import('../../../lib/noteAttachment')
      const ref = await uploadNoteAttachment(noteId, file)
      const md = ref.kind === 'image'
        ? `![${ref.name}](${ref.uri})`
        : `[${ref.name}](${ref.uri})`
      onInsert(md)
    } catch (e) {
      onError?.((e as Error).message || '上传失败')
    } finally {
      endUpload()
    }
  }, [noteId, onInsert, startUpload, endUpload, onError])

  const handleCamera = useCallback(async () => {
    startUpload()
    try {
      const { pickFromCamera } = await import('../../../lib/noteAttachment')
      const result = await pickFromCamera()
      if (result) {
        await handleInsertFile(result, 'image')
      }
    } catch (e) {
      onError?.('拍照失败')
    } finally {
      endUpload()
    }
  }, [handleInsertFile, startUpload, endUpload, onError])

  const handleGallery = useCallback(async () => {
    startUpload()
    try {
      const { pickFromGallery } = await import('../../../lib/noteAttachment')
      const results = await pickFromGallery(true)
      for (const r of results) {
        await handleInsertFile(r, 'image')
      }
    } catch (e) {
      onError?.('选取图片失败')
    } finally {
      endUpload()
    }
  }, [handleInsertFile, startUpload, endUpload, onError])

  const handleVideo = useCallback(async () => {
    startUpload()
    try {
      const { pickVideo } = await import('../../../lib/noteAttachment')
      const result = await pickVideo()
      if (result) {
        await handleInsertFile(result, 'video')
      }
    } catch (e) {
      onError?.('选择视频失败')
    } finally {
      endUpload()
    }
  }, [handleInsertFile, startUpload, endUpload, onError])

  const handleFile = useCallback(async () => {
    startUpload()
    try {
      const { pickAnyFile } = await import('../../../lib/noteAttachment')
      const result = await pickAnyFile()
      if (result) {
        const ext = (result.name.split('.').pop() || '').toLowerCase()
        const kind: AttachmentKind = /mp4|mov|webm/.test(ext) ? 'video' : /pdf/.test(ext) ? 'pdf' : /doc|docx/.test(ext) ? 'document' : 'file'
        await handleInsertFile(result, kind)
      }
    } catch (e) {
      onError?.('选择文件失败')
    } finally {
      endUpload()
    }
  }, [handleInsertFile, startUpload, endUpload, onError])

  const handleOCR = useCallback(async () => {
    startUpload()
    try {
      const { pickFromCamera } = await import('../../../lib/noteAttachment')
      const result = await pickFromCamera()
      if (!result) { endUpload(); return }
      const { ocrExtractBase64 } = await import('../../../lib/bridges')
      const text = await ocrExtractBase64(result.dataUrl)
      if (text) {
        onInsert(text)
      } else {
        onError?.('未识别到文字')
      }
    } catch (e) {
      onError?.('OCR 识别失败')
    } finally {
      endUpload()
    }
  }, [onInsert, startUpload, endUpload, onError])

  return (
    <div style={{ display: 'flex', gap: 4, padding: '4px 0' }}>
      <ToolBtn icon="🖼️" title="相册" onClick={handleGallery} disabled={uploading} />
      <ToolBtn icon="📷" title="拍照" onClick={handleCamera} disabled={uploading} />
      <ToolBtn icon="🎬" title="视频" onClick={handleVideo} disabled={uploading} />
      <ToolBtn icon="🔍" title="OCR" onClick={handleOCR} disabled={uploading} />
      <ToolBtn icon="📎" title="文件" onClick={handleFile} disabled={uploading} />
    </div>
  )
}

function ToolBtn({ icon, title, onClick, disabled }: { icon: string; title: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={{
        width: 36, height: 36, borderRadius: 8,
        border: '1px solid var(--color-border, #ddd)',
        background: disabled ? 'var(--bg-tertiary)' : 'var(--color-surface, #fff)',
        cursor: disabled ? 'not-allowed' : 'pointer',
        fontSize: 16, opacity: disabled ? 0.4 : 1,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}
    >
      {icon}
    </button>
  )
}
