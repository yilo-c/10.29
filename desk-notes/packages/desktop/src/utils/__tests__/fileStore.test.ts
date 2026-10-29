import { describe, test, expect, beforeEach } from 'vitest'

// ── FileStore tests ──────────────────────────────────────────────────

describe('fileStorage (browser fallback)', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  test('getItem reads from localStorage', async () => {
    const mod = await import('../fileStore')
    localStorage.setItem('test-key', 'test-value')
    const val = await mod.fileStorage.getItem('test-key')
    expect(val).toBe('test-value')
  })

  test('setItem writes to localStorage', async () => {
    const mod = await import('../fileStore')
    await mod.fileStorage.setItem('set-key', 'set-value')
    expect(localStorage.getItem('set-key')).toBe('set-value')
  })

  test('removeItem deletes from localStorage', async () => {
    const mod = await import('../fileStore')
    localStorage.setItem('rm-key', 'rm-value')
    await mod.fileStorage.removeItem('rm-key')
    expect(localStorage.getItem('rm-key')).toBeNull()
  })

  test('getItem returns null for missing key', async () => {
    const mod = await import('../fileStore')
    const val = await mod.fileStorage.getItem('nonexistent')
    expect(val).toBeNull()
  })
})

describe('initFileStore', () => {
  test('returns migrated:false in browser mode', async () => {
    const mod = await import('../fileStore')
    const result = await mod.initFileStore()
    expect(result).toEqual({ migrated: false })
  })
})

describe('note helpers (browser fallback)', () => {
  test('saveNoteToFile is no-op in browser', async () => {
    const mod = await import('../fileStore')
    await expect(mod.saveNoteToFile({ id: 'test' })).resolves.toBeUndefined()
  })

  test('deleteNoteFile is no-op in browser', async () => {
    const mod = await import('../fileStore')
    await expect(mod.deleteNoteFile('test')).resolves.toBeUndefined()
  })

  test('listNoteIds returns empty array in browser', async () => {
    const mod = await import('../fileStore')
    const ids = await mod.listNoteIds()
    expect(ids).toEqual([])
  })

  test('loadAllNotes returns empty array in browser', async () => {
    const mod = await import('../fileStore')
    const notes = await mod.loadAllNotes()
    expect(notes).toEqual([])
  })
})

describe('trash helpers (browser fallback)', () => {
  test('readTrashFile returns null in browser', async () => {
    const mod = await import('../fileStore')
    expect(await mod.readTrashFile()).toBeNull()
  })

  test('writeTrashFile is no-op in browser', async () => {
    const mod = await import('../fileStore')
    await expect(mod.writeTrashFile('{}')).resolves.toBeUndefined()
  })
})
