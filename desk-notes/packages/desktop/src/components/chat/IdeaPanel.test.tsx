import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { IdeaPanel } from './IdeaPanel'

// ── Hoisted mocks ──────────────────────────────────────────────────────────
const { mockUseStore, mockGetWsClient, mockEnqueueSyncOp, mockSubscribeSyncQueue, mockErrorStore } = vi.hoisted(() => ({
  mockUseStore: vi.fn(),
  mockGetWsClient: vi.fn(),
  mockEnqueueSyncOp: vi.fn(() => true),
  mockSubscribeSyncQueue: vi.fn(() => vi.fn()),
  mockErrorStore: { success: vi.fn(), warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('../../store/useStore', () => ({ useStore: mockUseStore }))

vi.mock('../../utils/ai', () => ({ getWsClient: mockGetWsClient }))

vi.mock('../../store/errorStore', () => ({ errorStore: mockErrorStore }))

vi.mock('@desk-notes/shared', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  enqueueSyncOp: mockEnqueueSyncOp,
  subscribeSyncQueue: mockSubscribeSyncQueue,
}))

vi.mock('../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

describe('IdeaPanel', () => {
  const mockAddFloatingNote = vi.fn()
  const mockCall = vi.fn()
  const mockStreamTyped = vi.fn()

  /** 默认 ws.call 分发：inbox.list 返回服务端速记条目，inbox.add 返回新 id */
  const defaultCall = vi.fn(async (method: string) => {
    if (method === 'inbox.list') return []
    if (method === 'inbox.add') return { id: 'entry-1' }
    return null
  })

  beforeEach(() => {
    vi.clearAllMocks()
    mockUseStore.mockImplementation((sel: (s: unknown) => unknown) =>
      sel({ addFloatingNote: mockAddFloatingNote, nextZ: 42 }),
    )
    // IdeaPanel 直接调用 useStore.getState().nextZ，需挂 getState
    ;(mockUseStore as unknown as { getState: () => unknown }).getState = () => ({
      addFloatingNote: mockAddFloatingNote,
      nextZ: 42,
    })
    mockCall.mockImplementation(defaultCall)
    mockGetWsClient.mockReturnValue({ call: mockCall, streamTyped: mockStreamTyped, status: 'connected' })
  })

  it('renders expanded panel with input and save button', () => {
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    expect(screen.getByPlaceholderText('chat.ideaPanel.placeholder')).toBeTruthy()
    expect(screen.getByText('chat.ideaPanel.save')).toBeTruthy()
    expect(screen.getByText('chat.ideaPanel.popNote')).toBeTruthy()
  })

  it('collapsed panel shows only the toggle button', () => {
    render(<IdeaPanel collapsed={true} onToggleCollapse={vi.fn()} />)
    expect(screen.getByTitle('chat.ideaPanel.expand')).toBeTruthy()
    expect(screen.queryByPlaceholderText('chat.ideaPanel.placeholder')).toBeNull()
  })

  it('saves idea to backend inbox with tag=临时想法 and shows it in list', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.add') return { id: 'entry-1', content: '测试想法' }
      return []
    })
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('chat.ideaPanel.placeholder'), { target: { value: '测试想法' } })
    fireEvent.click(screen.getByText('chat.ideaPanel.save'))

    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('inbox.add', {
        content: '测试想法',
        source: 'idea_panel',
        tags: ['临时想法'],
      })
    })
    expect(screen.getByText('测试想法')).toBeTruthy()
  })

  it('pops current input as a floating note via store action', () => {
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText('chat.ideaPanel.placeholder'), { target: { value: '待弹出的想法' } })
    fireEvent.click(screen.getByText('chat.ideaPanel.popNote'))

    expect(mockAddFloatingNote).toHaveBeenCalledTimes(1)
    const note = mockAddFloatingNote.mock.calls[0][0]
    expect(note.content).toBe('待弹出的想法')
    expect(note.type).toBe('text')
    expect(note.tags).toEqual(['临时想法'])
    expect(note.zIndex).toBe(42)
  })

  it('save button is disabled when input is empty', () => {
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    const btn = screen.getByText('chat.ideaPanel.save').closest('button')
    expect(btn).toBeTruthy()
    expect(btn?.hasAttribute('disabled')).toBe(true)
  })

  // ── A1 数据闭环：回读服务端速记 ─────────────────────────────────────────
  it('loads persisted ideas from inbox on mount (source=idea_panel only)', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') {
        return [
          { id: 7, content: '昨天的想法', source: 'idea_panel', created_at: '2026-08-31T10:00:00Z' },
          { id: 8, content: '别的数据', source: 'rss_import' },
        ]
      }
      return null
    })
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('昨天的想法')).toBeTruthy())
    expect(screen.queryByText('别的数据')).toBeNull()
    // 服务端条目应带 entryId → 出现转笔记/转待办/删除操作
    expect(screen.getByText('chat.ideaPanel.toNote')).toBeTruthy()
  })

  // ── A2 不丢：保存失败入持久队列并乐观展示 ────────────────────────────────
  it('queues failed save into syncQueue and shows pending item', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return []
      throw new Error('offline')
    })
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('chat.ideaPanel.placeholder'), { target: { value: '断网想法' } })
    fireEvent.click(screen.getByText('chat.ideaPanel.save'))

    await waitFor(() => {
      expect(mockEnqueueSyncOp).toHaveBeenCalledWith('inbox.add', {
        content: '断网想法',
        source: 'idea_panel',
        tags: ['临时想法'],
      })
    })
    expect(screen.getByText('断网想法')).toBeTruthy()
    expect(screen.getByText('chat.ideaPanel.pending')).toBeTruthy()
  })

  // ── A3 条目操作 ─────────────────────────────────────────────────────────
  it('converts server item to note then archives it', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return [{ id: 7, content: '成笔记', source: 'idea_panel' }]
      return null
    })
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('成笔记')).toBeTruthy())

    fireEvent.click(screen.getByText('chat.ideaPanel.toNote'))
    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('notes.create', expect.objectContaining({ content: '成笔记' }))
      expect(mockCall).toHaveBeenCalledWith('inbox.update', { entry_id: 7, status: 'archived' })
    })
    expect(screen.queryByText('成笔记')).toBeNull()
  })

  it('deletes server item via inbox.update dismissed', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return [{ id: 9, content: '删掉我', source: 'idea_panel' }]
      return null
    })
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('删掉我')).toBeTruthy())

    fireEvent.click(screen.getByText('chat.ideaPanel.remove'))
    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('inbox.update', { entry_id: 9, status: 'dismissed' })
    })
    expect(screen.queryByText('删掉我')).toBeNull()
  })

  // ── C-14①-3 假删回归：entryId 保持后端原始 int 形状 ─────────────────────
  it('keeps raw numeric entryId (no String() coercion) so backend int match works', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return [{ id: 123, content: '原始 id', source: 'idea_panel' }]
      if (method === 'inbox.add') return { id: 456 }
      return null
    })
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('原始 id')).toBeTruthy())

    fireEvent.click(screen.getByText('chat.ideaPanel.remove'))
    await waitFor(() => {
      const updateCall = mockCall.mock.calls.find((c) => c[0] === 'inbox.update')
      expect(updateCall?.[1].entry_id).toBe(123)
      expect(typeof updateCall?.[1].entry_id).toBe('number')
    })
  })

  it('keeps numeric entryId from inbox.add result for newly saved items', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return []
      if (method === 'inbox.add') return { id: 777 }
      return null
    })
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('chat.ideaPanel.placeholder'), { target: { value: '新条目' } })
    fireEvent.click(screen.getByText('chat.ideaPanel.save'))
    await waitFor(() => expect(screen.getByText('新条目')).toBeTruthy())

    fireEvent.click(screen.getByText('chat.ideaPanel.remove'))
    await waitFor(() => {
      const updateCall = mockCall.mock.calls.find((c) => c[0] === 'inbox.update')
      expect(updateCall?.[1]).toEqual({ entry_id: 777, status: 'dismissed' })
    })
  })

  it('keeps item in list when inbox.update returns {error} (no optimistic fake-delete)', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return [{ id: 9, content: '删不掉', source: 'idea_panel' }]
      if (method === 'inbox.update') return { error: 'inbox repository unavailable' }
      return null
    })
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('删不掉')).toBeTruthy())

    fireEvent.click(screen.getByText('chat.ideaPanel.remove'))
    await waitFor(() => {
      expect(mockErrorStore.error).toHaveBeenCalled()
    })
    // 后端没删成 → 条目必须留在列表里（刷新不复活的前提是本地也不假装删掉）
    expect(screen.getByText('删不掉')).toBeTruthy()
  })

  // ── B1 AI 展开成笔记 ────────────────────────────────────────────────────
  it('AI-expands input into a structured note via chat.send stream', async () => {
    mockCall.mockImplementation(async (method: string) => (method === 'inbox.list' ? [] : null))
    async function* fakeStream() {
      yield { type: 'chunk', data: '# 标题\n' }
      yield { type: 'chunk', data: '## 背景\n内容' }
    }
    mockStreamTyped.mockReturnValue(fakeStream())
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)

    fireEvent.change(screen.getByPlaceholderText('chat.ideaPanel.placeholder'), { target: { value: '一个想法' } })
    fireEvent.click(screen.getByText('chat.ideaPanel.aiExpand'))

    await waitFor(() => {
      const noteCall = mockCall.mock.calls.find((c) => c[0] === 'notes.create')
      expect(noteCall).toBeTruthy()
      expect(noteCall?.[1].content).toContain('## 背景')
    })
    // 成功后清空输入框
    expect((screen.getByPlaceholderText('chat.ideaPanel.placeholder') as HTMLTextAreaElement).value).toBe('')
  })

  // ── B2 以此开聊 ─────────────────────────────────────────────────────────
  it('dispatches chat-draft event when starting chat from an idea', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'inbox.list') return [{ id: 7, content: '聊聊这个', source: 'idea_panel' }]
      return null
    })
    const listener = vi.fn()
    window.addEventListener('siyi:chat-draft', listener)
    render(<IdeaPanel collapsed={false} onToggleCollapse={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('聊聊这个')).toBeTruthy())

    // 输入区"以此开聊"在无输入时禁用，条目级按钮在最后 → 点条目那个
    const startBtns = screen.getAllByText('chat.ideaPanel.startChat')
    fireEvent.click(startBtns[startBtns.length - 1])
    expect(listener).toHaveBeenCalledTimes(1)
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toBe('聊聊这个')
    window.removeEventListener('siyi:chat-draft', listener)
  })
})
