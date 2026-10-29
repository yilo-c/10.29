import { uid } from '../utils/helpers'

interface UndoEntry {
  id: string
  label: string
  timestamp: number
  undo: () => void
  redo?: () => void
}

export type UndoEvent =
  | { type: 'push'; label: string; timestamp: number }
  | { type: 'undo'; label: string; timestamp: number }
  | { type: 'redo'; label: string; timestamp: number }
  | { type: 'clear' }

class UndoManager {
  private undoStack: UndoEntry[] = []
  private redoStack: UndoEntry[] = []
  private maxSteps: number
  private listeners = new Set<(event: UndoEvent) => void>()
  private _version = 0

  constructor(maxSteps = 50) {
    this.maxSteps = maxSteps
  }

  push(label: string, undo: () => void, redo?: () => void): void {
    const entry: UndoEntry = { id: uid(), label, timestamp: Date.now(), undo, redo }
    this.undoStack.push(entry)
    if (this.undoStack.length > this.maxSteps) {
      this.undoStack.shift()
    }
    this.redoStack = []
    this._version++
    this.emit({ type: 'push', label, timestamp: entry.timestamp })
  }

  undo(): boolean {
    const entry = this.undoStack.pop()
    if (!entry) return false
    entry.undo()
    if (entry.redo) {
      this.redoStack.push(entry)
    }
    this._version++
    this.emit({ type: 'undo', label: entry.label, timestamp: Date.now() })
    return true
  }

  redo(): boolean {
    const entry = this.redoStack.pop()
    if (!entry) return false
    if (entry.redo) {
      entry.redo()
    }
    this.undoStack.push(entry)
    while (this.undoStack.length > this.maxSteps) this.undoStack.shift()
    this._version++
    this.emit({ type: 'redo', label: entry.label, timestamp: Date.now() })
    return true
  }

  getHistory(): ReadonlyArray<{ id: string; label: string; timestamp: number; canUndo: boolean }> {
    return this.undoStack.map((entry, i) => ({
      id: entry.id,
      label: entry.label,
      timestamp: entry.timestamp,
      canUndo: i === this.undoStack.length - 1,
    }))
  }

  clear(): void {
    this.undoStack = []
    this.redoStack = []
    this._version++
    this.emit({ type: 'clear' })
  }

  get canUndo(): boolean { return this.undoStack.length > 0 }
  get canRedo(): boolean { return this.redoStack.length > 0 }
  get label(): string | null {
    return this.undoStack.length > 0 ? this.undoStack[this.undoStack.length - 1].label : null
  }
  get version(): number { return this._version }

  subscribe(fn: (event: UndoEvent) => void): () => void {
    this.listeners.add(fn)
    return () => this.listeners.delete(fn)
  }

  private emit(event: UndoEvent): void {
    for (const fn of this.listeners) fn(event)
  }
}

export const undoManager = new UndoManager()
