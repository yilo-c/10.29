import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { onContentEdit, flushPendingEdit } from '@desk-notes/shared'
import { useScreenWake } from '../../../hooks/useScreenWake'
import { Button } from '../../ui'
import NotesFormatToolbar, { type ToolbarAction } from './NotesFormatToolbar'
import NoteAiAssistant from './NoteAiAssistant'
import { NoteAttachmentToolbar } from './NoteAttachmentToolbar'
import { NoteAttachmentPreview } from './NoteAttachmentPreview'
import type { FloatingNote } from '@desk-notes/shared'
import { swallowError } from '../../../lib/swallowError'
import { relatedByText, autoTag, generateSummary } from '../../../lib/bridges'

interface NoteEditorSheetProps {
  note: FloatingNote | null
  open: boolean
  quickCapture?: boolean
  onClose: () => void
  onUpdate: (id: string, data: Partial<FloatingNote>) => void
  onAddNote?: (data: { title: string; content: string; isQuickCapture?: boolean }) => void
}

export default function NoteEditorSheet({ note, open, quickCapture, onClose, onUpdate, onAddNote }: NoteEditorSheetProps) {
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [aiOpen, setAiOpen] = useState(false)
  const [selRange, setSelRange] = useState<{ start: number; end: number }>({ start: 0, end: 0 })
  const [previewMode, setPreviewMode] = useState<'editor' | 'split' | 'preview'>('editor')
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const autoSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const autoSaveData = useRef({ title: '', content: '', noteId: '' })
  const onUpdateRef = useRef(onUpdate)
  onUpdateRef.current = onUpdate
  autoSaveData.current = { title, content, noteId: note?.id ?? '' }

  useScreenWake(open)

  useEffect(() => {
    if (note) {
      setTitle(note.title || '')
      setContent(note.content || '')
    } else {
      setTitle('')
      setContent('')
    }
    setPreviewMode('editor')
  }, [note])

  // 记忆回响：打开笔记时自动查找相关旧记忆
  useEffect(() => {
    if (!note?.content || note.content.length < 10) return
    let cancelled = false
    ;(async () => {
      try {
        const res = await relatedByText(note.content.slice(0, 500), 3)
        if (!cancelled && Array.isArray(res) && res.length > 0) {
          setRelatedMemories(res.map((m: any) => m.text || m.content || m.summary || '').filter(Boolean))
        }
      } catch (e) { swallowError('NoteEditorSheet:54', e) }
    })()
    return () => { cancelled = true }
  }, [note?.id, note?.content])

  const triggerAutoSave = useCallback(() => {
    if (!autoSaveData.current.noteId) return
    if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current)
    autoSaveTimer.current = setTimeout(() => {
      const data = autoSaveData.current
      if (data.noteId) {
        onUpdateRef.current(data.noteId, {
          title: data.title || '无标题',
          content: data.content,
          updatedAt: Date.now(),
          editMode: 'markdown',
        })
      }
    }, 1500)
  }, [])

  useEffect(() => {
    return () => {
      if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current)
    }
  }, [])

  const handleContentChange = useCallback((value: string) => {
    setContent(value)
    if (note) { onContentEdit(note.id); triggerAutoSave() }
  }, [note, triggerAutoSave])

  const handleTitleChange = useCallback((value: string) => {
    setTitle(value)
    if (note) { onContentEdit(note.id); triggerAutoSave() }
  }, [note, triggerAutoSave])

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const file = e.clipboardData.files?.[0]
    if (!file || !file.type.startsWith('image/')) return

    e.preventDefault()
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = reader.result as string
      const markdown = `![${file.name || '图片'}](${base64})`
      const ta = textareaRef.current
      if (!ta) return
      const start = ta.selectionStart
      const end = ta.selectionEnd
      const newVal = content.slice(0, start) + markdown + content.slice(end)
      handleContentChange(newVal)
      setTimeout(() => {
        ta.focus()
        const pos = start + markdown.length
        ta.setSelectionRange(pos, pos)
      }, 0)
    }
    reader.readAsDataURL(file)
  }, [content, handleContentChange])

  useEffect(() => {
    return () => { if (note) flushPendingEdit() }
  }, [note])

  // 检测内容是否有修改
  const hasUnsavedChanges = useMemo(() => {
    if (!note) {
      // 新建笔记：有标题或内容就算有修改
      return Boolean(title.trim() || content.trim())
    }
    // 编辑模式：和原始值对比
    return (title !== (note.title || '')) || (content !== (note.content || ''))
  }, [note, title, content])

  const handleSave = () => {
    if (autoSaveTimer.current) clearTimeout(autoSaveTimer.current)
    if (note) {
      onUpdate(note.id, {
        title: title || '无标题',
        content,
        updatedAt: Date.now(),
        editMode: 'markdown',
      })
    } else {
      onAddNote?.({ title: title || '无标题', content, isQuickCapture: quickCapture })
    }
    onClose()
  }

  const handleClose = useCallback(() => {
    if (hasUnsavedChanges) {
      const confirmed = window.confirm('有未保存的内容，确定要放弃吗？')
      if (!confirmed) return
    }
    onClose()
  }, [hasUnsavedChanges, onClose])

  // ── 附件上传状态 ──
  const [uploading, setUploading] = useState(false)
  const [summaryText, setSummaryText] = useState<string | null>(null)
  const [relatedMemories, setRelatedMemories] = useState<string[]>([])
  const [summaryLoading, setSummaryLoading] = useState(false)
  const [toastMsg, setToastMsg] = useState<string | null>(null)

  const handleInsertAttachment = useCallback((markdown: string) => {
    const ta = textareaRef.current
    if (!ta) {
      setContent(prev => prev + '\n' + markdown)
      handleContentChange(content + '\n' + markdown)
      return
    }
    const start = ta.selectionStart
    const end = ta.selectionEnd
    const newVal = content.slice(0, start) + markdown + content.slice(end)
    handleContentChange(newVal)
    setTimeout(() => {
      ta.focus()
      const pos = start + markdown.length
      ta.setSelectionRange(pos, pos)
    }, 0)
  }, [content, handleContentChange])

  const noteId = note?.id || 'draft'

  const charCount = content.length
  const wordCount = content.replace(/[\s\n\r]+/g, ' ').trim().split(' ').filter(Boolean).length

  const miniBtnStyle: React.CSSProperties = {
    width: 32, height: 28, borderRadius: 6, fontSize: 14,
    border: '0.5px solid var(--border-subtle)', background: 'var(--bg-secondary)',
    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 0
  }
  const titleText = note ? '编辑笔记' : quickCapture ? '快速捕获' : '新建笔记'
  if (!open) return null
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 2000, display: 'flex', flexDirection: 'column', background: 'var(--bg-primary)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 'var(--safe-top) var(--space-4) 12px', borderBottom: '0.5px solid var(--border-subtle)', flexShrink: 0 }}>
        <span style={{ fontSize: 'var(--text-headline)', fontWeight: 600, color: 'var(--text-primary)' }}>{titleText}</span>
        <button onClick={handleClose} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: 'var(--text-body)', cursor: 'pointer', padding: '4px 8px' }}>完成</button>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: 'var(--space-4)' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <input
          value={title}
          onChange={e => handleTitleChange(e.target.value)}
          placeholder="标题"
          autoFocus
          style={{
            width: '100%',
            padding: '8px 0',
            border: 'none',
            borderBottom: '1px solid var(--border-subtle)',
            background: 'transparent',
            color: 'var(--text-primary)',
            fontSize: 'var(--text-body)',
            fontWeight: 600,
            outline: 'none',
          }}
        />



        {/* 附件工具栏 */}
        <NoteAttachmentToolbar
          noteId={noteId}
          onInsert={handleInsertAttachment}
          onUploadStart={() => setUploading(true)}
          onUploadEnd={() => setUploading(false)}
          onError={(msg) => setToastMsg(msg)}
        />

        {/* 附件预览（已有附件的内联显示） */}
        {note && (
          <InlineAttachmentPreviews content={content} />
        )}

        {/* AI 操作按钮 */}
        <div style={{ display: 'flex', gap: 4, padding: '2px 0' }}>
          <button onClick={async () => {
            if (!content.trim() || summaryLoading) return
            setSummaryLoading(true)
            try {
              const text = await generateSummary(content.slice(0, 1000))
              setSummaryText(text || 'AI 摘要生成失败')
            } catch { setSummaryText('AI 摘要生成失败') }
            setSummaryLoading(false)
          }} style={miniBtnStyle} title="AI 摘要">{'🧐'}</button>
          <button onClick={async () => {
            if (!content.trim()) return
            try {
              const tags = await autoTag(content.slice(0, 2000))
              if (tags.length > 0) setToastMsg('标签: ' + tags.join(', '))
            } catch (e) { swallowError('NoteEditorSheet:255', e) }
          }} style={miniBtnStyle} title="自动标签">{'🏷'}</button>
        </div>
        {relatedMemories.length > 0 && (
          <div style={{ padding: 8, background: 'var(--accent-surface)', borderRadius: 8, fontSize: 'var(--text-caption-2)', color: 'var(--accent)', lineHeight: 1.6, border: '0.5px solid var(--accent-border)' }}>
            <div style={{ fontWeight: 600, marginBottom: 4, color: 'var(--accent)' }}>💭 相关记忆</div>
            {relatedMemories.map((m, i) => (
              <div key={i} style={{ padding: '4px 0', borderBottom: i < relatedMemories.length - 1 ? '0.5px solid var(--accent-border)' : 'none', color: 'var(--text-secondary)' }}>{m.slice(0, 120)}</div>
            ))}
            <button onClick={() => setRelatedMemories([])} style={{ marginTop: 4, fontSize: 10, color: 'var(--text-tertiary)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>关闭</button>
          </div>
        )}
        {summaryText && (
          <div style={{ padding: 8, background: 'var(--bg-tertiary)', borderRadius: 8, fontSize: 'var(--text-caption-2)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>AI 摘要</div>
            <div>{summaryText}</div>
            <button onClick={() => setSummaryText(null)} style={{ marginTop: 4, fontSize: 10, color: 'var(--text-tertiary)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>关闭</button>
          </div>
        )}
        {uploading && (
          <div style={{ padding: 8, fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
            上传中...
          </div>
        )}

        {previewMode === 'preview' ? (
          <div style={{
            flex: 1,
            padding: '8px 0',
            fontSize: 'var(--text-footnote)',
            color: 'var(--text-primary)',
            lineHeight: 1.8,
            overflowY: 'auto',
            minHeight: 200,
          }}>
            <MarkdownPreview content={content} />
          </div>
        ) : previewMode === 'split' ? (
          <div style={{
            flex: 1, display: 'flex', flexDirection: 'column', gap: 8,
            minHeight: 200, overflow: 'hidden',
          }}>
            <textarea
              ref={textareaRef}
              value={content}
              onChange={e => handleContentChange(e.target.value)}
              onSelect={e => {
                const t = e.currentTarget
                setSelRange({ start: t.selectionStart, end: t.selectionEnd })
              }}
              onPaste={handlePaste}
              placeholder="写点什么..."
              style={{
                flex: 1, minHeight: 100,
                padding: 0, border: 'none',
                background: 'transparent',
                color: 'var(--text-primary)',
                fontSize: 'var(--text-footnote)',
                lineHeight: 1.8,
                resize: 'none', outline: 'none', fontFamily: 'inherit',
              }}
            />
            <div style={{ height: 0.5, background: 'var(--border-subtle)', flexShrink: 0 }} />
            <div style={{
              flex: 1, overflowY: 'auto', padding: '4px 0',
              fontSize: 'var(--text-footnote)',
              color: 'var(--text-primary)',
              lineHeight: 1.8,
            }}>
              <MarkdownPreview content={content} />
            </div>
          </div>
        ) : (
          <textarea
            ref={textareaRef}
            value={content}
            onChange={e => handleContentChange(e.target.value)}
            onPaste={handlePaste}
            placeholder="写点什么..."
            style={{
              width: '100%',
              minHeight: 220,
              padding: 0,
              border: 'none',
              background: 'transparent',
              color: 'var(--text-primary)',
              fontSize: 'var(--text-footnote)',
              lineHeight: 1.8,
              resize: 'none',
              outline: 'none',
              fontFamily: 'inherit',
            }}
          />
        )}

        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          padding: '4px 0',
          fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)',
          borderTop: '0.5px solid var(--border-subtle)',
        }}>
          <span>{charCount} 字符</span>
          <span>{wordCount} 词</span>
          <div style={{ flex: 1 }} />
          <button
            onClick={() => setPreviewMode(m => m === 'editor' ? 'split' : m === 'split' ? 'preview' : 'editor')}
            style={{
              padding: '4px 10px', borderRadius: 'var(--radius-sm)',
              border: previewMode !== 'editor' ? '1px solid var(--accent-border)' : 'none',
              cursor: 'pointer',
              background: previewMode !== 'editor' ? 'var(--accent-surface)' : 'var(--bg-tertiary)',
              color: previewMode !== 'editor' ? 'var(--accent)' : 'var(--text-tertiary)',
              fontSize: 'var(--text-caption-1)', fontWeight: 500,
            }}
          >
            {previewMode === 'editor' ? '分屏' : previewMode === 'split' ? '预览' : '编辑'}
          </button>
        </div>

        <div style={{ display: 'flex', gap: 12 }}>
          <Button variant="ghost" onClick={onClose} style={{ flex: 1 }}>
            取消
          </Button>
          <Button variant="primary" onClick={handleSave} style={{ flex: 1 }}>
            保存
          </Button>
        </div>

        {/* Toast 通知 */}
        {toastMsg && (
          <div style={{
            position: 'fixed', bottom: 'calc(80px + env(safe-area-inset-bottom, 0px))', left: 16, right: 16,
            padding: '12px 16px', borderRadius: 12,
            background: 'var(--bg-secondary, #333)', color: 'var(--text-primary, #fff)',
            fontSize: 'var(--text-footnote)',
            textAlign: 'center', zIndex: 3000,
          }}>
            {toastMsg}
          </div>
        )}
      </div>
      </div>

      {/*
        底部工具区 —— 对标小米笔记：贴在键盘上方，而不是内容顶部。

        原实现把工具栏放在标题输入框下方、正文上方，是 PC 端习惯：
        手机上拇指够不到屏幕顶部，且工具栏会随正文一起滚走。

        新结构（根容器 flex column）：
          ├─ 顶栏（标题 + 完成）        flex-shrink:0
          ├─ 内容区（标题/正文/附件）   flex:1, overflow:auto
          └─ 底部工具区                 flex-shrink:0  ← 常驻，键盘弹出时随之上移

        键盘避让由 useKeyboardAvoidance（监听 visualViewport）负责，
        这里只需保证：不在滚动区内 + flex-shrink:0。
      */}
      <div style={{
        flexShrink: 0,
        borderTop: '0.5px solid var(--border-subtle)',
        background: 'var(--bg-primary)',
        paddingBottom: 'var(--safe-bottom)',
      }}>
        <NotesFormatToolbar
          textareaRef={textareaRef}
          content={content}
          onChange={handleContentChange}
          selection={selRange}
          onAction={(action: ToolbarAction) => {
            if (action === 'ai') {
              const ta = textareaRef.current
              if (ta) setSelRange({ start: ta.selectionStart, end: ta.selectionEnd })
              setAiOpen(true)
              return
            }
            if (action === 'checklist') {
              const ta = textareaRef.current
              const pos = ta ? ta.selectionStart : content.length
              handleContentChange(content.slice(0, pos) + '\n- [ ] ' + content.slice(pos))
              return
            }
            if (action === 'image') {
              document.getElementById('siyi-note-attach-trigger')?.click()
              return
            }
            if (action === 'voice') {
              textareaRef.current?.focus()
              return
            }
          }}
        />
      </div>

      <NoteAiAssistant
        open={aiOpen}
        onClose={() => setAiOpen(false)}
        selectionText={content.slice(selRange.start, selRange.end)}
        fullText={content}
        onApply={(text, mode) => {
          if (mode === 'replace_selection' && selRange.end > selRange.start) {
            handleContentChange(content.slice(0, selRange.start) + text + content.slice(selRange.end))
          } else if (mode === 'replace_all') {
            handleContentChange(text)
          } else {
            handleContentChange(content + '\n\n' + text)
          }
        }}
      />
    </div>
  )
}


/** 内联附件预览：扫描 Markdown 中的 siyi-file:// 并渲染预览。 */
function InlineAttachmentPreviews({ content }: { content: string }) {
  const [attachments, setAttachments] = useState<any[]>([])

  useEffect(() => {
    import('@desk-notes/shared').then((mod) => {
      setAttachments(mod.extractAttachmentsFromMarkdown(content))
    })
  }, [content])

  if (attachments.length === 0) return null

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, padding: '4px 0' }}>
      {attachments.map((att: any, i: number) => (
        <NoteAttachmentPreview key={`${att.uri}-${i}`} attachment={att} inline />
      ))}
    </div>
  )
}

