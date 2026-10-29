import { useRef, useCallback, useEffect } from 'react'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { buildFuseIndex, resolveWikiLink } from '../../utils/wikiLinks'
import { swallowError } from '../../utils/swallowError'

export function useNoteWiki() {
  const { t } = useTranslation()
  const fuseCacheRef = useRef<{ key: string; fuse: ReturnType<typeof buildFuseIndex> } | null>(null)
  const timeoutIdsRef = useRef<ReturnType<typeof setTimeout>[]>([])

  useEffect(() => {
    return () => {
      for (const t of timeoutIdsRef.current) clearTimeout(t)
      timeoutIdsRef.current = []
    }
  }, [])

  const getCachedFuse = useCallback(() => {
    const allNotes = useStore.getState().floatingNotes
    const key = allNotes.map(n => `${n.id}:${n.title}`).join('|')
    if (fuseCacheRef.current && fuseCacheRef.current.key === key) {
      return fuseCacheRef.current.fuse
    }
    const noteStubs = allNotes.map(n => ({ id: n.id, title: n.title }))
    const fuse = buildFuseIndex(noteStubs)
    fuseCacheRef.current = { key, fuse }
    return fuse
  }, [])

  const navigateToRef = useCallback((refId: string) => {
    const allNotes = useStore.getState().floatingNotes
    const target = allNotes.find(n => n.id === refId)
    if (target) {
      useStore.getState().focusNote(refId)
      const el = document.querySelector(`[data-note-id="${refId}"]`) as HTMLElement
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.style.boxShadow = '0 0 20px rgba(96,165,250,0.4), 0 0 0 1px rgba(96,165,250,0.3)'
        const t = setTimeout(() => {
          const e = document.querySelector(`[data-note-id="${refId}"]`) as HTMLElement
          if (e) e.style.boxShadow = ''
        }, 2000)
        timeoutIdsRef.current.push(t)
      }
    }
  }, [])

  const linkifyEditor = useCallback((editor: HTMLElement, noteId: string) => {
    if (!editor.isConnected) return
    const contentAtStart = editor.innerHTML
    const allNotes = useStore.getState().floatingNotes
    const noteStubs = allNotes.map(n => ({ id: n.id, title: n.title }))
    const fuse = getCachedFuse()

    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT, null)
    const textNodes: Text[] = []
    while (walker.nextNode()) {
      const node = walker.currentNode as Text
      if (node.parentElement && !node.parentElement.closest('a')) {
        textNodes.push(node)
      }
    }

    let changed = false
    for (const node of textNodes) {
      const text = node.textContent || ''
      const combinedRegex = /(https?:\/\/[^\s]+)|(便签:\/\/[a-zA-Z0-9_-]+)|(\[\[[^\]|]+?(?:\|[^\]]+)?\]\])/g
      if (!combinedRegex.test(text)) continue

      combinedRegex.lastIndex = 0
      const fragment = document.createDocumentFragment()
      let lastIndex = 0
      let match: RegExpExecArray | null
      while ((match = combinedRegex.exec(text)) !== null) {
        if (match.index > lastIndex) {
          fragment.appendChild(document.createTextNode(text.slice(lastIndex, match.index)))
        }
        if (match[1]) {
          const a = document.createElement('a')
          a.href = match[1]
          a.textContent = match[1]
          a.target = '_blank'
          a.rel = 'noopener noreferrer'
          a.style.color = 'var(--brand)'
          a.style.textDecoration = 'underline'
          fragment.appendChild(a)
        } else if (match[2]) {
          const refId = match[2].replace('便签://', '')
          const a = document.createElement('a')
          a.href = `#ref-${refId}`
          a.textContent = match[2]
          a.style.color = '#a78bfa'
          a.style.textDecoration = 'underline'
          a.style.cursor = 'pointer'
          a.dataset.refId = refId
          a.addEventListener('click', (e) => {
            e.preventDefault()
            navigateToRef(refId)
          })
          fragment.appendChild(a)
        } else if (match[3]) {
          const inner = match[3].slice(2, -2)
          const pipeIdx = inner.indexOf('|')
          const linkTitle = (pipeIdx >= 0 ? inner.slice(0, pipeIdx) : inner).trim()
          const displayText = (pipeIdx >= 0 ? inner.slice(pipeIdx + 1) : inner).trim()
          const resolved = resolveWikiLink(linkTitle, noteStubs, fuse)
          const a = document.createElement('a')
          a.href = resolved ? `#ref-${resolved.id}` : '#'
          a.textContent = displayText
          a.style.cursor = 'pointer'
          if (resolved) {
            a.style.color = '#a78bfa'
            a.style.textDecoration = 'underline'
            a.dataset.refId = resolved.id
            a.addEventListener('click', (e) => {
              e.preventDefault()
              navigateToRef(resolved.id)
            })
          } else {
            a.style.color = 'var(--danger)'
            a.style.textDecoration = 'underline dashed'
            a.title = t('note.linkNotFound', { title: linkTitle })
            a.dataset.deadLink = 'true'
          }
          fragment.appendChild(a)
        }
        lastIndex = match.index + match[0].length
      }
      if (lastIndex < text.length) {
        fragment.appendChild(document.createTextNode(text.slice(lastIndex)))
      }

      if (fragment.childNodes.length > 0) {
        const sel = window.getSelection()
        const savedRange = sel && sel.rangeCount > 0 ? sel.getRangeAt(0).cloneRange() : null

        node.parentNode?.replaceChild(fragment, node)
        changed = true

        if (savedRange) {
          try {
            sel?.removeAllRanges()
            sel?.addRange(savedRange)
          } catch (e) { /* ignore */ swallowError('useNoteWiki:144', e) }
        }
      }
    }

    if (changed) {
      // Don't overwrite if content changed while we were processing (user kept typing)
      if (editor.innerHTML !== contentAtStart) return
      const updateFloatingNote = useStore.getState().updateFloatingNote
      updateFloatingNote(noteId, { content: editor.innerHTML })
    }
  }, [getCachedFuse, navigateToRef])

  return { navigateToRef, getCachedFuse, linkifyEditor }
}
