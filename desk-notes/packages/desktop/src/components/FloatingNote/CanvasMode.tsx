import React, { useState, useCallback, useRef, useEffect, useId } from 'react'
import { AnimatePresence } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { uid } from '../../utils/helpers'
import type { FloatingNote } from '../../types'
import { useTranslation } from '../../i18n'

const DOT_SPACING = 24

interface CanvasModeProps {
  notes: FloatingNote[]
  renderNote: (note: FloatingNote) => React.ReactNode
}

const fn = (n: number, m: number) => ((n % m) + m) % m

const CanvasMode: React.FC<CanvasModeProps> = ({ notes, renderNote }) => {
  const { t } = useTranslation()
  const canvas = useStore(s => s.canvas)
  const setCanvas = useStore(s => s.setCanvas)
  const addFloatingNote = useStore(s => s.addFloatingNote)
  const nextZ = useStore(s => s.nextZ)

  const [isPanning, setIsPanning] = useState(false)
  const [spaceHeld, setSpaceHeld] = useState(false)
  const panRef = useRef({ startX: 0, startY: 0, startOx: 0, startOy: 0 })
  const containerRef = useRef<HTMLDivElement>(null)
  const gridId = useId()

  // Space key toggles pan mode
  useEffect(() => {
    const onDown = (e: KeyboardEvent) => {
      if (e.code === 'Space' && !e.repeat
        && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement)) {
        e.preventDefault()
        setSpaceHeld(true)
      }
    }
    const onUp = (e: KeyboardEvent) => {
      if (e.code === 'Space') { setSpaceHeld(false); setIsPanning(false) }
    }
    window.addEventListener('keydown', onDown)
    window.addEventListener('keyup', onUp)
    return () => { window.removeEventListener('keydown', onDown); window.removeEventListener('keyup', onUp) }
  }, [])

  // Cursor
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    el.style.cursor = isPanning ? 'grabbing' : spaceHeld ? 'grab' : 'default'
  }, [isPanning, spaceHeld])

  const canPan = (e: React.MouseEvent) => e.button === 1 || (e.button === 0 && spaceHeld)

  const handleWheel = useCallback((e: React.WheelEvent) => {
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault()
      const rect = containerRef.current?.getBoundingClientRect()
      if (!rect) return
      const mx = e.clientX - rect.left
      const my = e.clientY - rect.top
      const ratio = e.deltaY > 0 ? 0.9 : 1.1
      const nz = Math.max(0.1, Math.min(5, canvas.zoom * ratio))
      setCanvas({
        zoom: nz,
        offsetX: mx - (mx - canvas.offsetX) * (nz / canvas.zoom),
        offsetY: my - (my - canvas.offsetY) * (nz / canvas.zoom),
      })
    } else {
      setCanvas({
        offsetX: canvas.offsetX - e.deltaX,
        offsetY: canvas.offsetY - e.deltaY,
      })
    }
  }, [canvas, setCanvas])

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (!canPan(e)) return
    e.preventDefault()
    setIsPanning(true)
    panRef.current = { startX: e.clientX, startY: e.clientY, startOx: canvas.offsetX, startOy: canvas.offsetY }
  }, [canvas.offsetX, canvas.offsetY, spaceHeld])

  const handleMouseMove = useCallback((e: React.MouseEvent) => {
    if (!isPanning) return
    const p = panRef.current
    setCanvas({
      offsetX: p.startOx + (e.clientX - p.startX),
      offsetY: p.startOy + (e.clientY - p.startY),
    })
  }, [isPanning, setCanvas])

  const handlePanEnd = useCallback(() => { setIsPanning(false) }, [])

  const handleDoubleClick = useCallback((e: React.MouseEvent) => {
    if (isPanning) return
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return
    const x = (e.clientX - rect.left - canvas.offsetX) / canvas.zoom
    const y = (e.clientY - rect.top - canvas.offsetY) / canvas.zoom
    addFloatingNote({
      id: uid(), type: 'text', title: '', content: '',
      x: Math.max(0, x - 130), y: Math.max(0, y - 30),
      width: 260, height: 200, zIndex: nextZ,
    })
  }, [canvas, addFloatingNote, nextZ, isPanning])

  const spacing = DOT_SPACING * canvas.zoom
  const gridX = fn(canvas.offsetX, spacing)
  const gridY = fn(canvas.offsetY, spacing)
  const dotR = Math.max(0.5, 0.5 * canvas.zoom)

  return (
    <div
      ref={containerRef}
      className="absolute inset-0 overflow-hidden select-none"
      onWheel={handleWheel}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handlePanEnd}
      onMouseLeave={handlePanEnd}
      onDoubleClick={handleDoubleClick}
    >
      {/* Dot grid background */}
      <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ zIndex: 0 }}>
        <defs>
          <pattern id={gridId} x={gridX} y={gridY} width={spacing} height={spacing} patternUnits="userSpaceOnUse">
            <circle cx={spacing / 2} cy={spacing / 2} r={dotR} fill="rgba(255,255,255,0.06)" />
          </pattern>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${gridId})`} />
      </svg>

      {/* Notes with pan/zoom transform */}
      <div
        className="absolute inset-0"
        style={{
          transform: `translate(${canvas.offsetX}px,${canvas.offsetY}px) scale(${canvas.zoom})`,
          transformOrigin: '0 0',
          zIndex: 1,
        }}
      >
        <AnimatePresence>
          {notes.map(renderNote)}
        </AnimatePresence>
      </div>

      {/* Zoom indicator */}
      <div className="absolute bottom-3 right-3 text-2xs text-text-dim pointer-events-none select-none tabular-nums z-20">
        {Math.round(canvas.zoom * 100)}%
      </div>

      {/* Hint tooltip on first use */}
      <div className="absolute bottom-3 left-3 text-[8px] text-text-dim pointer-events-none select-none z-20">
        {t('canvasMode.hint')}
      </div>
    </div>
  )
}

export default CanvasMode
