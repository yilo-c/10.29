import { describe, it, expect, vi, beforeEach } from 'vitest'
import { getContextMenuItems } from '../floatingNoteContextMenu'
import type { FloatingNote as FN } from '../../../types'

// ─── Mocks ──────────────────────────────────────────────────
vi.mock('../../../store/useStore', () => ({
  useStore: { getState: () => ({ floatingNotes: [] }), setState: vi.fn() },
}))
vi.mock('../../../store/undoManager', () => ({
  undoManager: { push: vi.fn() },
}))

// jsdom doesn't provide clipboard API
beforeEach(() => {
  Object.assign(navigator, {
    clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
  })
})

const makeDeps = (overrides: Record<string, unknown> = {}) => {
  const note = {
    id: 'n1', title: '测试', content: '内容', type: 'text',
    x: 100, y: 100, width: 300, height: 200, zIndex: 1, createdAt: 0,
    todos: [],
    ...overrides,
  } as FN
  return {
    ctxImage: null as string | null,
    t: (key: string) => key,
    note,
    editorRef: { current: document.createElement('div') },
    resizeTargetRefFN: { current: null },
    setResizingFN: vi.fn(),
    setResizeWidthFN: vi.fn(),
    updateFloatingNote: vi.fn(),
    removeFloatingNote: vi.fn(),
  }
}

describe('getContextMenuItems', () => {
  beforeEach(() => { vi.clearAllMocks() })

  describe('text note', () => {
    it('returns copy, archive, delete items', () => {
      const deps = makeDeps()
      const items = getContextMenuItems(deps)
      const labels = items.map(i => i.label)
      expect(labels).toContain('note.copyTitle')
      expect(labels).toContain('note.copyContent')
      expect(labels).toContain('note.archive')
      expect(labels).toContain('note.deleteTitle')
    })

    it('copyTitle writes to clipboard', () => {
      const deps = makeDeps({ title: '我的标题' })
      const items = getContextMenuItems(deps)
      const copyTitle = items.find(i => i.label === 'note.copyTitle')
      copyTitle!.onClick()
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('我的标题')
    })

    it('copyContent strips HTML for text notes', () => {
      const deps = makeDeps({ content: '<p>Hello <b>World</b></p>', type: 'text' })
      const items = getContextMenuItems(deps)
      const copyContent = items.find(i => i.label === 'note.copyContent')
      copyContent!.onClick()
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('Hello World')
    })

    it('delete is disabled when note is locked', () => {
      const deps = makeDeps({ locked: true })
      const items = getContextMenuItems(deps)
      const deleteItem = items.find(i => i.label === 'note.deleteTitle')
      expect(deleteItem?.disabled).toBe(true)
    })

    it('shows archive label when not archived', () => {
      const deps = makeDeps({ archived: false })
      const items = getContextMenuItems(deps)
      expect(items.find(i => i.label === 'note.archive')).toBeTruthy()
    })

    it('shows unarchive label when archived', () => {
      const deps = makeDeps({ archived: true })
      const items = getContextMenuItems(deps)
      expect(items.find(i => i.label === 'note.unarchive')).toBeTruthy()
    })

    it('toggle archive calls updateFloatingNote', () => {
      const deps = makeDeps({ archived: false })
      const items = getContextMenuItems(deps)
      const archiveItem = items.find(i => i.label === 'note.archive')
      archiveItem!.onClick()
      expect(deps.updateFloatingNote).toHaveBeenCalledWith('n1', { archived: true })
    })
  })

  describe('todo note', () => {
    it('copyContent serializes todos', () => {
      const deps = makeDeps({
        type: 'todo',
        content: '',
        todos: [
          { id: '1', text: '事项A', done: false, createdAt: 0 },
          { id: '2', text: '事项B', done: true, createdAt: 0 },
        ],
      })
      const items = getContextMenuItems(deps)
      const copyContent = items.find(i => i.label === 'note.copyContent')
      copyContent!.onClick()
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith('[ ] 事项A\n[x] 事项B')
    })
  })

  describe('image context menu', () => {
    it('returns image items when ctxImage is set', () => {
      const deps = makeDeps()
      deps.ctxImage = 'data:image/png,...'
      const items = getContextMenuItems(deps as Parameters<typeof getContextMenuItems>[0])
      const labels = items.map(i => i.label)
      expect(labels).toContain('note.resizeImage')
      expect(labels).toContain('note.alignLeft')
      expect(labels).toContain('note.deleteImage')
    })
  })
})
