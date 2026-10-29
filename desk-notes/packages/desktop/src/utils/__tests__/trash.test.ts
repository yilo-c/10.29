import { describe, it, expect, vi, beforeEach } from 'vitest'
import type { TrashItem } from '../trash'

// Mock fileStore
vi.mock('../fileStore', () => ({
  readTrashFile: vi.fn(),
  writeTrashFile: vi.fn(() => Promise.resolve()),
}))

describe('trash', () => {
  let Trash: typeof import('../trash')

  beforeEach(async () => {
    vi.clearAllMocks()
    localStorage.clear()
    // Re-import to get fresh module state
    Trash = await import('../trash')
  })

  it('starts with empty trash', () => {
    expect(Trash.getTrashItems()).toEqual([])
  })

  it('stores and retrieves an item', () => {
    const item: TrashItem = {
      id: 'item-1',
      type: 'todo',
      data: { text: 'test todo' },
      deletedAt: Date.now(),
    }

    Trash.moveToTrash(item)
    const items = Trash.getTrashItems()
    expect(items).toHaveLength(1)
    expect(items[0].id).toBe('item-1')
    expect(items[0].data).toEqual({ text: 'test todo' })
  })

  it('replaces item with same id on moveToTrash', () => {
    const item1: TrashItem = { id: 'dup', type: 'todo', data: { v: 1 }, deletedAt: 100 }
    const item2: TrashItem = { id: 'dup', type: 'todo', data: { v: 2 }, deletedAt: 200 }

    Trash.moveToTrash(item1)
    Trash.moveToTrash(item2)
    const items = Trash.getTrashItems()
    expect(items).toHaveLength(1)
    expect(items[0].data).toEqual({ v: 2 })
  })

  it('restores an item from trash', () => {
    const item: TrashItem = { id: 'r1', type: 'note', data: { title: 'My Note' }, deletedAt: Date.now() }
    Trash.moveToTrash(item)

    const restored = Trash.restoreFromTrash('r1')
    expect(restored).not.toBeNull()
    expect(restored!.id).toBe('r1')
    expect(restored!.data).toEqual({ title: 'My Note' })

    // Item should be removed from trash
    expect(Trash.getTrashItems()).toHaveLength(0)
  })

  it('returns null when restoring non-existent item', () => {
    expect(Trash.restoreFromTrash('non-existent')).toBeNull()
  })

  it('permanently deletes an item', () => {
    Trash.moveToTrash({ id: 'del1', type: 'todo', data: {}, deletedAt: 1 })
    Trash.moveToTrash({ id: 'del2', type: 'note', data: {}, deletedAt: 2 })

    Trash.permanentlyDelete('del1')
    const items = Trash.getTrashItems()
    expect(items).toHaveLength(1)
    expect(items[0].id).toBe('del2')
  })

  it('clears all trash', () => {
    Trash.moveToTrash({ id: 'a', type: 'todo', data: {}, deletedAt: 1 })
    Trash.moveToTrash({ id: 'b', type: 'note', data: {}, deletedAt: 2 })

    Trash.clearTrash()
    expect(Trash.getTrashItems()).toHaveLength(0)
  })

  it('notifies subscribers on mutations', async () => {
    const fn = vi.fn()
    Trash.subscribe(fn)

    Trash.moveToTrash({ id: 's1', type: 'todo', data: {}, deletedAt: 1 })
    expect(fn).toHaveBeenCalledTimes(1)

    Trash.clearTrash()
    expect(fn).toHaveBeenCalledTimes(2)

    Trash.restoreFromTrash('s1')
    // s1 was cleared, restore returns null and does not mutate
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('subscribe returns unsubscribe function', () => {
    const fn = vi.fn()
    const unsub = Trash.subscribe(fn)

    Trash.moveToTrash({ id: 'u1', type: 'todo', data: {}, deletedAt: 1 })
    expect(fn).toHaveBeenCalledTimes(1)

    unsub()
    Trash.clearTrash()
    // Should not be called after unsubscribe (clearTrash on empty = no items, but it still calls saveTrash([]) and notify())
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('persists to localStorage across getTrashItems calls', () => {
    Trash.moveToTrash({ id: 'p1', type: 'note', data: { x: 1 }, deletedAt: 10 })

    // Re-import should read from localStorage
    const items = Trash.getTrashItems()
    expect(items).toHaveLength(1)
    expect(items[0].id).toBe('p1')
  })
})
