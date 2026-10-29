import React, { useEffect, useRef, useState } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import WikiLinkPopup from './WikiLinkPopup'
import EmojiPicker from './EmojiPicker'

const FONT_SIZES = [12, 13, 14, 15, 16, 18, 20, 24]

interface FormatToolbarProps {
  x: number
  y: number
  visible: boolean
  containerEl: HTMLElement | null
  onClose: () => void
  /** When true, render as a fixed horizontal bar instead of floating popup */
  fixed?: boolean
  fontSize?: number
  onFontSizeChange?: (size: number) => void
  /** View mode for preview (only relevant in fixed mode) */
  viewMode?: 'edit' | 'preview' | 'split'
  onViewModeChange?: (mode: 'edit' | 'preview' | 'split') => void
  /** Current note ID (for wiki link insertion to exclude self) */
  noteId?: string
  /** Open AI chat panel */
  onOpenChat?: () => void
}

interface ToolBtn {
  icon: string
  label: string
  cmd: string
  val?: string
  active?: (doc: Document) => boolean
}

const FormatToolbar: React.FC<FormatToolbarProps> = ({ x, y, visible, containerEl, onClose, fixed, fontSize, onFontSizeChange, viewMode, onViewModeChange, noteId, onOpenChat }) => {
  const { t } = useTranslation()
  const barRef = useRef<HTMLDivElement>(null)
  const [showFontSize, setShowFontSize] = useState(false)
  const fontSizeRef = useRef<HTMLDivElement>(null)
  const [showWikiPicker, setShowWikiPicker] = useState(false)
  const wikiBtnRef = useRef<HTMLDivElement>(null)
  const [showEmojiPicker, setShowEmojiPicker] = useState(false)
  const emojiBtnRef = useRef<HTMLDivElement>(null)

  const TOOLS: ToolBtn[] = [
    { icon: 'fa-bold', label: t('formatToolbar.bold'), cmd: 'bold', active: d => d.queryCommandState('bold') },
    { icon: 'fa-italic', label: t('formatToolbar.italic'), cmd: 'italic', active: d => d.queryCommandState('italic') },
    { icon: 'fa-underline', label: t('formatToolbar.underline'), cmd: 'underline', active: d => d.queryCommandState('underline') },
    { icon: 'fa-heading', label: t('formatToolbar.heading'), cmd: 'formatBlock', val: 'h1', active: d => d.queryCommandValue('formatBlock') === 'h1' },
    { icon: 'fa-list-ul', label: t('formatToolbar.list'), cmd: 'insertUnorderedList', active: d => d.queryCommandState('insertUnorderedList') },
    { icon: 'fa-list-ol', label: t('formatToolbar.orderedList'), cmd: 'insertOrderedList', active: d => d.queryCommandState('insertOrderedList') },
    { icon: 'fa-quote-right', label: t('formatToolbar.quote'), cmd: 'formatBlock', val: 'blockquote', active: d => d.queryCommandValue('formatBlock') === 'blockquote' },
    { icon: 'fa-code', label: t('formatToolbar.code'), cmd: 'insertHTML', val: '<code>$TEXT</code>' },
    { icon: 'fa-link', label: t('formatToolbar.wikiLink'), cmd: 'wikiLink' },
    { icon: 'fa-face-smile', label: t('formatToolbar.emoji'), cmd: 'emoji' },
    { icon: 'fa-minus', label: t('formatToolbar.horizontalRule'), cmd: 'insertHTML', val: '<hr>' },
    { icon: 'fa-eraser', label: t('formatToolbar.clearFormat'), cmd: 'removeFormat' },
  ]

  // Close font size popup on outside click
  useEffect(() => {
    if (!showFontSize) return
    const handler = (e: MouseEvent) => {
      if (fontSizeRef.current && !fontSizeRef.current.contains(e.target as Node)) {
        setShowFontSize(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showFontSize])

  useEffect(() => {
    if (!visible || !barRef.current || fixed) return
    const bar = barRef.current
    const barW = bar.offsetWidth
    const container = containerEl || document.body
    const cRect = container.getBoundingClientRect()
    let adjustedX = x - barW / 2
    if (adjustedX + barW > cRect.right - 8) adjustedX = cRect.right - barW - 8
    if (adjustedX < cRect.left + 8) adjustedX = cRect.left + 8
    bar.style.left = `${adjustedX}px`
    bar.style.top = `${y - 44}px`
  }, [x, y, visible, containerEl, fixed])

  useEffect(() => {
    if (!visible || fixed) return
    const handler = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node)) {
        const sel = window.getSelection()
        if (!sel || !sel.rangeCount || !sel.toString().trim()) {
          onClose()
        }
      }
    }
    const timer = setTimeout(() => {
      document.addEventListener('mousedown', handler)
    }, 0)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handler)
    }
  }, [visible, onClose, fixed])

  const execCmd = (cmd: string, val?: string) => {
    if (cmd === 'wikiLink') {
      setShowWikiPicker(prev => !prev)
      setShowEmojiPicker(false)
      return
    }
    if (cmd === 'emoji') {
      setShowEmojiPicker(prev => !prev)
      setShowWikiPicker(false)
      return
    }
    if (cmd === 'insertHTML' && val) {
      const sel = window.getSelection()
      if (!sel || !sel.rangeCount) return
      const text = sel.toString().trim()
      const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      if (escaped) {
        document.execCommand('insertHTML', false, `<code>${escaped}</code>`)
      } else {
        document.execCommand('insertHTML', false, t('note.codeSample'))
      }
    } else {
      document.execCommand(cmd, false, val || undefined)
    }
    const el = containerEl
    if (el) el.focus()
  }

  if (!visible) return null

  return (
    <div
      ref={barRef}
      className={fixed
        ? 'flex items-center gap-0.5 px-2 py-1 border-b border-white/[0.04] flex-shrink-0 flex-wrap'
        : 'fixed z-[9999] flex items-center gap-0.5 bg-[rgba(28,28,38,0.95)] border border-white/[0.08] rounded-lg px-1.5 py-1 shadow-2xl backdrop-blur-xl'
      }
      style={fixed ? { background: 'var(--hover-bg)' } : undefined}
      onMouseDown={e => e.preventDefault()}
    >
      {fixed && (
        <>
          {/* View mode toggles */}
          {onViewModeChange && (
            <div className="flex items-center gap-0.5 mr-1.5 pr-1.5 border-r border-white/[0.06]">
              <button
                onClick={() => onViewModeChange('edit')}
                className={`w-6 h-6 flex items-center justify-center rounded text-2xs transition-all ${
                  viewMode === 'edit' || viewMode === undefined
                    ? 'text-white bg-white/[0.10]' : 'text-white/45 hover:text-white hover:bg-white/[0.06]'
                }`}
                title={t('formatToolbar.editMode')}
              >
                <Icon icon="fa-solid fa-pen" />
              </button>
              <button
                onClick={() => onViewModeChange('preview')}
                className={`w-6 h-6 flex items-center justify-center rounded text-2xs transition-all ${
                  viewMode === 'preview'
                    ? 'text-white bg-white/[0.10]' : 'text-white/45 hover:text-white hover:bg-white/[0.06]'
                }`}
                title={t('formatToolbar.previewMode')}
              >
                <Icon icon="fa-solid fa-eye" />
              </button>
              <button
                onClick={() => onViewModeChange('split')}
                className={`w-6 h-6 flex items-center justify-center rounded text-2xs transition-all ${
                  viewMode === 'split'
                    ? 'text-white bg-white/[0.10]' : 'text-white/45 hover:text-white hover:bg-white/[0.06]'
                }`}
                title={t('formatToolbar.splitMode')}
              >
                <Icon icon="fa-solid fa-columns" />
              </button>
            </div>
          )}
          <div className="relative" ref={fontSizeRef}>
            <button
              onClick={() => setShowFontSize(!showFontSize)}
              className="w-7 h-7 flex items-center justify-center rounded text-xs text-white/55 hover:text-white hover:bg-white/[0.06] transition-all"
              title={t('note.fontSize')}
            >
              {fontSize || 14}
            </button>
            {showFontSize && (
              <div className="absolute top-full left-0 mt-1 z-50 border border-white/[0.08] rounded-lg py-1 shadow-xl min-w-[48px]"
                style={{ background: 'var(--panel-bg-solid)' }}>
                {FONT_SIZES.map(s => (
                  <button
                    key={s}
                    onClick={() => { onFontSizeChange?.(s); setShowFontSize(false) }}
                    className={`w-full px-2 py-1 text-xs text-center transition-colors ${
                      (fontSize || 14) === s
                        ? 'text-white bg-white/[0.08]'
                        : 'text-white/55 hover:text-white hover:bg-white/[0.04]'
                    }`}
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        </>
      )}
      {/* AI chat button */}
      {onOpenChat && (
        <>
          <div className="w-px h-4 bg-white/[0.06] mx-0.5" />
          <button
            onClick={onOpenChat}
            className="w-7 h-7 flex items-center justify-center rounded text-xs transition-all hover:bg-white/[0.06]"
            style={{ color: 'var(--text-secondary, rgba(255,255,255,0.72))' }}
            title={t('ai.chat.title')}
          >
            <Icon icon="fa-solid fa-comment-dots" />
          </button>
        </>
      )}
      {/* Rich text tools — 仅在浮动选区工具栏（非 fixed）呈现；MD 编辑器的固定工具栏不再展示 HTML 命令 */}
      {!fixed && (viewMode === 'edit' || viewMode === undefined) && TOOLS.map(t => (
        t.cmd === 'wikiLink' ? (
          <div key="wikiLink" className="relative" ref={wikiBtnRef}>
            <button
              onClick={() => execCmd(t.cmd, t.val)}
              className={`w-7 h-7 flex items-center justify-center rounded text-xs transition-all ${
                showWikiPicker
                  ? 'text-white bg-white/[0.12]'
                  : 'text-white/55 hover:text-white hover:bg-white/[0.06]'
              }`}
              title={t.label}
            >
              <Icon icon={`fa-solid ${t.icon}`} />
            </button>
            {showWikiPicker && (
              <WikiLinkPopup
                containerEl={containerEl}
                currentNoteId={noteId || ''}
                onClose={() => setShowWikiPicker(false)}
              />
            )}
          </div>
        ) : t.cmd === 'emoji' ? (
          <div key="emoji" className="relative" ref={emojiBtnRef}>
            <button
              onClick={() => execCmd(t.cmd, t.val)}
              className={`w-7 h-7 flex items-center justify-center rounded text-xs transition-all ${
                showEmojiPicker
                  ? 'text-white bg-white/[0.12]'
                  : 'text-white/55 hover:text-white hover:bg-white/[0.06]'
              }`}
              title={t.label}
            >
              <Icon icon={`fa-solid ${t.icon}`} />
            </button>
            {showEmojiPicker && (
              <EmojiPicker
                onSelect={(emoji) => {
                  document.execCommand('insertText', false, emoji)
                  if (containerEl) containerEl.focus()
                }}
                onClose={() => setShowEmojiPicker(false)}
              />
            )}
          </div>
        ) : (
          <button
            key={t.cmd + (t.val || '')}
            onClick={() => execCmd(t.cmd, t.val)}
            className={`w-7 h-7 flex items-center justify-center rounded text-xs transition-all
              ${t.active && t.active(document)
                ? 'text-white bg-white/[0.12]'
                : 'text-white/55 hover:text-white hover:bg-white/[0.06]'
              }`}
            title={t.label}
          >
            <Icon icon={`fa-solid ${t.icon}`} />
          </button>
        )
      ))}
    </div>
  )
}

export default React.memo(FormatToolbar)