function MarkdownPreview({ content }: { content: string }) {
  const lines = useMemo(() => {
    return content.split('\n').map((line, i) => {
      if (/^### /.test(line)) return <h3 key={i}>{line.slice(4)}</h3>
      if (/^## /.test(line)) return <h2 key={i}>{line.slice(3)}</h2>
      if (/^# /.test(line)) return <h1 key={i}>{line.slice(2)}</h1>
      const cb = line.match(/^- \[( |x)\] (.+)/)
      if (cb) {
        const checked = cb[1] === 'x'
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, margin: '2px 0' }}>
            <input type="checkbox" checked={checked} readOnly style={{ pointerEvents: 'none' }} />
            <span style={{ textDecoration: checked ? 'line-through' : 'none', color: checked ? 'var(--text-tertiary)' : 'var(--text-primary)' }}>
              {parseInline(cb[2])}
            </span>
          </div>
        )
      }
      if (/^- /.test(line)) return <li key={i} style={{ marginLeft: 16 }}>{parseInline(line.slice(2))}</li>
      if (/^\d+\. /.test(line)) return <li key={i} style={{ marginLeft: 16 }}>{parseInline(line.replace(/^\d+\. /, ''))}</li>
      if (!line.trim()) return <br key={i} />
      return <p key={i} style={{ margin: '4px 0' }}>{parseInline(line)}</p>
    })
  }, [content])

  return <div style={{ wordBreak: 'break-word' }}>{lines}</div>
}

