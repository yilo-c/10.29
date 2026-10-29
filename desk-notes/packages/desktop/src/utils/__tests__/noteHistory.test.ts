import { describe, test, expect, beforeEach } from 'vitest'
import {
  addSnapshot,
  addAutoSnapshot,
  getHistory,
  computeDiff,
  VersionEntry,
} from '../noteHistory'

let noteCounter = 0
function uniqueNoteId(): string {
  return `note-${++noteCounter}-${Date.now()}`
}

describe('noteHistory', () => {
  beforeEach(() => {
    localStorage.removeItem('desk-notes-history')
  })

  test('getHistory returns empty array for unknown note', () => {
    expect(getHistory('nonexistent')).toEqual([])
  })

  test('addSnapshot stores a version entry', () => {
    const nid = uniqueNoteId()
    const entry = addSnapshot(nid, 'Test', 'hello world')
    expect(entry.id).toBeTruthy()
    expect(entry.noteId).toBe(nid)
    expect(entry.title).toBe('Test')
    expect(entry.content).toBe('hello world')
    expect(entry.timestamp).toBeGreaterThan(0)
  })

  test('getHistory returns entries for a note', () => {
    const nid = uniqueNoteId()
    addSnapshot(nid, 'A', 'content a')
    addSnapshot(nid, 'B', 'content b')
    const history = getHistory(nid)
    expect(history.length).toBe(2)
    expect(history[0].title).toBe('A')
    expect(history[1].title).toBe('B')
  })

  test('getHistory returns a copy (not mutable)', () => {
    const nid = uniqueNoteId()
    addSnapshot(nid, 'T', 'c')
    const first = getHistory(nid)
    const second = getHistory(nid)
    expect(first).toEqual(second)
    first.push({} as VersionEntry)
    expect(getHistory(nid).length).toBe(1)
  })

  test('addAutoSnapshot returns null within interval', () => {
    const nid = uniqueNoteId()
    addSnapshot(nid, 'Initial', 'v1')
    const result = addAutoSnapshot(nid, 'Auto', 'v1 edited')
    expect(result).toBeNull()
  })

  test('addSnapshot enforces MAX_VERSIONS_PER_NOTE', () => {
    const nid = uniqueNoteId()
    for (let i = 0; i < 35; i++) {
      addSnapshot(nid, `v${i}`, `content ${i}`)
    }
    const history = getHistory(nid)
    expect(history.length).toBeLessThanOrEqual(31)
  })

  test('addSnapshot saves todos if provided', () => {
    const nid = uniqueNoteId()
    const todos = [
      { id: 't1', text: 'Task 1', done: true },
      { id: 't2', text: 'Task 2', done: false },
    ]
    const entry = addSnapshot(nid, 'Todos', 'content', todos)
    expect(entry.todos).toEqual(todos)
  })

  test('persists and reloads from localStorage', () => {
    const nid = uniqueNoteId()
    addSnapshot(nid, 'Persist', 'test content')
    expect(getHistory(nid).length).toBe(1)
    const raw = localStorage.getItem('desk-notes-history')
    expect(raw).toBeTruthy()
    expect(raw).toContain('Persist')
  })
})

describe('computeDiff', () => {
  test('empty texts return empty diff', () => {
    expect(computeDiff('', '')).toEqual([])
  })

  test('identical texts return all "same"', () => {
    const result = computeDiff('line1\nline2', 'line1\nline2')
    expect(result.every(l => l.type === 'same')).toBe(true)
    expect(result.length).toBe(2)
  })

  test('detects added lines', () => {
    const result = computeDiff('line1', 'line1\nline2')
    expect(result.some(l => l.type === 'added' && l.text === 'line2')).toBe(true)
  })

  test('detects removed lines', () => {
    const result = computeDiff('line1\nline2', 'line1')
    expect(result.some(l => l.type === 'removed' && l.text === 'line2')).toBe(true)
  })

  test('handles mixed add/remove', () => {
    const result = computeDiff('a\nb\nc', 'a\nd\nc')
    const added = result.filter(l => l.type === 'added')
    const removed = result.filter(l => l.type === 'removed')
    expect(added.length).toBe(1)
    expect(added[0].text).toBe('d')
    expect(removed.length).toBe(1)
    expect(removed[0].text).toBe('b')
  })

  test('produces diff in correct order', () => {
    const result = computeDiff('first\nsecond', 'first\nmodified\nsecond')
    expect(result[0].type).toBe('same')
    expect(result[0].text).toBe('first')
    expect(result[1].type).toBe('added')
    expect(result[1].text).toBe('modified')
    expect(result[2].type).toBe('same')
    expect(result[2].text).toBe('second')
  })
})
