import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { exportToMarkdown, exportToJSON, downloadAsFile } from '../export'
import type { TodoItem, FloatingNote, Category } from '../../types'

const mockCategories: Category[] = [
  { id: 'cat1', label: '重要', icon: '⭐', color: '#f87171' },
  { id: 'cat2', label: '个人', icon: '👤', color: '#60a5fa' },
]

const mockTodos: TodoItem[] = [
  { id: '1', text: '完成PPT', done: false, createdAt: 1000, category: 'cat1' },
  { id: '2', text: '写周报', done: true, createdAt: 2000, category: 'cat2' },
  { id: '3', text: '无分类任务', done: false, createdAt: 3000 },
]

const mockNotes: FloatingNote[] = [
  {
    id: 'n1', type: 'text', title: '会议记录',
    content: '<p>周一站会要点</p>', x: 0, y: 0, width: 200, height: 200, zIndex: 1,
    createdAt: 4000, updatedAt: 5000,
  },
  {
    id: 'n2', type: 'todo', title: '待办便签',
    todos: [
      { id: 't1', text: '任务一', done: true, createdAt: 6000 },
      { id: 't2', text: '任务二', done: false, createdAt: 7000 },
    ],
    content: '', x: 100, y: 100, width: 200, height: 200, zIndex: 2,
    createdAt: 8000, updatedAt: 9000,
  },
]

const data = { todos: mockTodos, categories: mockCategories, floatingNotes: mockNotes }

describe('exportToMarkdown', () => {
  it('starts with title and export time', () => {
    const result = exportToMarkdown(data)
    expect(result).toContain('# 思忆便签导出')
    expect(result).toContain('> 导出时间：')
  })

  it('lists active and done todos with category tags', () => {
    const result = exportToMarkdown(data)
    expect(result).toContain('- [ ] 完成PPT')
    expect(result).toContain('[重要]')
    expect(result).toContain('- [x] 写周报')
    expect(result).toContain('[个人]')
  })

  it('shows un-categorized todos without tag', () => {
    const result = exportToMarkdown(data)
    expect(result).toContain('- [ ] 无分类任务')
  })

  it('includes floating notes section', () => {
    const result = exportToMarkdown(data)
    expect(result).toContain('## 📌 浮动便签')
    expect(result).toContain('### 会议记录')
    expect(result).toContain('周一站会要点')
  })

  it('renders todo-type notes with checkboxes', () => {
    const result = exportToMarkdown(data)
    expect(result).toContain('### 待办便签')
    expect(result).toContain('- [x] 任务一')
    expect(result).toContain('- [ ] 任务二')
  })

  it('handles empty floating notes', () => {
    const empty = { todos: mockTodos, categories: mockCategories, floatingNotes: [] }
    const result = exportToMarkdown(empty)
    expect(result).toContain('*暂无浮动便签*')
  })

  it('handles empty todos', () => {
    const empty = { todos: [], categories: mockCategories, floatingNotes: mockNotes }
    const result = exportToMarkdown(empty)
    expect(result).not.toContain('### 未完成')
  })

  it('includes summary line with counts', () => {
    const result = exportToMarkdown(data)
    expect(result).toContain('待办：3')
    expect(result).toContain('便签：2')
  })

  it('renders priority emoji', () => {
    const withPriority: TodoItem[] = [
      { ...mockTodos[0], priority: 0 },
    ]
    const result = exportToMarkdown({ todos: withPriority, categories: mockCategories, floatingNotes: [] })
    expect(result).toContain('🔴')
  })

  it('renders due date when present', () => {
    const withDue: TodoItem[] = [
      { ...mockTodos[0], dueDate: 1700000000000 },
    ]
    const result = exportToMarkdown({ todos: withDue, categories: mockCategories, floatingNotes: [] })
    expect(result).toContain('📅')
  })

  it('renders subtasks with indentation', () => {
    const parent: TodoItem = { id: 'p1', text: 'Parent', done: false, createdAt: 1000 }
    const child: TodoItem = { id: 'c1', text: 'Child task', done: false, createdAt: 2000, parentId: 'p1' }
    const result = exportToMarkdown({ todos: [parent, child], categories: [], floatingNotes: [] })
    expect(result).toContain('- [ ] Parent')
    expect(result).toContain('  - [ ] Child task')
  })
})

