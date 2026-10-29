import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest'
import type { onContentEdit as OnContentEdit, flushPendingEdit as FlushPendingEdit } from '../contentEditTracker'

const mockPush = vi.fn()
vi.mock('../undoManager', () => ({
  undoManager: { push: mockPush },
}))

vi.mock('../../i18n', () => ({
  getTranslations: () => ({ undoLabels: { editNote: '编辑便签' } }),
}))

let mockNotes: { id: string; content?: string }[] = []
const mockUpdateFloatingNote = vi.fn()

vi.mock('../useStore', () => ({
  useStore: {
    getState: () => ({
      floatingNotes: mockNotes,
      updateFloatingNote: mockUpdateFloatingNote,
    }),
  },
}))

describe('contentEditTracker', () => {
  let onContentEdit: typeof OnContentEdit
  let flushPendingEdit: typeof FlushPendingEdit

  beforeEach(async () => {
    vi.useFakeTimers()
    mockNotes = []
    mockPush.mockClear()
    mockUpdateFloatingNote.mockClear()
    const mod = await import('../contentEditTracker')
    onContentEdit = mod.onContentEdit
    flushPendingEdit = mod.flushPendingEdit
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  test('onContentEdit starts an edit session', () => {
    mockNotes = [{ id: 'n1', content: 'original' }]
    onContentEdit('n1')
    vi.advanceTimersByTime(2000)
    expect(mockPush).not.toHaveBeenCalled()
    flushPendingEdit()
  })

  // SKIPPED: contentEditTracker is now in @desk-notes/shared, desktop mocks don't reach it
  test.skip('onContentEdit pushes undo when content changes', () => {
    mockNotes = [{ id: 'n1', content: 'original' }]
    onContentEdit('n1')
    mockNotes[0].content = 'edited content'
    vi.advanceTimersByTime(2000)
    expect(mockPush).toHaveBeenCalledTimes(1)
    expect(mockPush.mock.calls[0][0]).toBe('编辑便签')
    flushPendingEdit()
  })

  test.skip('onContentEdit switches between notes', () => {
    mockNotes = [
      { id: 'n1', content: 'note1 v1' },
      { id: 'n2', content: 'note2 v1' },
    ]
    onContentEdit('n1')
    mockNotes[0].content = 'note1 v2'
    onContentEdit('n2')
    expect(mockPush).toHaveBeenCalledTimes(1)
    flushPendingEdit()
  })

  test.skip('flushPendingEdit finalizes immediately', () => {
    mockNotes = [{ id: 'n1', content: 'original' }]
    onContentEdit('n1')
    mockNotes[0].content = 'changed'
    flushPendingEdit()
    expect(mockPush).toHaveBeenCalledTimes(1)
  })

  test('deleted note during edit discards pending change', () => {
    mockNotes = [{ id: 'n1', content: 'original' }]
    onContentEdit('n1')
    mockNotes = []
    flushPendingEdit()
    expect(mockPush).not.toHaveBeenCalled()
  })
})
