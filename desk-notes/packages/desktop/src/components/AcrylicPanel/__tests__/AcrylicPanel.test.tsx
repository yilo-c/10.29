import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import AcrylicPanel from '../AcrylicPanel'

const mockStore: Record<string, unknown> = {
  panel: { x: 0, y: 0, width: 400, height: 600, locked: false, pinned: false, opacity: 1, color: null },
  setPanelPos: vi.fn(),
  togglePanelPin: vi.fn(),
  togglePanelLock: vi.fn(),
  setPanelOpacity: vi.fn(),
  setPanelColor: vi.fn(),
  theme: 'dark',
  toggleTheme: vi.fn(),
  viewMode: 'list',
  backgroundMode: 'acrylic',
  setBackgroundMode: vi.fn(),
  setLocale: vi.fn(),
  locale: 'zh-CN',
  appPin: false,
  appLocked: false,
  lockApp: vi.fn(),
  panelMode: 'todo',
  setPanelMode: vi.fn(),
  editingNoteId: null,
  todos: [],
  categories: [],
  activeCat: 'all',
  setActiveCat: vi.fn(),
  floatingNotes: [],
  updateFloatingNote: vi.fn(),
  searchQuery: '',
  removeFloatingNote: vi.fn(),
  noteHistory: [],
  errorLog: [],
  noteTemplates: [],
  plugins: [],
  aiEnabled: false,
  aiMessages: [],
  navOpen: false,
  setNavOpen: vi.fn(),
}

vi.mock('../../store/useStore', () => ({
  useStore: (selector?: (s: typeof mockStore) => unknown) =>
    selector ? selector(mockStore) : mockStore,
}))

vi.mock('../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

// Mock child components — paths must match AcrylicPanel.tsx's imports, resolved from test file
vi.mock('../../Todo/TodoList', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', { 'data-testid': 'todo-list' }, 'TodoList') }
})
vi.mock('../../Todo/MorningBrief', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', { 'data-testid': 'morning-brief' }, 'MorningBrief') }
})
vi.mock('../../Common/Toolbar', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', { 'data-testid': 'toolbar' }, 'Toolbar') }
})
vi.mock('../../Sidebar/CategoryPills', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', { 'data-testid': 'category-pills' }, 'CategoryPills') }
})
vi.mock('../../Sidebar/FolderTree', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', { 'data-testid': 'folder-tree' }, 'FolderTree') }
})
vi.mock('../../Common/NotesBrowser', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', { 'data-testid': 'notes-browser' }, 'NotesBrowser') }
})
vi.mock('../../Common/NoteEditor', async () => {
  const React = await import('react')
  return { default: () => React.createElement('div', { 'data-testid': 'note-editor' }, 'NoteEditor') }
})

describe('AcrylicPanel', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders toolbar', () => {
    render(<AcrylicPanel />)
    expect(screen.getByTestId('toolbar')).toBeTruthy()
  })

  it('renders todo list in todo mode', () => {
    render(<AcrylicPanel />)
    expect(screen.getByTestId('todo-list')).toBeTruthy()
  })

  it('renders morning brief in todo mode', () => {
    render(<AcrylicPanel />)
    expect(screen.getByTestId('morning-brief')).toBeTruthy()
  })

  it('renders category pills in todo mode', () => {
    render(<AcrylicPanel />)
    expect(screen.getByTestId('category-pills')).toBeTruthy()
  })

  it('renders lock button', () => {
    render(<AcrylicPanel />)
    expect(document.querySelector('[data-icon="fa-lock"]')).toBeTruthy()
  })

  it('renders pin button', () => {
    render(<AcrylicPanel />)
    expect(document.querySelector('[data-icon="fa-thumbtack"]')).toBeTruthy()
  })

  it('renders trash button', () => {
    render(<AcrylicPanel />)
    expect(document.querySelector('[data-icon="fa-trash-can"]')).toBeTruthy()
  })
})
