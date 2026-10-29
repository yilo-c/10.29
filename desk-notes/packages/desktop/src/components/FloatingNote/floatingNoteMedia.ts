import { saveMedia, MAX_IMAGE_SIZE, MAX_VIDEO_SIZE } from '../../utils/imageStore'
import { onContentEdit } from '../../store/contentEditTracker'

/** Safely get the current selection range, or null when there is no selection. */
function getSelectionRange(): Range | null {
  const sel = window.getSelection()
  if (!sel || sel.rangeCount === 0) return null
  return sel.getRangeAt(0).cloneRange()
}

/** Insert a media/block element at the current range, or append to editor when no range. */
function insertAtRange(range: Range | null, el: HTMLElement, editorRef: React.RefObject<HTMLDivElement | null>) {
  if (range) {
    range.deleteContents()
    range.insertNode(el)
    range.collapse(false)
  } else {
    editorRef.current?.appendChild(el)
  }
  return el
}

export function handlePaste(
  e: React.ClipboardEvent,
  noteId: string,
  editorRef: React.RefObject<HTMLDivElement | null>,
  updateFloatingNote: (id: string, data: Record<string, unknown>) => void,
  contentEdit: typeof onContentEdit,
) {
  const files = Array.from(e.clipboardData.files)
  const mediaFiles = files.filter(f => f.type.startsWith('image/') || f.type.startsWith('video/'))
  if (mediaFiles.length > 0) {
    e.preventDefault()
    const range = getSelectionRange()
    for (const file of mediaFiles) {
      const isVideo = file.type.startsWith('video/')
      const maxSize = isVideo ? MAX_VIDEO_SIZE : MAX_IMAGE_SIZE
      if (file.size > maxSize) continue

      const reader = new FileReader()
      reader.onload = async (loadEvent) => {
        const dataUrl = loadEvent.target?.result as string
        if (!dataUrl) return
        let src: string
        try {
          src = await saveMedia(dataUrl, file.name)
        } catch {
          return
        }

        if (isVideo) {
          const video = document.createElement('video')
          video.src = src
          video.controls = true
          video.style.maxWidth = '100%'
          video.style.maxHeight = '400px'
          video.style.display = 'block'
          video.setAttribute('data-asset', src.startsWith('assets://') ? '1' : '0')
          insertAtRange(range, video, editorRef)
        } else {
          const img = document.createElement('img')
          img.src = src
          img.alt = file.name || 'pasted image'
          img.style.maxWidth = '100%'
          img.style.height = 'auto'
          img.setAttribute('data-asset', src.startsWith('assets://') ? '1' : '0')
          insertAtRange(range, img, editorRef)
        }
        contentEdit(noteId)
        const el = editorRef.current
        if (el) updateFloatingNote(noteId, { content: el.innerHTML })
      }
      reader.readAsDataURL(file)
    }
    return
  }

  e.preventDefault()
  const html = e.clipboardData.getData('text/html')
  if (html) {
    const allowedTags = new Set(['b', 'i', 'u', 'h1', 'h2', 'h3', 'ul', 'ol', 'li', 'code', 'pre', 'a', 'br', 'div', 'p', 'span', 'blockquote', 'img'])
    const parser = new DOMParser()
    const doc = parser.parseFromString(html, 'text/html')
    const stripNode = (node: Node): Node | null => {
      if (node.nodeType === Node.TEXT_NODE) return node.cloneNode()
      if (node.nodeType === Node.ELEMENT_NODE) {
        const el = node as HTMLElement
        const tag = el.tagName.toLowerCase()
        if (!allowedTags.has(tag)) {
          const text = el.textContent || ''
          return document.createTextNode(text)
        }
        const clone = document.createElement(tag)
        if (tag === 'a' && el.getAttribute('href')) {
          const href = el.getAttribute('href')!
          if (/^https?:\/\//i.test(href) && !/javascript:/i.test(href)) {
            clone.setAttribute('href', href)
            clone.setAttribute('target', '_blank')
            clone.setAttribute('rel', 'noopener noreferrer')
          }
        }
        if (tag === 'img') {
          const src = el.getAttribute('src') || ''
          if (src.startsWith('data:') || src.startsWith('blob:')) {
            clone.setAttribute('src', src)
            const alt = el.getAttribute('alt')
            if (alt) clone.setAttribute('alt', alt)
          }
        }
        for (const child of Array.from(el.childNodes)) {
          const cleaned = stripNode(child)
          if (cleaned) clone.appendChild(cleaned)
        }
        return clone
      }
      return null
    }
    const fragment = document.createDocumentFragment()
    for (const child of Array.from(doc.body.childNodes)) {
      const cleaned = stripNode(child)
      if (cleaned) fragment.appendChild(cleaned)
    }
    const range = getSelectionRange()
    if (range) {
      range.deleteContents()
      range.insertNode(fragment)
      range.collapse(false)
    } else {
      // No active selection: append pasted rich text to the end of the editor.
      editorRef.current?.appendChild(fragment)
    }
  } else {
    document.execCommand('insertText', false, e.clipboardData.getData('text/plain'))
  }
}

export function handleDrop(
  e: React.DragEvent,
  noteId: string,
  noteLocked: boolean,
  noteArchived: boolean,
  editorRef: React.RefObject<HTMLDivElement | null>,
  updateFloatingNote: (id: string, data: Record<string, unknown>) => void,
  contentEdit: typeof onContentEdit,
) {
  e.preventDefault()
  e.stopPropagation()
  if (noteLocked || noteArchived) return
  const files = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/') || f.type.startsWith('video/'))
  if (files.length === 0) return
  const range = getSelectionRange()
  for (const file of files) {
    const isVideo = file.type.startsWith('video/')
    const maxSize = isVideo ? MAX_VIDEO_SIZE : MAX_IMAGE_SIZE
    if (file.size > maxSize) continue
    const reader = new FileReader()
    reader.onload = async (loadEvent) => {
      const dataUrl = loadEvent.target?.result as string
      const src = await saveMedia(dataUrl, file.name)

      if (isVideo) {
        const video = document.createElement('video')
        video.src = src
        video.controls = true
        video.style.maxWidth = '100%'
        video.style.maxHeight = '400px'
        video.style.display = 'block'
        video.setAttribute('data-asset', src.startsWith('assets://') ? '1' : '0')
        insertAtRange(range, video, editorRef)
      } else {
        const img = document.createElement('img')
        img.src = src
        img.alt = file.name || 'dropped image'
        img.style.maxWidth = '100%'
        img.style.height = 'auto'
        img.setAttribute('data-asset', src.startsWith('assets://') ? '1' : '0')
        insertAtRange(range, img, editorRef)
      }
      contentEdit(noteId)
      const el = editorRef.current
      if (el) updateFloatingNote(noteId, { content: el.innerHTML })
    }
    reader.readAsDataURL(file)
  }
}