function parseInline(text: string): React.ReactNode {
  const parts: React.ReactNode[] = []
  let remaining = text
  let idx = 0

  while (remaining.length > 0) {
    const bolds = remaining.match(/^\*\*\*(.+?)\*\*\*/)
    if (bolds) { parts.push(<strong key={idx++}><em>{bolds[1]}</em></strong>); remaining = remaining.slice(bolds[0].length); continue }
    const bold = remaining.match(/^\*\*(.+?)\*\*/)
    if (bold) { parts.push(<strong key={idx++}>{bold[1]}</strong>); remaining = remaining.slice(bold[0].length); continue }
    const italic = remaining.match(/^\*(.+?)\*/)
    if (italic) { parts.push(<em key={idx++}>{italic[1]}</em>); remaining = remaining.slice(italic[0].length); continue }
    const code = remaining.match(/^`(.+?)`/)
    if (code) { parts.push(<code key={idx++} style={{ background: 'var(--bg-tertiary)', padding: '1px 4px', borderRadius: 3, fontSize: '0.9em' }}>{code[1]}</code>); remaining = remaining.slice(code[0].length); continue }
    const img = remaining.match(/^!\[(.+?)\]\((.+?)\)/)
    if (img) { parts.push(<img key={idx++} src={img[2]} alt={img[1]} style={{ maxWidth: '100%', borderRadius: 8, margin: '4px 0', display: 'block' }} />); remaining = remaining.slice(img[0].length); continue }
    const link = remaining.match(/^\[(.+?)\]\((.+?)\)/)
    if (link) { parts.push(<a key={idx++} href={link[2]} style={{ color: 'var(--accent)' }}>{link[1]}</a>); remaining = remaining.slice(link[0].length); continue }
    const next = remaining.search(/[*`\[]/)
    if (next === 0) { parts.push(remaining[0]); remaining = remaining.slice(1) }
    else if (next > 0) { parts.push(remaining.slice(0, next)); remaining = remaining.slice(next) }
    else { parts.push(remaining); remaining = '' }
  }
  return parts
}
