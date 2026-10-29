import { useEffect, useRef } from 'react'

/**
 * Listens to visualViewport changes (keyboard open/close on mobile)
 * and adjusts the scroll position so the active input stays visible.
 *
 * Usage: call in a sheet/modal that contains text inputs
 */
export function useKeyboardAvoidance() {
  const viewportRef = useRef(window.visualViewport?.height ?? window.innerHeight)
  const rafRef = useRef<number | null>(null)

  useEffect(() => {
    const vp = window.visualViewport
    if (!vp) return

    const handler = () => {
      const prev = viewportRef.current
      const curr = vp.height
      const diff = prev - curr

      // Cancel any pending animation
      if (rafRef.current) {
        cancelAnimationFrame(rafRef.current)
      }

      // Use requestAnimationFrame to ensure layout is updated
      rafRef.current = requestAnimationFrame(() => {
        const el = document.activeElement
        const isInput = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')

        if (diff > 60) {
          // Keyboard opened
          if (isInput) {
            const rect = el.getBoundingClientRect()
            // Check if input bottom is below visible viewport bottom
            const viewportBottom = vp.offsetTop + curr
            const inputBottom = rect.bottom + 20 // Add 20px padding
            const offset = inputBottom - viewportBottom

            // Find sheet and add padding
            const sheet = el.closest('[data-keyboard-aware]') as HTMLElement | null
            if (sheet) {
              sheet.style.paddingBottom = `${diff + 48}px`
            }

            // Scroll input into visible area if needed
            if (offset > 0) {
              // Use scrollBy instead of scrollIntoView for better control in fixed containers
              const scrollable = el.closest('[style*="overflow-y: auto"], [style*="overflow: auto"]') as HTMLElement | null
              if (scrollable) {
                scrollable.scrollBy({ top: offset, behavior: 'smooth' })
              } else {
                el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
              }
            }
          }
        } else if (diff < -60) {
          // Keyboard closed — reset padding for all sheets
          document.querySelectorAll('[data-keyboard-aware]').forEach(el => {
            (el as HTMLElement).style.paddingBottom = ''
          })
        }
      })

      viewportRef.current = curr
    }

    vp.addEventListener('resize', handler)
    // Also trigger handler when scrolling occurs
    document.addEventListener('focusin', handler)

    return () => {
      vp.removeEventListener('resize', handler)
      document.removeEventListener('focusin', handler)
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [])
}

/**
 * Adds a bottom padding spacer equal to the keyboard height.
 * Place at the bottom of sheets/modals with inputs.
 */
export function KeyboardSpacer() {
  return (
    <div
      data-keyboard-spacer
      style={{
        height: 0,
        transition: 'height 0.25s ease',
      }}
    />
  )
}
