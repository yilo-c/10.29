import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import FloatingNoteHeader from '../FloatingNoteHeader'
import type { FloatingNote as FN } from '../../../types'

// ─── Mocks ──────────────────────────────────────────────────
const mockStore: Record<string, unknown> = {
  updateFloatingNote: vi.fn(),
  theme: 'dark',
  toggleTheme: vi.fn(),
  backgroundMode: 'acrylic',
  setBackgroundMode: vi.fn(),
  locale: 'zh-CN',
  suggestedTags: {},
}

vi.mock('../../../store/useStore', () => ({
  useStore: (selector: (s: typeof mockStore) => unknown) => selector(mockStore),
}))

vi.mock('../../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('../../../utils/noteHistory', () => ({ addAutoSnapshot: vi.fn() }))

const makeNote = (overrides: Partial<FN> = {}): FN => ({
  id: 'n1', title: '测试标题', content: '<p>内容</p>', type: 'text',
  x: 100, y: 100, width: 300, height: 200, collapsed: false, locked: false,
  archived: false, pinned: false, opacity: 1, zIndex: 1, todos: [],
  createdAt: Date.now() - 3600000,
  ...overrides,
})

const makeProps = (overrides: Record<string, unknown> = {}) => {
  const note = makeNote(overrides.note as Partial<FN> | undefined)
  return {
    note,
    standalone: false,
    fullscreen: false,
    noteOpacity: 1,
    handleMouseDown: vi.fn(),
    handleFullscreenToggle: vi.fn(),
    handleTitleSave: vi.fn(),
    onRemove: vi.fn(),
    editorRef: { current: document.createElement('div') },
    timeoutIdsRef: { current: [] as ReturnType<typeof setTimeout>[] },
    ...overrides,
  }
}

describe('FloatingNoteHeader', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.updateFloatingNote = vi.fn()
    mockStore.toggleTheme = vi.fn()
  })

  it('renders title', () => {
    render(<FloatingNoteHeader {...makeProps()} />)
    expect(screen.getByText('测试标题')).toBeTruthy()
  })

  it('renders collapse button', () => {
    render(<FloatingNoteHeader {...makeProps()} />)
    // Chevron-down when expanded
    const btn = document.querySelector('[data-icon="fa-chevron-down"]')
    expect(btn).toBeTruthy()
  })

  it('shows chevron-right when collapsed', () => {
    render(<FloatingNoteHeader {...makeProps({ note: makeNote({ collapsed: true }) })} />)
    expect(document.querySelector('[data-icon="fa-chevron-right"]')).toBeTruthy()
  })

  it('toggle collapse calls updateFloatingNote', () => {
    render(<FloatingNoteHeader {...makeProps()} />)
    const collapseBtn = document.querySelector('[data-icon="fa-chevron-down"]')?.parentElement
    fireEvent.click(collapseBtn!)
    expect(mockStore.updateFloatingNote).toHaveBeenCalledWith('n1', { collapsed: true })
  })

  it('archive button calls updateFloatingNote', () => {
    render(<FloatingNoteHeader {...makeProps()} />)
    const archiveBtn = document.querySelector('[data-icon="fa-box-archive"]')?.parentElement
    fireEvent.click(archiveBtn!)
    expect(mockStore.updateFloatingNote).toHaveBeenCalledWith('n1', { archived: true })
  })

  it('shows archived banner when archived', () => {
    render(<FloatingNoteHeader {...makeProps({ note: makeNote({ archived: true }) })} />)
    expect(document.querySelector('[data-icon="fa-box-archive"]')).toBeTruthy()
    expect(screen.getByText('note.archivedBanner')).toBeTruthy()
  })

  it('lock button toggles locked state', () => {
    render(<FloatingNoteHeader {...makeProps()} />)
    const lockBtn = document.querySelector('[data-icon="fa-lock"]')?.parentElement
    fireEvent.click(lockBtn!)
    expect(mockStore.updateFloatingNote).toHaveBeenCalledWith('n1', { locked: true })
  })

  it('pin button calls updateFloatingNote with pinned true', () => {
    render(<FloatingNoteHeader {...makeProps()} />)
    const pinBtn = document.querySelector('[data-icon="fa-thumbtack"]')?.parentElement
    fireEvent.click(pinBtn!)
    expect(mockStore.updateFloatingNote).toHaveBeenCalledWith('n1', { pinned: true, zIndex: 999999 })
  })

  it('shows ref count badge when refCount > 0', () => {
    render(<FloatingNoteHeader {...makeProps({ note: makeNote({ refCount: 3 }) })} />)
    expect(screen.getByText('3')).toBeTruthy()
  })

  it('shows more menu on ellipsis click', () => {
    render(<FloatingNoteHeader {...makeProps()} />)
    const moreBtn = document.querySelector('[data-icon="fa-ellipsis-vertical"]')?.parentElement
    fireEvent.click(moreBtn!)
    expect(screen.getByText('note.copyRefLink')).toBeTruthy()
    expect(screen.getByText('note.manageTags')).toBeTruthy()
  })

  it('more menu has fullscreen and save version items', () => {
    render(<FloatingNoteHeader {...makeProps()} />)
    fireEvent.click(document.querySelector('[data-icon="fa-ellipsis-vertical"]')!.parentElement!)
    expect(screen.getByText('note.fullscreen')).toBeTruthy()
    expect(screen.getByText('noteHistory.saveVersion')).toBeTruthy()
  })
})
