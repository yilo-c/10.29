import { useStore } from '../../store/useStore'
import { onContentEdit } from '../../store/contentEditTracker'
import { undoManager } from '../../store/undoManager'
import { stripHtml } from '../../utils/helpers'
import type { MenuItem } from '../Common/ContextMenu'
import type { FloatingNote as FN } from '../../types'

export interface ContextMenuDeps {
  ctxImage: string | null
  t: (key: string, params?: Record<string, string | number>) => string
  note: FN
  editorRef: React.RefObject<HTMLDivElement | null>
  resizeTargetRefFN: React.MutableRefObject<HTMLImageElement | null>
  setResizingFN: (v: boolean) => void
  setResizeWidthFN: (v: number) => void
  updateFloatingNote: (id: string, data: Partial<FN>) => void
  removeFloatingNote: (id: string) => void
  onMoveToFolder?: () => void
  folders?: { id: string; name: string }[]
}

export function getContextMenuItems(deps: ContextMenuDeps): MenuItem[] {
  const {
    ctxImage, t, note, editorRef, resizeTargetRefFN,
    setResizingFN, setResizeWidthFN, updateFloatingNote, removeFloatingNote,
    onMoveToFolder,
  } = deps

  if (ctxImage) {
    return [
      {
        label: t('note.resizeImage'),
        icon: 'fa-expand',
        onClick: () => {
          const editor = editorRef.current
          if (!editor) return
          const img = Array.from(editor.querySelectorAll('img')).find(i => i.getAttribute('src') === ctxImage)
          if (!img) return
          resizeTargetRefFN.current = img
          const pct = Math.round((img.width / (img.naturalWidth || img.width)) * 100)
          setResizeWidthFN(Math.max(10, Math.min(200, pct)))
          setResizingFN(true)
        },
      },
      {
        label: t('note.alignLeft'),
        icon: 'fa-align-left',
        onClick: () => {
          const editor = editorRef.current
          if (!editor) return
          const img = Array.from(editor.querySelectorAll('img')).find(i => i.getAttribute('src') === ctxImage)
          if (!img) return
          img.style.display = 'block'
          img.style.float = 'none'
          img.style.marginLeft = '0'
          img.style.marginRight = 'auto'
          onContentEdit(note.id)
          updateFloatingNote(note.id, { content: editor.innerHTML })
        },
      },
      {
        label: t('note.alignCenter'),
        icon: 'fa-align-center',
        onClick: () => {
          const editor = editorRef.current
          if (!editor) return
          const img = Array.from(editor.querySelectorAll('img')).find(i => i.getAttribute('src') === ctxImage)
          if (!img) return
          img.style.display = 'block'
          img.style.float = 'none'
          img.style.marginLeft = 'auto'
          img.style.marginRight = 'auto'
          onContentEdit(note.id)
          updateFloatingNote(note.id, { content: editor.innerHTML })
        },
      },
      {
        label: t('note.alignRight'),
        icon: 'fa-align-right',
        onClick: () => {
          const editor = editorRef.current
          if (!editor) return
          const img = Array.from(editor.querySelectorAll('img')).find(i => i.getAttribute('src') === ctxImage)
          if (!img) return
          img.style.display = 'block'
          img.style.float = 'none'
          img.style.marginLeft = 'auto'
          img.style.marginRight = '0'
          onContentEdit(note.id)
          updateFloatingNote(note.id, { content: editor.innerHTML })
        },
      },
      {
        label: (() => {
          const editor = editorRef.current
          if (!editor) return t('note.addCaption')
          const img = Array.from(editor.querySelectorAll('img')).find(i => i.getAttribute('src') === ctxImage)
          return img?.nextElementSibling?.matches('.image-caption')
            ? t('note.removeCaption') : t('note.addCaption')
        })(),
        icon: 'fa-quote-right',
        onClick: () => {
          const editor = editorRef.current
          if (!editor) return
          const img = Array.from(editor.querySelectorAll('img')).find(i => i.getAttribute('src') === ctxImage)
          if (!img) return
          const existing = img.nextElementSibling
          if (existing?.matches('.image-caption')) {
            existing.remove()
          } else {
            const cap = document.createElement('p')
            cap.className = 'image-caption'
            cap.textContent = t('note.imageCaptionPlaceholder')
            cap.style.cssText = 'font-size:9px;color:rgba(255,255,255,0.5);text-align:center;margin:2px 0 8px;font-style:italic;'
            img.parentNode?.insertBefore(cap, img.nextSibling)
          }
          onContentEdit(note.id)
          updateFloatingNote(note.id, { content: editor.innerHTML })
        },
      },
      {
        label: t('note.exportImage'),
        icon: 'fa-download',
        onClick: () => {
          const link = document.createElement('a')
          link.href = ctxImage
          link.download = `image-${Date.now()}.png`
          document.body.appendChild(link)
          link.click()
          document.body.removeChild(link)
        },
      },
      {
        label: t('note.deleteImage'),
        icon: 'fa-trash-can',
        danger: true,
        onClick: () => {
          if (note.locked) return
          const editor = editorRef.current
          if (!editor) return
          const imgs = editor.querySelectorAll('img')
          for (const img of imgs) {
            if (img.getAttribute('src') === ctxImage) {
              const cap = img.nextElementSibling
              if (cap?.matches('.image-caption')) cap.remove()
              img.remove()
              break
            }
          }
          onContentEdit(note.id)
          updateFloatingNote(note.id, { content: editor.innerHTML })
        },
      },
    ]
  }

  return [
    {
      label: t('note.copyTitle'),
      icon: 'fa-copy',
      onClick: () => navigator.clipboard.writeText(note.title),
    },
    {
      label: t('note.copyContent'),
      icon: 'fa-copy',
      onClick: () => {
        const text = note.type === 'text'
          ? stripHtml(note.content)
          : (note.todos || []).map(t => `${t.done ? '[x]' : '[ ]'} ${t.text}`).join('\n')
        navigator.clipboard.writeText(text || '')
      },
    },
    {
      label: note.archived ? t('note.unarchive') : t('note.archive'),
      icon: 'fa-box-archive',
      onClick: () => updateFloatingNote(note.id, { archived: !note.archived }),
    },
    ...(onMoveToFolder ? [{
      label: t('folder.moveToFolder'),
      icon: 'fa-folder-open' as const,
      onClick: () => onMoveToFolder(),
    }] : []),
    {
      label: (note.refCount ?? 0) > 0 ? t('note.deleteTitleWithRefs', { count: note.refCount ?? 0 }) : t('note.deleteTitle'),
      icon: 'fa-trash-can',
      danger: true,
      disabled: note.locked,
      onClick: () => {
        if (note.locked) return
        if ((note.refCount ?? 0) > 0) {
          if (!confirm(t('note.deleteConfirmRefs', { count: note.refCount ?? 0 }))) return
        }
        const snapshot = { ...note }
        undoManager.push(t('undoLabels.deleteNote'), () => {
          useStore.setState(s => ({ floatingNotes: [...s.floatingNotes, snapshot] }))
        })
        removeFloatingNote(note.id)
      },
    },
  ]
}
