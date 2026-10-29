import React, { useEffect, useRef } from 'react'

interface SheetProps {
  open: boolean
  onClose: () => void
  title?: string
  children: React.ReactNode
}

export const Sheet: React.FC<SheetProps> = ({ open, onClose, title, children }) => {
  const sheetRef = useRef<HTMLDivElement>(null)
  const startY = useRef(0)

  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden'
    } else {
      document.body.style.overflow = ''
    }
    return () => {
      document.body.style.overflow = ''
    }
  }, [open])

  const handleTouchStart = (e: React.TouchEvent) => {
    if (e.touches[0].clientY > 100) return // only if dragging from top
    startY.current = e.touches[0].clientY
  }

  const handleTouchMove = (e: React.TouchEvent) => {
    const dy = e.touches[0].clientY - startY.current
    if (dy > 0 && sheetRef.current) {
      sheetRef.current.style.transform = `translateY(${dy}px)`
    }
  }

  const handleTouchEnd = (e: React.TouchEvent) => {
    const dy = e.changedTouches[0].clientY - startY.current
    if (sheetRef.current) {
      sheetRef.current.style.transform = ''
    }
    if (dy > 80) onClose()
  }

  if (!open) return null

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
      }}
    >
      {/* Backdrop */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: 'rgba(0,0,0,0.5)',
        }}
        onClick={onClose}
      />

      {/* Sheet */}
      <div
        ref={sheetRef}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        style={{
          position: 'relative',
          background: 'var(--bg-secondary)',
          borderTopLeftRadius: 'var(--radius-lg)',
          borderTopRightRadius: 'var(--radius-lg)',
          padding: 20,
          paddingBottom: 'calc(var(--safe-bottom) + 20px)',
          maxHeight: '85dvh',
          overflowY: 'auto',
          animation: `slideUp var(--duration-md) var(--ease-out)`,
          border: '1px solid var(--border-card)',
          borderBottom: 'none',
        }}
      >
        {/* Drag handle */}
        <div
          style={{
            width: 36,
            height: 4,
            borderRadius: 2,
            background: 'var(--text-disabled)',
            margin: '0 auto 16px',
          }}
        />

        {title && (
          <div
            style={{
              fontSize: 'var(--text-h2)',
              fontWeight: 600,
              marginBottom: 16,
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            {title}
          </div>
        )}
        {children}
      </div>

      <style>{`
        @keyframes slideUp {
          from { transform: translateY(100%); }
          to { transform: translateY(0); }
        }
      `}</style>
    </div>
  )
}
