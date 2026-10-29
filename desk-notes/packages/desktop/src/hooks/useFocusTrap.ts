/**
 * useFocusTrap — keep keyboard focus inside an open modal/dialog.
 *
 * Behaviour:
 * - On open: remember the previously focused element and move focus to the
 *   first focusable descendant of `containerRef` (or the container itself).
 * - While open: trap Tab / Shift+Tab cycling inside the container.
 * - On close: restore focus to the previously focused element.
 *
 * P2-13 a11y groundwork — modals that mount via React portal (QuickCapture,
 * QuickSearchModal, etc.) previously had no focus management, so screen
 * reader / keyboard users could tab away into the underlying page.
 */
import { useEffect, useRef } from 'react'

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function getFocusable(container: HTMLElement): HTMLElement[] {
  const nodes = container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
  // Filter out elements that are hidden (display:none / visibility:hidden)
  return Array.from(nodes).filter(el => {
    if (el.hasAttribute('disabled')) return false
    if (el.getAttribute('aria-hidden') === 'true') return false
    const style = window.getComputedStyle(el)
    return style.display !== 'none' && style.visibility !== 'hidden'
  })
}

export function useFocusTrap(
  containerRef: React.RefObject<HTMLElement | null>,
  active: boolean,
): void {
  const previouslyFocused = useRef<HTMLElement | null>(null)

  useEffect(() => {
    if (!active) return
    const container = containerRef.current
    if (!container) return

    previouslyFocused.current = (document.activeElement as HTMLElement) ?? null

    // Defer to next tick so portal mount + autofocus useEffects settle first.
    const focusTimer = window.setTimeout(() => {
      const focusables = getFocusable(container)
      // Don't steal focus if a child useEffect already focused something inside.
      const active = document.activeElement
      if (active instanceof HTMLElement && container.contains(active)) return
      if (focusables.length > 0) focusables[0].focus()
      else container.focus()
    }, 0)

    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const focusables = getFocusable(container)
      if (focusables.length === 0) {
        e.preventDefault()
        container.focus()
        return
      }
      const first = focusables[0]
      const last = focusables[focusables.length - 1]
      const current = document.activeElement as HTMLElement | null
      if (e.shiftKey) {
        if (current === first || !container.contains(current)) {
          e.preventDefault()
          last.focus()
        }
      } else {
        if (current === last) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', handleKey, true)

    return () => {
      window.clearTimeout(focusTimer)
      document.removeEventListener('keydown', handleKey, true)
      const prev = previouslyFocused.current
      if (prev && typeof prev.focus === 'function' && document.body.contains(prev)) {
        prev.focus()
      }
      previouslyFocused.current = null
    }
  }, [active, containerRef])
}
