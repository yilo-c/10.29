import { describe, it, expect, beforeEach } from 'vitest'
import {
  getInboxItems,
  addInboxItem,
  markInboxProcessed,
  removeInboxItem,
  unprocessedCount,
} from '../inboxManager'

function mockLocalStorage() {
  let store: Record<string, string> = {}
  Object.defineProperty(globalThis, 'localStorage', {
    value: {
      getItem: (key: string) => store[key] ?? null,
      setItem: (key: string, value: string) => { store[key] = value },
      removeItem: (key: string) => { delete store[key] },
      clear: () => { store = {} },
      get length() { return Object.keys(store).length },
      key: (i: number) => Object.keys(store)[i] ?? null,
    },
    configurable: true,
  })
}

describe('inboxManager', () => {
  beforeEach(() => {
    mockLocalStorage()
  })

  it('returns empty array when no items exist', () => {
    expect(getInboxItems()).toEqual([])
  })

  it('adds an inbox item', () => {
    addInboxItem({ text: 'Test note', source: 'quick_capture', type: 'note' })
    const items = getInboxItems()
    expect(items).toHaveLength(1)
    expect(items[0].text).toBe('Test note')
    expect(items[0].source).toBe('quick_capture')
    expect(items[0].type).toBe('note')
    expect(items[0].processed).toBe(false)
    expect(items[0].id).toBeTruthy()
    expect(items[0].createdAt).toBeTruthy()
  })

  it('adds items in reverse chronological order', () => {
    addInboxItem({ text: 'First', source: 'quick_capture', type: 'note' })
    addInboxItem({ text: 'Second', source: 'share', type: 'task' })
    const items = getInboxItems()
    expect(items[0].text).toBe('Second')
    expect(items[1].text).toBe('First')
  })

  it('marks item as processed', () => {
    addInboxItem({ text: 'To process', source: 'quick_capture', type: 'note' })
    const item = getInboxItems()[0]
    expect(item.processed).toBe(false)
    markInboxProcessed(item.id)
    expect(getInboxItems()[0].processed).toBe(true)
  })

  it('removes an item by id', () => {
    addInboxItem({ text: 'Keep', source: 'quick_capture', type: 'note' })
    addInboxItem({ text: 'Remove', source: 'share', type: 'task' })
    const toRemove = getInboxItems().find(i => i.text === 'Remove')!
    removeInboxItem(toRemove.id)
    const remaining = getInboxItems()
    expect(remaining).toHaveLength(1)
    expect(remaining[0].text).toBe('Keep')
  })

  it('counts unprocessed items', () => {
    expect(unprocessedCount()).toBe(0)
    addInboxItem({ text: 'Item 1', source: 'quick_capture', type: 'note' })
    addInboxItem({ text: 'Item 2', source: 'sync', type: 'task' })
    expect(unprocessedCount()).toBe(2)
    markInboxProcessed(getInboxItems()[0].id)
    expect(unprocessedCount()).toBe(1)
  })

  it('handles corrupt localStorage data gracefully', () => {
    localStorage.setItem('siyi_inbox_items', 'invalid-json')
    expect(getInboxItems()).toEqual([])
  })
})
