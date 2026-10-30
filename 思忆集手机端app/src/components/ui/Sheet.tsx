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
  /**
   * 本次触摸是否「从内容顶部开始」。只有它为 true，下拉才当作关闭手势；
   * 否则交给内容自身滚动。用 ref：它在 touchmove 里被高频读取，
   * 走 state 会触发额外渲染且读不到最新值。
   */
  const pullToCloseRef = useRef(false)
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
        onTouchStart={e => {
          /*
           * 只有在「内容已滚到顶部」时，下拉才被视为「关闭手势」；
           * 否则手势属于内容滚动。不区分就会导致长内容面板滚不动——
           * 原实现无条件 preventDefault()，让 overflowY:auto 形同虚设，
           * 带输入框的长 Sheet（新建待办、笔记编辑）手指滑不动，
           * 只能靠拖拽关闭，用户会以为是卡死。
           */
          const el = sheetRef.current
          pullToCloseRef.current = !el || el.scrollTop <= 0
          handlePointerStart(e.touches[0].clientY)
        }}
        onTouchMove={e => {
          // 内容未到顶 → 放行原生滚动；到顶且向下拉 → 阻止默认行为交给下拉关闭
          if (pullToCloseRef.current && dragY >= 0) {
            e.preventDefault()
          }
          handlePointerMove(e.touches[0].clientY)
        }}
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
          /*
           * 用项目自己的 --safe-bottom，而不是 env(safe-area-inset-bottom)。
           * layout.css 已注明本项目在三键导航下 env() 恒为 0，
           * 而 --safe-bottom 是由原生 getInsets 测出来写进 CSS 变量的，
           * 两者混用会让底部按钮被系统导航键压住。
           */
          paddingBottom: 'calc(var(--safe-bottom, 0px) + 32px)',
          maxHeight: '85dvh',
          overflowY: 'auto',
          WebkitOverflowScrolling: 'touch',
          overscrollBehaviorY: 'contain',
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
