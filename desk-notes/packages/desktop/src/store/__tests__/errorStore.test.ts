import { describe, test, expect, vi } from 'vitest'
import { errorStore } from '../errorStore'

describe('errorStore', () => {
  test('push adds error and returns id', () => {
    errorStore.clear()
    const id = errorStore.push('test error', 'error')
    expect(id).toBeTruthy()
    expect(typeof id).toBe('string')
    expect(errorStore.activeErrors.some(e => e.id === id)).toBe(true)
    errorStore.dismiss(id)
  })

  test('error() shorthand', () => {
    errorStore.clear()
    const id = errorStore.error('err msg')
    expect(errorStore.activeErrors.some(e => e.id === id)).toBe(true)
    errorStore.dismiss(id)
  })

  test('warn() shorthand sets level', () => {
    errorStore.clear()
    const id = errorStore.warn('warn msg')
    const err = errorStore.activeErrors.find(e => e.id === id)
    expect(err?.level).toBe('warn')
    errorStore.dismiss(id)
  })

  test('info() shorthand sets level', () => {
    errorStore.clear()
    const id = errorStore.info('info msg')
    const err = errorStore.activeErrors.find(e => e.id === id)
    expect(err?.level).toBe('info')
    errorStore.dismiss(id)
  })

  test('dismiss removes from active', () => {
    errorStore.clear()
    const id = errorStore.error('to dismiss')
    expect(errorStore.activeErrors.some(e => e.id === id)).toBe(true)
    errorStore.dismiss(id)
    expect(errorStore.activeErrors.some(e => e.id === id)).toBe(false)
  })

  test('clear removes all', () => {
    errorStore.clear()
    const id1 = errorStore.error('a')
    const id2 = errorStore.error('b')
    expect(errorStore.activeErrors.some(e => e.id === id1)).toBe(true)
    expect(errorStore.activeErrors.some(e => e.id === id2)).toBe(true)
    errorStore.clear()
    expect(errorStore.activeErrors.some(e => e.id === id1)).toBe(false)
    expect(errorStore.activeErrors.some(e => e.id === id2)).toBe(false)
  })

  test('subscribe receives push events', () => {
    errorStore.clear()
    const fn = vi.fn()
    const unsub = errorStore.subscribe(fn)
    errorStore.error('sub test')
    expect(fn).toHaveBeenCalledWith(expect.objectContaining({ type: 'push' }))
    unsub()
  })

  test('subscribe receives dismiss events', () => {
    errorStore.clear()
    const fn = vi.fn()
    const unsub = errorStore.subscribe(fn)
    const id = errorStore.error('dismiss test')
    fn.mockClear()
    errorStore.dismiss(id)
    expect(fn).toHaveBeenCalledWith({ type: 'dismiss', id })
    unsub()
  })

  test('subscribe receives clear events', () => {
    errorStore.clear()
    const fn = vi.fn()
    const unsub = errorStore.subscribe(fn)
    errorStore.clear()
    expect(fn).toHaveBeenCalledWith({ type: 'clear' })
    unsub()
  })

  test('unsubscribe stops events', () => {
    errorStore.clear()
    const fn = vi.fn()
    const unsub = errorStore.subscribe(fn)
    unsub()
    errorStore.error('after unsub')
    expect(fn).not.toHaveBeenCalled()
  })

  test('each push gets unique id', () => {
    errorStore.clear()
    const id1 = errorStore.error('a')
    const id2 = errorStore.error('b')
    expect(id1).not.toBe(id2)
    errorStore.dismiss(id1)
    errorStore.dismiss(id2)
  })

  test('activeErrors returns independent copies', () => {
    errorStore.clear()
    const id = errorStore.error('test')
    const first = errorStore.activeErrors
    const second = errorStore.activeErrors
    expect(first).not.toBe(second)
    expect(first).toEqual(second)
    errorStore.dismiss(id)
  })
})
