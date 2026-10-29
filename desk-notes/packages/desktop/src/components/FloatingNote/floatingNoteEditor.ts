import { onContentEdit } from '../../store/contentEditTracker'
import { countText } from '../../utils/helpers'
import { sanitizeHtml } from '../../utils/sanitize'
import type { FloatingNote as FN } from '../../types'
import type { AIAction } from '../../utils/ai/types'
import type { EditorAction } from './AIChatPanel'

export interface ToolbarSetters {
  setFmtToolbar: React.Dispatch<React.SetStateAction<{ x: number; y: number; visible: boolean }>>
  setLinkToolbar: React.Dispatch<React.SetStateAction<{ x: number; y: number; visible: boolean; url: string; node: HTMLAnchorElement | null }>>
  setAiToolbar: React.Dispatch<React.SetStateAction<{ x: number; y: number; visible: boolean; text: string }>>
}

export function handleTextSelectLogic(
  editorRef: React.RefObject<HTMLDivElement>,
  showFormatBar: boolean,
  collapsed: boolean | undefined,
  setters: ToolbarSetters,
) {
  const sel = window.getSelection()
  if (!sel || !sel.rangeCount || !sel.toString().trim()) {
    setters.setFmtToolbar(prev => ({ ...prev, visible: false }))
    setters.setLinkToolbar(prev => ({ ...prev, visible: false }))
    setters.setAiToolbar(prev => ({ ...prev, visible: false, text: '' }))
    return
  }
  const editor = editorRef.current
  if (!editor || !editor.contains(sel.anchorNode)) {
    setters.setFmtToolbar(prev => ({ ...prev, visible: false }))
    setters.setLinkToolbar(prev => ({ ...prev, visible: false }))
    setters.setAiToolbar(prev => ({ ...prev, visible: false, text: '' }))
    return
  }
  if (showFormatBar && !collapsed) {
    setters.setFmtToolbar(prev => ({ ...prev, visible: false }))
    setters.setLinkToolbar(prev => ({ ...prev, visible: false }))
    const text = sel.toString().trim()
    if (text.length >= 10) {
      const range = sel.getRangeAt(0)
      const rect = range.getBoundingClientRect()
      setters.setAiToolbar({ x: rect.left + rect.width / 2, y: rect.top + 28, visible: true, text })
    } else {
      setters.setAiToolbar(prev => ({ ...prev, visible: false, text: '' }))
    }
    return
  }
  const range = sel.getRangeAt(0)
  const rect = range.getBoundingClientRect()

  const parent = sel.anchorNode?.parentElement
  const linkEl = parent?.closest?.('a')
  if (linkEl && linkEl.href) {
    setters.setLinkToolbar({ x: rect.left + rect.width / 2, y: rect.top, visible: true, url: linkEl.href, node: linkEl })
    setters.setFmtToolbar(prev => ({ ...prev, visible: false }))
    setters.setAiToolbar(prev => ({ ...prev, visible: false, text: '' }))
    return
  }
  setters.setLinkToolbar(prev => ({ ...prev, visible: false }))

  const text = sel.toString().trim()
  setters.setFmtToolbar({ x: rect.left + rect.width / 2, y: rect.top, visible: true })
  if (text.length >= 10) {
    setters.setAiToolbar({ x: rect.left + rect.width / 2, y: rect.top + 28, visible: true, text })
  } else {
    setters.setAiToolbar(prev => ({ ...prev, visible: false, text: '' }))
  }
}

export function handleTextInputLogic(
  editorRef: React.RefObject<HTMLDivElement>,
  committedContentRef: React.MutableRefObject<string>,
  linkifyTimerRef: React.MutableRefObject<ReturnType<typeof setTimeout> | undefined>,
  linkifyEditor: (editor: HTMLElement, noteId: string) => void,
  noteId: string,
  updateFloatingNote: (id: string, data: Partial<FN>) => void,
  setWc: React.Dispatch<React.SetStateAction<{ count: number; label: string }>>,
) {
  const el = editorRef.current
  if (!el) return
  const marks = el.querySelectorAll('mark.search-highlight')
  for (const m of marks) {
    const parent = m.parentNode
    if (parent) {
      parent.replaceChild(document.createTextNode(m.textContent || ''), m)
      parent.normalize()
    }
  }
  const html = el.innerHTML
  committedContentRef.current = html
  onContentEdit(noteId)
  updateFloatingNote(noteId, { content: html })
  const text = el.textContent || ''
  setWc(countText(text))

  if (linkifyTimerRef.current) clearTimeout(linkifyTimerRef.current)
  linkifyTimerRef.current = setTimeout(() => {
    linkifyEditor(el, noteId)
  }, 2000)
}

