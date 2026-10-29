import React, { useEffect, useRef, useState } from 'react'

interface SheetProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
}

// Damping: drag feels stiff after 60px
const DRAG_THRESHOLD = 80
const VELOCITY_THRESHOLD = 500 // px/s
const MAX_DRAG = 200

export function Sheet({ open, onClose, title, children }: SheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null)
  const startY = useRef(0)
  const [dragY, setDragY] = useState(0)
  const [closing, setClosing] = useState(false)
  const history = useRef<{ y: number; t: number }[]>([])

  useEffect(() => {
    document.body.style.overflow = open ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [open])

  useEffect(() => {
    if (!open) { setDragY(0); setClosing(false) }
  }, [open])

  const damped = (dy: number): number => {
    if (dy <= 0) return 0
    if (dy > MAX_DRAG) return MAX_DRAG
    // Quadratic damping: small drags feel light, large drags get heavy resistance
    return dy * (1 - dy / (MAX_DRAG * 2))
  }

  const handlePointerStart = (clientY: number) => {
    startY.current = clientY
    history.current = [{ y: clientY, t: performance.now() }]
  }

  const handlePointerMove = (clientY: number) => {
    const dy = clientY - startY.current
    setDragY(damped(dy))

    // Record position for velocity calculation (sample every ~30ms)
    const h = history.current
    const now = performance.now()
    if (h.length === 0 || now - h[h.length - 1].t > 30) {
      h.push({ y: clientY, t: now })
      if (h.length > 5) h.shift()
    }
  }

  const handlePointerEnd = (clientY: number) => {
    const h = history.current
    let velocity = 0
    if (h.length >= 2) {
      const first = h[0]
      const last = h[h.length - 1]
      const dt = (last.t - first.t) / 1000
      if (dt > 0) velocity = (last.y - first.y) / dt
    }

    const dy = clientY - startY.current
    if (dy > DRAG_THRESHOLD || velocity > VELOCITY_THRESHOLD) {
      setClosing(true)
      setTimeout(onClose, 200)
    } else {
      setDragY(0)
    }
  }

  if (!open) return null

  const sheetStyle: React.CSSProperties = closing
    ? {
        transform: `translateY(${dragY || 100}px)`,
        opacity: 0,
        transition: 'transform 0.2s ease, opacity 0.2s ease',
      }
    : dragY > 0
      ? { transform: `translateY(${dragY}px)` }
      : { animation: `slideUp 0.3s var(--ease-out)` }

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 1000,
      display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
    }}>
      <div
        style={{ position: 'absolute', inset: 0, background: 'rgba(0,0,0,0.4)' }}
        onClick={onClose}
      />

      <div
        ref={sheetRef}
        data-keyboard-aware=""
        onTouchStart={e => handlePointerStart(e.touches[0].clientY)}
        onTouchMove={e => { e.preventDefault(); handlePointerMove(e.touches[0].clientY) }}
        onTouchEnd={e => handlePointerEnd(e.changedTouches[0].clientY)}
        onMouseDown={e => handlePointerStart(e.clientY)}
        onMouseMove={e => { if (dragY > 0) handlePointerMove(e.clientY) }}
        onMouseUp={e => { if (dragY > 0) handlePointerEnd(e.clientY) }}
        onMouseLeave={e => { if (dragY > 0) handlePointerEnd(e.clientY) }}
        style={{
          position: 'relative',
          background: 'var(--bg-secondary)',
          borderTopLeftRadius: 'var(--radius-lg)',
          borderTopRightRadius: 'var(--radius-lg)',
          padding: 20,
          paddingBottom: 'calc(env(safe-area-inset-bottom, 48px) + 32px)',
          maxHeight: '85dvh',
          overflowY: 'auto',
          border: '0.5px solid var(--border-card)',
          borderBottom: 'none',
          boxShadow: 'var(--shadow-sheet)',
          transition: dragY > 0 && !closing ? 'none' : 'transform 0.25s ease',
          ...sheetStyle,
        }}
      >
        <div style={{
          width: 36, height: 4, borderRadius: 2,
          background: 'var(--text-disabled)',
          margin: '0 auto 16px',
          cursor: 'grab',
        }} />

        {title && (
          <div style={{
            fontSize: 'var(--text-title-3)', fontWeight: 600,
            marginBottom: 16,
          }}>
            {title}
          </div>
        )}
        {children}
      </div>
    </div>
  )
}
