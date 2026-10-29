import { useRef, useState, useCallback, useEffect } from 'react'
import { onContentEdit } from '../../store/contentEditTracker'
import { useTranslation } from '../../i18n'
import type { MenuItem } from './ContextMenu'
import type { FloatingNote } from '../../types'

export function useNoteEditorImages(
  note: FloatingNote | undefined,
  editorRef: React.RefObject<HTMLDivElement>,
  updateFloatingNote: (id: string, data: Partial<FloatingNote>) => void,
) {
  const { t } = useTranslation()
  const imgCtxRef = useRef<HTMLImageElement | null>(null)
  const resizeTargetRef = useRef<HTMLImageElement | null>(null)
  const [imgCtxMenu, setImgCtxMenu] = useState<{ x: number; y: number } | null>(null)
  const [resizing, setResizing] = useState(false)
  const [resizeWidth, setResizeWidth] = useState(100)
  const [lightboxSrc, setLightboxSrc] = useState<string | null>(null)
  const selectedImgRef = useRef<HTMLImageElement | null>(null)
  const dragStateRef = useRef<{ startX: number; startW: number } | null>(null)
  const dragCleanupRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    return () => { dragCleanupRef.current?.() }
  }, [])
  const [selectedImg, setSelectedImg] = useState<HTMLImageElement | null>(null)

  const handleImageDoubleClick = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement
    if (target.tagName === 'IMG' && editorRef.current?.contains(target)) {
      e.preventDefault()
      setLightboxSrc((target as HTMLImageElement).getAttribute('src') || '')
    }
  }, [editorRef])

  useEffect(() => {
    const el = editorRef.current
    if (!el) return
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      if (target.tagName === 'IMG' && el.contains(target)) {
        e.preventDefault()
        imgCtxRef.current = target as HTMLImageElement
        setImgCtxMenu({ x: e.clientX, y: e.clientY })
      }
    }
    el.addEventListener('contextmenu', handler)
    return () => el.removeEventListener('contextmenu', handler)
  }, [editorRef])

  const getImageMenuItems = useCallback((): MenuItem[] => {
    const img = imgCtxRef.current
    if (!img || !note) return []
    return [
      {
        label: t('note.resizeImage'),
        icon: 'fa-expand',
        onClick: () => {
          resizeTargetRef.current = img
          const pct = Math.round((img.width / (img.naturalWidth || img.width)) * 100)
          setResizeWidth(Math.max(10, Math.min(200, pct)))
          setResizing(true)
          setImgCtxMenu(null)
        },
      },
      {
        label: t('note.alignLeft'),
        icon: 'fa-align-left',
        onClick: () => doAlign('left'),
      },
      {
        label: t('note.alignCenter'),
        icon: 'fa-align-center',
        onClick: () => doAlign('center'),
      },
      {
        label: t('note.alignRight'),
        icon: 'fa-align-right',
        onClick: () => doAlign('right'),
      },
      {
        label: img.nextElementSibling?.matches('.image-caption')
          ? t('note.removeCaption') : t('note.addCaption'),
        icon: 'fa-quote-right',
        onClick: () => {
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
          if (!note) return
          onContentEdit(note.id)
          if (editorRef.current) updateFloatingNote(note.id, { content: editorRef.current.innerHTML })
        },
      },
      {
        label: t('note.exportImage'),
        icon: 'fa-download',
        onClick: () => {
          const link = document.createElement('a')
          link.href = img.src
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
          img.remove()
          if (!note) return
          onContentEdit(note.id)
          if (editorRef.current) updateFloatingNote(note.id, { content: editorRef.current.innerHTML })
        },
      },
    ]
  }, [note, t, updateFloatingNote, editorRef])

  const doAlign = useCallback((align: 'left' | 'center' | 'right') => {
    const img = imgCtxRef.current
    if (!img || !note) return
    img.style.display = 'block'
    img.style.float = 'none'
    if (align === 'center') {
      img.style.marginLeft = 'auto'
      img.style.marginRight = 'auto'
    } else if (align === 'right') {
      img.style.marginLeft = 'auto'
      img.style.marginRight = '0'
    } else {
      img.style.marginLeft = '0'
      img.style.marginRight = 'auto'
    }
    onContentEdit(note.id)
    if (editorRef.current) updateFloatingNote(note.id, { content: editorRef.current.innerHTML })
    setImgCtxMenu(null)
  }, [note, updateFloatingNote, editorRef])

  const handleResizeSlider = useCallback((pct: number) => {
    const img = resizeTargetRef.current
    if (!img || !note) return
    setResizeWidth(pct)
    const w = img.naturalWidth || img.width
    img.style.width = `${Math.round(w * pct / 100)}px`
  }, [note])

  const commitResize = useCallback(() => {
    const img = resizeTargetRef.current
    if (img && note) {
      onContentEdit(note.id)
      if (editorRef.current) updateFloatingNote(note.id, { content: editorRef.current.innerHTML })
    }
    setResizing(false)
    resizeTargetRef.current = null
  }, [note, updateFloatingNote, editorRef])

  const getResizePos = (img: HTMLImageElement) => {
    const rect = img.getBoundingClientRect()
    let top = rect.bottom + 8
    let left = rect.left
    if (top + 120 > window.innerHeight) top = rect.top - 130
    if (left + 220 > window.innerWidth) left = window.innerWidth - 220
    if (left < 8) left = 8
    return { left, top }
  }

  const handleEditorMouseDown = useCallback((e: React.MouseEvent) => {
    const target = e.target as HTMLElement
    if (target.tagName === 'IMG' && editorRef.current?.contains(target)) {
      selectedImgRef.current = target as HTMLImageElement
      setSelectedImg(target as HTMLImageElement)
    } else if (selectedImgRef.current) {
      selectedImgRef.current = null
      setSelectedImg(null)
    }
  }, [editorRef])

  const handleDragResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const img = selectedImgRef.current
    if (!img) return
    dragStateRef.current = { startX: e.clientX, startW: img.width }

    const onMove = (ev: MouseEvent) => {
      const ds = dragStateRef.current
      if (!ds) return
      const delta = ev.clientX - ds.startX
      const newW = Math.max(50, ds.startW + delta)
      img.style.width = `${newW}px`
    }
    const onUp = () => {
      dragStateRef.current = null
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      dragCleanupRef.current = null
      if (note) {
        onContentEdit(note.id)
        if (editorRef.current) updateFloatingNote(note.id, { content: editorRef.current.innerHTML })
      }
    }
    if (dragCleanupRef.current) dragCleanupRef.current()
    dragCleanupRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [note, updateFloatingNote, editorRef])

  return {
    imgCtxMenu,
    setImgCtxMenu,
    resizing,
    resizeWidth,
    lightboxSrc,
    setLightboxSrc,
    selectedImg,
    resizeTargetRef,
    handleImageDoubleClick,
    handleEditorMouseDown,
    handleDragResizeStart,
    handleResizeSlider,
    commitResize,
    getImageMenuItems,
    getResizePos,
  }
}