export function handleAIReplaceLogic(
  editorRef: React.RefObject<HTMLDivElement>,
  setAiToolbar: React.Dispatch<React.SetStateAction<{ x: number; y: number; visible: boolean; text: string }>>,
  newText: string,
  action: AIAction,
) {
  const sel = window.getSelection()
  if (!sel || !sel.rangeCount) return
  const editor = editorRef.current
  if (!editor || !editor.contains(sel.anchorNode)) return
  const range = sel.getRangeAt(0)
  if (action === 'continue') {
    range.collapse(false)
    range.insertNode(document.createTextNode(newText))
    range.collapse(false)
  } else {
    range.deleteContents()
    range.insertNode(document.createTextNode(newText))
    range.collapse(false)
  }
  setAiToolbar(prev => ({ ...prev, visible: false, text: '' }))
}

export function handleApplyActionLogic(
  editorRef: React.RefObject<HTMLDivElement>,
  savedRangeRef: React.MutableRefObject<Range | null>,
  noteId: string,
  updateFloatingNote: (id: string, data: Partial<FN>) => void,
  action: EditorAction,
) {
  const editor = editorRef.current
  if (!editor) return

  const { action: type, text } = action
  // 清洗 AI 响应中可能携带的 null 字节（\u0000），避免污染笔记存储
  const cleanText = typeof text === 'string' ? text.split('\0').join('') : text

  if (type === 'replace_full') {
    editor.innerHTML = sanitizeHtml(cleanText)
    onContentEdit(noteId)
    updateFloatingNote(noteId, { content: cleanText })
    return
  }

  if (type === 'append') {
    const cur = editor.innerHTML || ''
    const newContent = cur + cleanText
    editor.innerHTML = sanitizeHtml(newContent)
    onContentEdit(noteId)
    updateFloatingNote(noteId, { content: cur + cleanText })
    return
  }

  if (type === 'prepend') {
    const cur = editor.innerHTML || ''
    editor.innerHTML = sanitizeHtml(cleanText + cur)
    onContentEdit(noteId)
    updateFloatingNote(noteId, { content: cleanText + cur })
    return
  }

  // For replace_selection / insert_at_cursor: get or fallback range
  const sel = window.getSelection()
  let range: Range | null = null
  if (sel && sel.rangeCount > 0 && editor.contains(sel.anchorNode)) {
    range = sel.getRangeAt(0)
  } else if (savedRangeRef.current && editor.contains(savedRangeRef.current.startContainer as Node)) {
    range = savedRangeRef.current.cloneRange()
  }

  // No valid range → fallback to append for insert_at_cursor, silent ignore for replace_selection
  if (!range) {
    if (type === 'insert_at_cursor') {
      const cur = editor.innerHTML || ''
      editor.innerHTML = sanitizeHtml(cur + cleanText)
      onContentEdit(noteId)
      updateFloatingNote(noteId, { content: cur + cleanText })
    }
    return
  }

  if (type === 'replace_selection' || type === 'insert_at_cursor') {
    range.deleteContents()
  }
  range.insertNode(document.createTextNode(cleanText))
  range.collapse(false)
}

export function handleViewModeChangeLogic(
  mode: 'edit' | 'preview' | 'split',
  setViewMode: (mode: 'edit' | 'preview' | 'split') => void,
  noteId: string,
  updateFloatingNote: (id: string, data: Partial<FN>) => void,
) {
  setViewMode(mode)
  updateFloatingNote(noteId, { contentViewMode: mode })
}
