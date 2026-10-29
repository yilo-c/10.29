import { useState, useRef, useCallback } from 'react'

interface SwipeableRowProps {
  children: React.ReactNode
  onDelete: () => void
  deleteLabel?: string
}

export function SwipeableRow({ children, onDelete, deleteLabel = '删除' }: SwipeableRowProps) {
  const [offsetX, setOffsetX] = useState(0)
  const startX = useRef(0)
  const isSwiping = useRef(false)

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    startX.current = e.touches[0].clientX
    isSwiping.current = false
    setOffsetX(0)
  }, [])

  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    const dx = e.touches[0].clientX - startX.current
    if (dx < 0) {
      isSwiping.current = true
      setOffsetX(Math.max(dx, -100))
    }
  }, [])

  const handleTouchEnd = useCallback((e: React.TouchEvent) => {
    const dx = e.changedTouches[0].clientX - startX.current
    if (dx < -60) {
      onDelete()
    }
    setOffsetX(0)
    isSwiping.current = false
  }, [onDelete])

  return (
    <div style={{ position: 'relative', overflow: 'hidden' }}>
      {/* Delete hint behind */}
      <div style={{
        position: 'absolute', right: 0, top: 0, bottom: 0, width: 100,
        background: 'var(--danger)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        borderRadius: 'var(--radius-lg)',
      }}>
        <span style={{ color: '#fff', fontSize: 'var(--text-footnote)', fontWeight: 600 }}>
          {deleteLabel}
        </span>
      </div>

      {/* Content */}
      <div
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        style={{
          transform: `translateX(${offsetX}px)`,
          transition: isSwiping.current ? 'none' : 'transform 0.2s ease',
          position: 'relative',
          background: 'var(--bg-primary)',
          zIndex: 1,
        }}
      >
        {children}
      </div>
    </div>
  )
}
