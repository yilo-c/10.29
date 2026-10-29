/* ── 手机端附件预览：图片/视频/文件 + 保存/分享/复制 ── */

import { useState, useEffect, useCallback } from 'react'
import type { AttachmentKind } from '@desk-notes/shared'

interface AttachmentData {
  uri: string
  label: string
  kind: AttachmentKind
  path: string
}

interface Props {
  attachment: AttachmentData
  inline?: boolean
}

const _dataUrlCache = new Map<string, string>()

async function _loadAttachment(path: string): Promise<string | null> {
  const cached = _dataUrlCache.get(path)
  if (cached) return cached
  const { downloadAttachment } = await import('../../../lib/noteAttachment')
  const dataUrl = await downloadAttachment(path)
  if (dataUrl) {
    _dataUrlCache.set(path, dataUrl)
    if (_dataUrlCache.size > 100) {
      const firstKey = _dataUrlCache.keys().next().value
      if (firstKey) _dataUrlCache.delete(firstKey)
    }
  }
  return dataUrl
}

export function NoteAttachmentPreview({ attachment, inline }: Props) {
  const [dataUrl, setDataUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)

  const load = useCallback(async () => {
    if (dataUrl) return
    setLoading(true)
    setError(null)
    try {
      const url = await _loadAttachment(attachment.path)
      if (url) setDataUrl(url)
      else setError('加载失败')
    } catch {
      setError('加载失败')
    } finally {
      setLoading(false)
    }
  }, [attachment.path, dataUrl])

  useEffect(() => {
    if (attachment.kind === 'image' && !inline) load()
  }, [attachment.kind, inline, load])

  const handleSave = useCallback(async () => {
    const url = dataUrl || await _loadAttachment(attachment.path)
    if (!url) return
    const filepath = attachment.path.split('/').pop() || 'file'
    if (attachment.kind === 'image') {
      const { saveToGallery } = await import('../../../lib/noteAttachment')
      await saveToGallery(url, filepath)
    } else {
      const { saveToDevice } = await import('../../../lib/noteAttachment')
      await saveToDevice(url, filepath)
    }
  }, [attachment, dataUrl])

  const handleShare = useCallback(async () => {
    const url = dataUrl || await _loadAttachment(attachment.path)
    if (!url) return
    const { shareFile } = await import('../../../lib/noteAttachment')
    const name = attachment.path.split('/').pop() || 'file'
    await shareFile(url, name)
  }, [attachment, dataUrl])

  const handleCopy = useCallback(async () => {
    const url = dataUrl || await _loadAttachment(attachment.path)
    if (!url) return
    const { copyFileToClipboard } = await import('../../../lib/noteAttachment')
    await copyFileToClipboard(url, attachment.kind)
  }, [attachment, dataUrl])

  if (loading) {
    return (
      <div style={{ padding: 12, opacity: 0.5, fontSize: 'var(--text-caption-2)' }}>
        加载中...
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: 12, color: '#ef4444', fontSize: 'var(--text-caption-2)', display: 'flex', gap: 8, alignItems: 'center' }}>
        <span>{error}</span>
        <button onClick={load} style={{ background: 'none', border: 'none', color: 'var(--accent)', cursor: 'pointer', fontSize: 'var(--text-caption-1)', textDecoration: 'underline' }}>重试</button>
      </div>
    )
  }

  if (attachment.kind === 'image') {
    return (
      <div style={{ margin: '4px 0', position: 'relative' }}>
        {dataUrl ? (
          <>
            <img
              src={dataUrl}
              alt={attachment.label}
              onClick={() => setMenuOpen(!menuOpen)}
              style={{
                maxWidth: inline ? 80 : '100%',
                maxHeight: inline ? 80 : 300,
                borderRadius: 8,
                objectFit: 'cover',
                cursor: 'pointer',
              }}
            />
            {menuOpen && (
              <ImageMenu onSave={handleSave} onShare={handleShare} onCopy={handleCopy} onClose={() => setMenuOpen(false)} />
            )}
          </>
        ) : (
          <FileCard label={attachment.label} kind="image" onLoad={load} />
        )}
      </div>
    )
  }

  if (attachment.kind === 'video') {
    return (
      <div style={{ margin: '4px 0' }}>
        {dataUrl ? (
          <div>
            <video controls style={{ maxWidth: '100%', maxHeight: 300, borderRadius: 8 }}>
              <source src={dataUrl} />
            </video>
            <ActionRow onSave={handleSave} onShare={handleShare} />
          </div>
        ) : (
          <FileCard label={attachment.label} kind={attachment.kind} onLoad={load} onSave={handleSave} onShare={handleShare} />
        )}
      </div>
    )
  }

  return (
    <FileCard
      label={attachment.label}
      kind={attachment.kind}
      onLoad={load}
      onSave={handleSave}
      onShare={handleShare}
    />
  )
}


function FileCard({ label, kind, onLoad, onSave, onShare }: {
  label: string; kind: AttachmentKind; onLoad: () => void; onSave?: () => void; onShare?: () => void
}) {
  const iconMap: Record<string, string> = {
    pdf: '\u{1F4C4}', audio: '\u{1F3B5}', document: '\u{1F4DD}', archive: '\u{1F4E6}',
    video: '\u{1F3AC}', image: '\u{1F5BC}', file: '\u{1F4CE}',
  }
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '8px 12px', borderRadius: 8,
      background: 'var(--color-surface, #f5f5f5)',
      margin: '4px 0', flexWrap: 'wrap',
    }}>
      <span style={{ fontSize: 20 }}>{iconMap[kind] || iconMap.file}</span>
      <span style={{ flex: 1, fontSize: 14, minWidth: 60, wordBreak: 'break-all' }}>{label}</span>
      <div style={{ display: 'flex', gap: 4 }}>
        {onLoad && <Chip label="打开" onClick={onLoad} />}
        {onSave && <Chip label="保存" onClick={onSave} />}
        {onShare && <Chip label="分享" onClick={onShare} />}
      </div>
    </div>
  )
}

function Chip({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      padding: '2px 8px', borderRadius: 6, border: '0.5px solid var(--border-subtle)',
      background: 'var(--bg-secondary)', cursor: 'pointer',
      fontSize: 'var(--text-caption-2)', color: 'var(--text-secondary)',
    }}>
      {label}
    </button>
  )
}

function ActionRow({ onSave, onShare }: { onSave: () => void; onShare: () => void }) {
  return (
    <div style={{ display: 'flex', gap: 8, padding: '4px 0' }}>
      <Chip label="保存" onClick={onSave} />
      <Chip label="分享" onClick={onShare} />
    </div>
  )
}

function ImageMenu({ onSave, onShare, onCopy, onClose }: {
  onSave: () => void; onShare: () => void; onCopy: () => void; onClose: () => void
}) {
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 999 }} />
      <div style={{
        position: 'absolute', top: '100%', left: 0, zIndex: 1000,
        background: 'var(--bg-secondary)', borderRadius: 12,
        padding: 4, boxShadow: '0 4px 16px rgba(0,0,0,0.2)',
        display: 'flex', gap: 2, flexDirection: 'column',
      }}>
        <MenuBtn label="保存到相册" onClick={onSave} />
        <MenuBtn label="分享" onClick={onShare} />
        <MenuBtn label="复制图片" onClick={onCopy} />
      </div>
    </>
  )
}

function MenuBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} style={{
      padding: '8px 16px', border: 'none', background: 'transparent',
      cursor: 'pointer', fontSize: 'var(--text-footnote)', color: 'var(--text-primary)',
      borderRadius: 8, textAlign: 'left',
    }}>
      {label}
    </button>
  )
}
