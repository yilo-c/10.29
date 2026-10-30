import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { onContentEdit, flushPendingEdit } from '@desk-notes/shared'
import { useScreenWake } from '../../../hooks/useScreenWake'
import { IconChevronLeft, IconMore } from '../../ui'
import NotesFormatToolbar, { type ToolbarAction } from './NotesFormatToolbar'
import NoteAiAssistant from './NoteAiAssistant'
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
  // 2026-10-30 三态（editor/split/preview）改为两态切换（editor/preview）。
  // 用户明确要求：「分屏可以存在，但不应该分屏，而是切换。点击可以切换到 md 渲染。」
  // 手机竖屏下分屏两栏都太窄，正文难读，改为整屏切换更实用。
  const [previewMode, setPreviewMode] = useState<'editor' | 'preview'>('editor')
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
  // 顶栏「⋯」设置菜单（2026-10-30：替代底部「取消 / 保存」两个大按钮）
  const [menuOpen, setMenuOpen] = useState(false)

  // 键盘避让说明（2026-10-30 修正）：
  //   曾尝试接入 useKeyboardAvoidance 并给根容器加 data-keyboard-aware，
  //   但那个 hook 的实现是给目标元素「加 paddingBottom = 键盘高度 + 48」。
  //   根容器本身是 position:fixed; inset:0（高度已等于视口），
  //   再加 padding 会让总高度溢出视口 —— 实测表现为「编辑器被压成两半、
  //   工具栏跑到中间」的布局错乱。
  //   正确做法：交给 flex 自然处理 —— 内容区 flex:1 + overflow:auto 自动收缩，
  //   底部工具栏 flex-shrink:0 常驻。浏览器已把 fixed 元素的视口收缩到
  //   键盘上方区域，无需再手动加 padding。

  // AI 摘要 / 自动标签：原先散落在正文区里各占一行的 emoji 按钮（🧐 / 🏷），
  // 2026-10-30 收进顶栏「⋯」菜单，正文区不再插入任何工具按钮。
  const handleAiSummary = useCallback(async () => {
    if (!content.trim() || summaryLoading) return
    setSummaryLoading(true)
    try {
      const text = await generateSummary(content.slice(0, 1000))
      setSummaryText(text || 'AI 摘要生成失败')
    } catch { setSummaryText('AI 摘要生成失败') }
    setSummaryLoading(false)
  }, [content, summaryLoading])

  const handleAutoTag = useCallback(async () => {
    if (!content.trim()) return
    try {
      const tags = await autoTag(content.slice(0, 2000))
      if (tags.length > 0) setToastMsg('标签: ' + tags.join(', '))
    } catch (e) { swallowError('NoteEditorSheet:autoTag', e) }
  }, [content])

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

  /** OCR 取字：拍照 → 后端 ocr.extract_base64 → 把识别文本插入正文。
   *  原先定义在 NoteAttachmentToolbar 内部；该组件因与底部工具栏功能重复
   *  已被移除，但 OCR 是它独有的能力，故在此保留。 */
  const handleOCR = useCallback(async () => {
    setUploading(true)
    try {
      const { pickFromCamera } = await import('../../../lib/noteAttachment')
      const result = await pickFromCamera()
      if (!result) return
      const { ocrExtractBase64 } = await import('../../../lib/bridges')
      const text = await ocrExtractBase64(result.dataUrl)
      if (text) handleInsertAttachment(text)
      else setToastMsg('未识别到文字')
    } catch {
      setToastMsg('OCR 识别失败')
    } finally {
      setUploading(false)
    }
  }, [handleInsertAttachment])

  const noteId = note?.id || 'draft'

  // 字数统计（2026-10-30：显示位置由底部状态行移到标题下方元信息行）
  // charCount 用 content.length，与原实现一致；wordCount 保留计算供将来使用。
  const charCount = content.length


  const titleText = note ? '编辑笔记' : quickCapture ? '快速捕获' : '新建笔记'
  if (!open) return null
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 2000, display: 'flex', flexDirection: 'column', background: 'var(--bg-primary)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: 'var(--safe-top) var(--space-4) 12px', borderBottom: '0.5px solid var(--border-subtle)', flexShrink: 0 }}>
        {/* 左：返回（自动保存后退出）。Redmi 笔记同款语义——退出不需要额外确认，
            只有「⋯→放弃编辑」才会丢弃改动。 */}
        <button
          onClick={() => { void handleSave() }}
          aria-label="返回并保存"
          style={{ background: 'none', border: 'none', color: 'var(--text-primary)', cursor: 'pointer', padding: 4, display: 'flex', alignItems: 'center' }}
        >
          <IconChevronLeft size={22} />
        </button>
        <span style={{ fontSize: 'var(--text-body)', fontWeight: 600, color: 'var(--text-primary)' }}>{titleText}</span>
        {/* 右：⋯ 设置菜单 */}
        <div style={{ position: 'relative' }}>
          <button
            onClick={() => setMenuOpen(v => !v)}
            aria-label="更多设置"
            style={{ background: 'none', border: 'none', color: 'var(--text-primary)', cursor: 'pointer', padding: 4, display: 'flex', alignItems: 'center' }}
          >
            <IconMore size={22} />
          </button>
          {menuOpen && (
            <>
              {/* 点击空白关闭 */}
              <div onClick={() => setMenuOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 1 }} />
              <div style={{
                position: 'absolute', top: '100%', right: 0, marginTop: 6, zIndex: 2,
                minWidth: 148, padding: 6,
                background: 'var(--bg-primary)', borderRadius: 12,
                boxShadow: 'var(--shadow-sheet)', border: '0.5px solid var(--border-subtle)',
              }}>
                {[
                  { label: 'OCR 取字', onClick: () => { setMenuOpen(false); void handleOCR() } },
                  { label: 'AI 摘要', onClick: () => { setMenuOpen(false); void handleAiSummary() } },
                  { label: '自动标签', onClick: () => { setMenuOpen(false); void handleAutoTag() } },
                  { label: '完成并保存', onClick: () => { setMenuOpen(false); void handleSave() } },
                  { label: '放弃编辑', onClick: () => { setMenuOpen(false); handleClose() } },
                ].map(item => (
                  <button
                    key={item.label}
                    onClick={item.onClick}
                    style={{
                      display: 'block', width: '100%', textAlign: 'left',
                      padding: '10px 12px', borderRadius: 8,
                      background: 'none', border: 'none', cursor: 'pointer',
                      fontSize: 'var(--text-footnote)', color: 'var(--text-primary)',
                    }}
                  >{item.label}</button>
                ))}
              </div>
            </>
          )}
        </div>
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

        {/* 元信息行：日期时间 | 字数（2026-10-30 新增，对标 Redmi 笔记）
            原先字数统计放在底部状态行并配「分屏」按钮，用户要求「字符统计也要，
            但是要换地方显示」。参考图中该行为标题正下方的小号浅灰副标题：
              10月30日 18:57 | 0字
            左侧「进入编辑 / 预览」切换：整屏切换，不分屏。 */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 'var(--text-caption-2)',
          color: 'var(--text-tertiary)',
          lineHeight: 1.4,
        }}>
          <span>{formatNoteDate(note)}</span>
          <span style={{ color: 'var(--border-default)' }}>|</span>
          <span>{charCount} 字</span>
          <div style={{ flex: 1 }} />
          <button
            onClick={() => setPreviewMode(m => m === 'editor' ? 'preview' : 'editor')}
            aria-label={previewMode === 'editor' ? '切换到预览' : '切换到编辑'}
            style={{
              padding: '2px 8px', borderRadius: 6,
              border: 'none', cursor: 'pointer',
              background: previewMode === 'preview' ? 'var(--accent-surface)' : 'transparent',
              color: previewMode === 'preview' ? 'var(--accent)' : 'var(--text-tertiary)',
              fontSize: 'var(--text-caption-2)', fontWeight: 500,
            }}
          >
            {previewMode === 'editor' ? '预览' : '编辑'}
          </button>
        </div>

        {/* 附件预览（已有附件的内联显示） */}
        {note && (
          <InlineAttachmentPreviews content={content} />
        )}

        {/* AI 摘要 / 自动标签按钮已移入顶栏「⋯」菜单（原为 🧐 / 🏷 emoji 按钮） */}
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

        {/* 2026-10-30 「分屏 / 预览 / 编辑」三态切换已移除，
            改为标题下方元信息行右侧的「预览 / 编辑」两态整屏切换。 */}

        {/* 2026-10-30 移除「取消 / 保存」两个大按钮。
            原实现占满一整行（flex:1 各半屏宽），键盘弹起时把编辑区压得极窄；
            Redmi 笔记的做法是「返回即自动保存」，顶栏右上角只留一个设置入口。
            现在：顶栏左侧「返回」（有未保存内容时二次确认），
                  顶栏右侧「⋯」设置菜单（含 保存 / 放弃）。
            保存仍走 handleSave，只是入口从底部大按钮移到设置菜单。 */}

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
              // 2026-10-30：原实现是 document.getElementById('siyi-note-attach-trigger')
              // 去点一个隐藏按钮，但该 id 从未在任何地方定义 —— 点「插入图片」
              // 实际无反应（坏功能）。改为直接调用相册选择器。
              void (async () => {
                try {
                  setUploading(true)
                  const { pickFromGallery, uploadNoteAttachment } = await import('../../../lib/noteAttachment')
                  const results = await pickFromGallery(true)
                  for (const r of results) {
                    const ref = await uploadNoteAttachment(noteId, r)
                    handleInsertAttachment(`![${ref.name}](${ref.uri})`)
                  }
                } catch (e) {
                  setToastMsg('选取图片失败')
                } finally {
                  setUploading(false)
                }
              })()
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

