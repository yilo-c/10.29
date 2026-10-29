export type SaveState = 'idle' | 'saving' | 'saved' | 'error'

let state: SaveState = 'idle'
let listeners: Array<(s: SaveState) => void> = []
let autoIdleTimer: ReturnType<typeof setTimeout> | null = null

export function getSaveStatus(): SaveState {
  return state
}

export function setSaveStatus(s: SaveState): void {
  if (autoIdleTimer) {
    clearTimeout(autoIdleTimer)
    autoIdleTimer = null
  }
  state = s
  listeners.forEach(fn => fn(s))

  // Auto-transition from 'saved'/'error' → 'idle' after 2s
  if (s === 'saved' || s === 'error') {
    autoIdleTimer = setTimeout(() => {
      state = 'idle'
      listeners.forEach(fn => fn('idle'))
      autoIdleTimer = null
    }, 2000)
  }
}

export function onSaveStatusChange(fn: (s: SaveState) => void): () => void {
  listeners = [...listeners, fn]
  fn(state)
  return () => {
    listeners = listeners.filter(f => f !== fn)
  }
}
