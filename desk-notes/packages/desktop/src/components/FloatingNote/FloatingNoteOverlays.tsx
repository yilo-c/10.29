import React from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { onContentEdit } from '../../store/contentEditTracker'

interface FloatingNoteOverlaysProps {
  noteId: string
  resizingFN: boolean
  resizeWidthFN: number
  resizeTargetRefFN: React.MutableRefObject<HTMLImageElement | null>
  selectedImgFN: HTMLImageElement | null
  lightboxSrcFN: string | null
  editorRef: React.RefObject<HTMLDivElement>
  onSetResizingFN: (v: boolean) => void
  onSetResizeWidthFN: (v: number) => void
  onSetLightboxSrcFN: (v: string | null) => void
  onDragResizeStart: (e: React.MouseEvent) => void
  t: (key: string) => string
}

const FloatingNoteOverlays: React.FC<FloatingNoteOverlaysProps> = ({
  noteId, resizingFN, resizeWidthFN, resizeTargetRefFN,
  selectedImgFN, lightboxSrcFN, editorRef,
  onSetResizingFN, onSetResizeWidthFN, onSetLightboxSrcFN,
  onDragResizeStart, t,
}) => {
  const updateFloatingNote = useStore(s => s.updateFloatingNote)

  const handleResizeBackdrop = () => {
    const img = resizeTargetRefFN.current
    if (img) {
      onContentEdit(noteId)
      updateFloatingNote(noteId, { content: editorRef.current?.innerHTML || '' })
    }
    onSetResizingFN(false)
    resizeTargetRefFN.current = null
  }

  const handleResizeSlider = (pct: number) => {
    onSetResizeWidthFN(pct)
    const img = resizeTargetRefFN.current
    if (img) {
      const w = img.naturalWidth || img.width
      img.style.width = `${Math.round(w * pct / 100)}px`
    }
  }

  return (
    <>
      {/* Image resize popup */}
      {resizingFN && resizeTargetRefFN.current && (
        <div className="fixed inset-0 z-[10001]" onMouseDown={handleResizeBackdrop}>
          <div
            className="fixed z-[10001] rounded-xl border shadow-2xl p-3 min-w-[200px]"
            style={{
              ...(() => {
                const rect = resizeTargetRefFN.current!.getBoundingClientRect()
                let top = rect.bottom + 8
                let left = rect.left
                if (top + 120 > window.innerHeight) top = rect.top - 130
                if (left + 220 > window.innerWidth) left = window.innerWidth - 220
                if (left < 8) left = 8
                return { left, top }
              })(),
              background: 'var(--panel-bg-solid, rgba(28,28,38,0.98))',
              borderColor: 'var(--border-color, rgba(255,255,255,0.08))',
            }}
            onMouseDown={e => e.stopPropagation()}
          >
            <div className="text-2xs text-text-secondary mb-2">{t('note.imageWidth')}</div>
            <div className="flex items-center gap-2 mb-2">
              <input
                type="range" min={10} max={200} value={resizeWidthFN}
                onChange={e => handleResizeSlider(Number(e.target.value))}
                className="flex-1 h-1 accent-fluent-blue cursor-pointer"
              />
              <span className="text-xs text-text-secondary w-[34px] text-right tabular-nums">
                {resizeWidthFN}%
              </span>
            </div>
            <div className="flex items-center gap-1">
              {[30, 50, 80, 100, 150, 200].map(pct => (
                <button
                  key={pct}
                  onClick={() => handleResizeSlider(pct)}
                  className={`px-2 py-0.5 rounded text-2xs transition-all ${
                    resizeWidthFN === pct
                      ? 'bg-fluent-blue/20 text-fluent-blue/80'
                      : 'text-white/55 hover:text-text-secondary hover:bg-white/[0.06]'
                  }`}
                >
                  {pct}%
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Selected image overlay + drag handle */}
      {selectedImgFN && selectedImgFN.isConnected && !resizingFN && (() => {
        const rect = selectedImgFN.getBoundingClientRect()
        return (
          <>
            <div className="fixed z-[10000] pointer-events-none" style={{
              left: rect.left - 2, top: rect.top - 2,
              width: rect.width + 4, height: rect.height + 4,
              border: '1.5px solid rgba(96,165,250,0.5)',
              borderRadius: '3px',
            }} />
            <div
              className="fixed z-[10000] flex items-center justify-center cursor-se-resize"
              onMouseDown={onDragResizeStart}
              style={{
                left: rect.right - 7, top: rect.bottom - 7,
                width: 14, height: 14,
                background: 'rgba(96,165,250,0.85)',
                borderRadius: '2px',
              }}
            >
              <svg width="8" height="8" viewBox="0 0 8 8" fill="none">
                <path d="M8 0v8H0" stroke="white" strokeWidth="1.5" />
              </svg>
            </div>
          </>
        )
      })()}

      {/* Lightbox */}
      {lightboxSrcFN && (
        <div
          className="fixed inset-0 z-[10002] flex items-center justify-center bg-black/70 backdrop-blur-sm"
          onMouseDown={() => onSetLightboxSrcFN(null)}
        >
          <button
            className="absolute top-4 right-4 w-8 h-8 flex items-center justify-center rounded-full bg-white/[0.08] hover:bg-white/[0.15] text-text-secondary hover:text-white transition-all z-10"
            onClick={() => onSetLightboxSrcFN(null)}
          >
            <Icon icon="fa-solid fa-xmark text-sm" />
          </button>
          <img
            src={lightboxSrcFN}
            /* 首屏关键图：lightbox 仅在用户点击放大时挂载，需立即可见，故 eager */
            loading="eager"
            className="max-w-[90vw] max-h-[90vh] object-contain rounded-xl shadow-2xl select-none"
            style={{ background: 'rgba(0,0,0,0.3)' }}
            onMouseDown={e => e.stopPropagation()}
            onClick={() => onSetLightboxSrcFN(null)}
          />
        </div>
      )}
    </>
  )
}

export default FloatingNoteOverlays
