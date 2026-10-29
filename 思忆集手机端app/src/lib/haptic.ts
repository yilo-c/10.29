/**
 * Standardized haptic feedback patterns.
 * All vibration durations in milliseconds.
 *
 * Usage:
 *   haptic.light()   — tap, button press
 *   haptic.medium()  — confirm, toggle
 *   haptic.heavy()   — destructive action
 *   haptic.success() — positive completion
 */
export const haptic = {
  light: () => navigator.vibrate?.(6),
  medium: () => navigator.vibrate?.(10),
  heavy: () => navigator.vibrate?.(15),
  success: () => navigator.vibrate?.(12),
  error: () => navigator.vibrate?.([20, 50, 20]),
  /** Pattern for pulsing alarm */
  alarm: () => navigator.vibrate?.([200, 100, 200, 100, 200]),
}

/**
 * Touch feedback: apply scale-down on touch start, restore on touch end.
 * Attach to button elements.
 */
export const touchScale = {
  onStart: (e: React.TouchEvent | React.MouseEvent) => {
    const el = e.currentTarget as HTMLElement
    el.style.transition = 'transform 0.1s ease'
    el.style.transform = 'scale(0.94)'
  },
  onEnd: (e: React.TouchEvent | React.MouseEvent) => {
    const el = e.currentTarget as HTMLElement
    el.style.transform = 'scale(1)'
  },
}
