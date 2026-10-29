export type ErrorLevel = 'error' | 'warn' | 'info' | 'success'

export interface ErrorMessage {
  id: string
  message: string
  level: ErrorLevel
  timestamp: number
}

export type ErrorEvent =
  | { type: 'push'; message: ErrorMessage }
  | { type: 'dismiss'; id: string }
  | { type: 'clear' }

let idCounter = 0

class ErrorStore {
  private active = new Map<string, ErrorMessage>()
  private listeners = new Set<(event: ErrorEvent) => void>()

  push(message: string, level: ErrorLevel = 'error'): string {
    const id = `err_${++idCounter}_${Date.now()}`
    const msg: ErrorMessage = { id, message, level, timestamp: Date.now() }
    this.active.set(id, msg)
    const event: ErrorEvent = { type: 'push', message: msg }
    for (const fn of this.listeners) fn(event)
    return id
  }

  dismiss(id: string): void {
    this.active.delete(id)
    const event: ErrorEvent = { type: 'dismiss', id }
    for (const fn of this.listeners) fn(event)
  }

  clear(): void {
    this.active.clear()
    const event: ErrorEvent = { type: 'clear' }
    for (const fn of this.listeners) fn(event)
  }

  get activeErrors(): ErrorMessage[] {
    return [...this.active.values()]
  }

  /** Convenience methods */
  error(message: string): string { return this.push(message, 'error') }
  warn(message: string): string { return this.push(message, 'warn') }
  info(message: string): string { return this.push(message, 'info') }
  success(message: string): string { return this.push(message, 'success') }

  subscribe(fn: (event: ErrorEvent) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }
}

export const errorStore = new ErrorStore()
