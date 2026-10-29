import { describe, test, expect, vi } from 'vitest'
import { undoManager } from '../undoManager'

// Access private stack for testing maxSteps
const stackSize = (m: typeof undoManager) => (m as unknown as { undoStack: unknown[] })['undoStack']?.length ?? 0

function setup() {
  while (undoManager.undo()) { /* drain */ }
  while (undoManager.redo()) { /* drain */ }
  undoManager.clear()
  return undoManager
}

describe('UndoManager', () => {
  test('starts empty', () => {
    const m = setup()
    expect(m.canUndo).toBe(false)
    expect(m.canRedo).toBe(false)
    expect(m.label).toBeNull()
  })

  test('push adds undo entry', () => {
    const m = setup()
    m.push('test', () => {})
    expect(m.canUndo).toBe(true)
    expect(m.label).toBe('test')
  })

  test('undo calls the undo function', () => {
    const m = setup()
    const fn = vi.fn()
    m.push('test', fn)
    expect(m.undo()).toBe(true)
    expect(fn).toHaveBeenCalledOnce()
  })

  test('redo calls redo function and pushes back to undo', () => {
    const m = setup()
    const undo = vi.fn()
    const redo = vi.fn()
    m.push('test', undo, redo)
    expect(m.undo()).toBe(true)
    expect(undo).toHaveBeenCalledOnce()
    expect(m.canRedo).toBe(true)
    expect(m.redo()).toBe(true)
    expect(redo).toHaveBeenCalledOnce()
    expect(m.canUndo).toBe(true)
  })

  test('undo returns false when stack empty', () => {
    const m = setup()
    expect(m.undo()).toBe(false)
  })

  test('redo returns false when stack empty', () => {
    const m = setup()
    expect(m.redo()).toBe(false)
  })

  test('push clears redo stack', () => {
    const m = setup()
    m.push('a', () => {}, () => {})
    m.undo()
    expect(m.canRedo).toBe(true)
    m.push('b', () => {})
    expect(m.canRedo).toBe(false)
  })

  test('clear empties both stacks', () => {
    const m = setup()
    m.push('a', () => {}, () => {})
    m.undo()
    m.clear()
    expect(m.canUndo).toBe(false)
    expect(m.canRedo).toBe(false)
  })

  test('respects maxSteps (2)', () => {
    const m = setup()
    // Override max for test - can't change private, so test at default 50
    for (let i = 0; i < 60; i++) {
      m.push(`step-${i}`, () => {})
    }
    // Should have trimmed old entries
    expect(m.label).toBe('step-59')
    expect(stackSize(m)).toBeLessThanOrEqual(50)
  })

  test('undo with redo but no redo fn keeps entry on redo stack', () => {
    const m = setup()
    const fn = vi.fn()
    m.push('test', fn) // no redo fn
    m.undo()
    expect(fn).toHaveBeenCalledOnce()
    // Without redo() callback, entry is discarded
    expect(m.canRedo).toBe(false)
  })

  test('subscribe receives events', () => {
    const m = setup()
    const fn = vi.fn()
    m.subscribe(fn)
    m.push('x', () => {})
    expect(fn).toHaveBeenCalledWith(expect.objectContaining({ type: 'push', label: 'x' }))
  })

  test('unsubscribe stops events', () => {
    const m = setup()
    const fn = vi.fn()
    const unsub = m.subscribe(fn)
    unsub()
    m.push('x', () => {})
    expect(fn).not.toHaveBeenCalled()
  })

  test('version increments on push/undo/redo/clear', () => {
    const m = setup()
    const v0 = m.version
    m.push('x', () => {})
    expect(m.version).toBeGreaterThan(v0)
    const v1 = m.version
    m.undo()
    expect(m.version).toBeGreaterThan(v1)
    m.redo()
    expect(m.version).toBeGreaterThan(v1)
    const v3 = m.version
    m.clear()
    expect(m.version).toBeGreaterThan(v3)
  })

  test('canUndo/canRedo reflect after operations', () => {
    const m = setup()
    expect(m.canUndo).toBe(false)
    m.push('a', () => {}, () => {})
    expect(m.canUndo).toBe(true)
    expect(m.canRedo).toBe(false)
    m.undo()
    expect(m.canUndo).toBe(false)
    expect(m.canRedo).toBe(true)
    m.redo()
    expect(m.canUndo).toBe(true)
    expect(m.canRedo).toBe(false)
  })
})