describe('exportToJSON', () => {
  it('produces valid JSON string', () => {
    const result = exportToJSON(data)
    expect(() => JSON.parse(result)).not.toThrow()
  })

  it('includes todos, categories, floatingNotes', () => {
    const parsed = JSON.parse(exportToJSON(data))
    expect(parsed.todos).toHaveLength(3)
    expect(parsed.categories).toHaveLength(2)
    expect(parsed.floatingNotes).toHaveLength(2)
  })

  it('includes export metadata', () => {
    const parsed = JSON.parse(exportToJSON(data))
    expect(parsed.exportedAt).toBeDefined()
    expect(parsed.version).toBe(1)
  })

  it('normalizes floating notes with empty todos array', () => {
    const noteWithoutTodos: FloatingNote = {
      id: 'n3', type: 'text', title: '无待办',
      content: '', x: 0, y: 0, width: 100, height: 100, zIndex: 1,
    }
    const parsed = JSON.parse(exportToJSON({ todos: [], categories: [], floatingNotes: [noteWithoutTodos] }))
    expect(parsed.floatingNotes[0].todos).toEqual([])
  })
})

describe('downloadAsFile', () => {
  beforeEach(() => {
    delete (globalThis as unknown as { electronAPI?: unknown }).electronAPI
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('creates a download link in browser mode', () => {
    const createObjectURL = vi.fn(() => 'blob:mock')
    const revokeObjectURL = vi.fn()
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = revokeObjectURL

    const appendChild = vi.fn()
    const removeChild = vi.fn()
    const click = vi.fn()
    const anchor = document.createElement('a')
    vi.spyOn(document, 'createElement').mockReturnValue(anchor)
    vi.spyOn(document.body, 'appendChild').mockImplementation(appendChild)
    vi.spyOn(document.body, 'removeChild').mockImplementation(removeChild)
    vi.spyOn(anchor, 'click').mockImplementation(click)

    downloadAsFile('test content', 'test.md')

    expect(createObjectURL).toHaveBeenCalled()
    expect(anchor.download).toBe('test.md')
    expect(appendChild).toHaveBeenCalledWith(anchor)
    expect(click).toHaveBeenCalled()
    expect(removeChild).toHaveBeenCalledWith(anchor)
    expect(revokeObjectURL).toHaveBeenCalled()
  })

  it('calls electronAPI.showSaveDialog in Electron mode', () => {
    const showSaveDialog = vi.fn()
    ;(globalThis as unknown as { electronAPI?: { showSaveDialog: typeof showSaveDialog } }).electronAPI = { showSaveDialog }

    downloadAsFile('electron content', 'doc.md')

    expect(showSaveDialog).toHaveBeenCalledWith({
      filename: 'doc.md',
      content: 'electron content',
      mimeType: 'text/markdown;charset=utf-8',
    })
  })

  it('uses custom mime type when provided', () => {
    const createObjectURL = vi.fn(() => 'blob:mock')
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = vi.fn()

    const anchor = document.createElement('a')
    vi.spyOn(document, 'createElement').mockReturnValue(anchor)
    vi.spyOn(anchor, 'click').mockImplementation(() => {})
    vi.spyOn(document.body, 'appendChild').mockReturnValue(document.createElement('div'))
    vi.spyOn(document.body, 'removeChild').mockReturnValue(document.createElement('div'))

    downloadAsFile('{"a":1}', 'data.json', 'application/json;charset=utf-8')

    const calls = vi.mocked(createObjectURL).mock.calls as unknown[][]
    const blob = calls[0]?.[0] as unknown as Blob
    expect(blob.type).toBe('application/json;charset=utf-8')
  })
})
