import { describe, it, expect, vi, beforeEach } from 'vitest'
import { getSaveStatus, setSaveStatus, onSaveStatusChange } from '../saveTracker'

describe('saveTracker', () => {
  beforeEach(() => {
    setSaveStatus('idle')
  })

  it('starts in idle state', () => {
    expect(getSaveStatus()).toBe('idle')
  })

  it('setSaveStatus updates state', () => {
    setSaveStatus('saving')
    expect(getSaveStatus()).toBe('saving')
    setSaveStatus('saved')
    expect(getSaveStatus()).toBe('saved')
  })

  it('onSaveStatusChange receives initial state', () => {
    const fn = vi.fn()
    const unsub = onSaveStatusChange(fn)
    expect(fn).toHaveBeenCalledWith('idle')
    unsub()
  })

  it('onSaveStatusChange receives updates', () => {
    const fn = vi.fn()
    const unsub = onSaveStatusChange(fn)
    fn.mockClear()

    setSaveStatus('saving')
    expect(fn).toHaveBeenCalledWith('saving')
    unsub()
  })

  it('unsubscribe stops receiving updates', () => {
    const fn = vi.fn()
    const unsub = onSaveStatusChange(fn)
    fn.mockClear()
    unsub()

    setSaveStatus('saving')
    expect(fn).not.toHaveBeenCalled()
  })

  it('auto-transitions from saved to idle after 2s', () => {
    vi.useFakeTimers()
    setSaveStatus('saved')
    expect(getSaveStatus()).toBe('saved')

    vi.advanceTimersByTime(2000)
    expect(getSaveStatus()).toBe('idle')
    vi.useRealTimers()
  })

  it('saving during saved state cancels auto-idle timer', () => {
    vi.useFakeTimers()
    setSaveStatus('saved')
    setSaveStatus('saving')
    vi.advanceTimersByTime(2000)
    // Should still be 'saving' since we changed before the timer fired
    expect(getSaveStatus()).toBe('saving')
    vi.useRealTimers()
  })

  it('multiple listeners all receive updates', () => {
    const fn1 = vi.fn()
    const fn2 = vi.fn()
    const unsub1 = onSaveStatusChange(fn1)
    const unsub2 = onSaveStatusChange(fn2)
    fn1.mockClear()
    fn2.mockClear()

    setSaveStatus('saving')
    expect(fn1).toHaveBeenCalledWith('saving')
    expect(fn2).toHaveBeenCalledWith('saving')
    unsub1()
    unsub2()
  })
})