/**
 * 笔记元信息行的日期时间格式化（2026-10-30，对标 Redmi 笔记）
 *
 * 参考图样式：`10月30日 18:57 | 0字`
 *   - 当天：只显示「今天 HH:mm」中的时间部分太寒酸，Redmi 用「M月D日 HH:mm」
 *   - 无 note（新建中）：用当前时间，让用户看到写作起点
 * 输出 M月D日 HH:mm；仅在**非当年**时补上年份（yyyy年M月D日 HH:mm）。
 *
 * 2026-10-30 增补年份：原实现无论哪一年都只输出「M月D日」，
 * 跨年笔记（如去年今天写的）与今年今天的完全同形，无法区分。
 * 但不是无脑加年份——同一年内占多数，加年份会挤占窄屏、
 * 且 Redmi 笔记自身当年也不带年份。故按「与当前年份比较」决定是否补。
 */
function formatNoteDate(note: { updatedAt?: number; createdAt?: number } | null): string {
  const ts = note?.updatedAt ?? note?.createdAt ?? Date.now()
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  const mm = d.getMonth() + 1
  const dd = d.getDate()
  const hh = String(d.getHours()).padStart(2, '0')
  const mi = String(d.getMinutes()).padStart(2, '0')
  const isThisYear = d.getFullYear() === new Date().getFullYear()
  const datePart = isThisYear ? `${mm}月${dd}日` : `${d.getFullYear()}年${mm}月${dd}日`
  return `${datePart} ${hh}:${mi}`
}
