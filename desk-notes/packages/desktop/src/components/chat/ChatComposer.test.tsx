import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ChatComposer, COMPOSER_KERNEL_OPTIONS } from './ChatComposer'
import {
  CHAT_KERNEL_SELECTOR_OPTIONS,
  KERNEL_AUTO,
  KERNEL_CLAUDE_CODE,
  KERNEL_CODEX,
  KERNEL_DSH,
  buildKernelSendParam,
} from '@desk-notes/shared/utils/kernelVocabulary'

// ── Hoisted mocks ──────────────────────────────────────────────────────────
const { mockGetWsClient, mockSwallowError, mockOn, capturedHandlers } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
  mockSwallowError: vi.fn(),
  // 记录 useBackendEvent 注册的 handler，供测试手动触发 server-push 事件
  capturedHandlers: {} as Record<string, (e: { event_type: string; payload: Record<string, unknown> }) => void>,
  mockOn: vi.fn((type: string, handler: (e: { event_type: string; payload: Record<string, unknown> }) => void) => {
    capturedHandlers[type] = handler
    return () => {
      delete capturedHandlers[type]
    }
  }),
}))

vi.mock('../../utils/ai', () => ({ getWsClient: mockGetWsClient }))

vi.mock('../../utils/swallowError', () => ({ swallowError: mockSwallowError }))

vi.mock('../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const baseProps = {
  onSend: vi.fn(),
  onStop: vi.fn(),
  streaming: false,
  models: [] as never[],
  selection: {} as { provider?: string | null; model?: string | null },
  onModelChange: vi.fn(),
  searchMode: false,
  onToggleSearch: vi.fn(),
  multiAgentMode: false,
  onToggleMultiAgent: vi.fn(),
}

// 所有 describe 共享：模拟 WS 流式返回，供润色 / 附件测试使用
const mockStreamTyped = vi.fn()

describe('ChatComposer polish', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({ streamTyped: mockStreamTyped, status: 'connected', on: mockOn })
  })

  it('polish button is disabled when input is empty', () => {
    render(<ChatComposer {...baseProps} />)
    const btn = screen.getByTitle('chat.polishButton').closest('button')
    expect(btn?.hasAttribute('disabled')).toBe(true)
  })

  it('polish streams result back into the textarea, replacing input', async () => {
    // 模拟流式 chunk：先给一段，再给一段
    async function* chunks() {
      yield { type: 'chunk', data: '专业' }
      yield { type: 'chunk', data: '表达' }
      yield { type: 'done', data: '' }
    }
    mockStreamTyped.mockReturnValue(chunks())

    render(<ChatComposer {...baseProps} history={[{ role: 'user', content: '上文' }]} />)
    const textarea = screen.getByLabelText('聊天输入框')
    fireEvent.change(textarea, { target: { value: '普通的话' } })
    fireEvent.click(screen.getByTitle('chat.polishButton'))

    await waitFor(() => {
      // 断言调用参数：消息含润色指令+原文，context_messages 含上文
      expect(mockStreamTyped).toHaveBeenCalledWith('chat.send', expect.objectContaining({
        message: expect.stringContaining('普通的话'),
        context_messages: expect.arrayContaining([{ role: 'user', content: '上文' }]),
        stream: true,
      }))
    })
    await waitFor(() => {
      expect((textarea as HTMLTextAreaElement).value).toBe('专业表达')
    })
  })

  it('keeps original text when polish stream fails before any chunk', async () => {
    async function* failing() {
      yield* []
      throw new Error('backend down')
    }
    mockStreamTyped.mockReturnValue(failing())

    render(<ChatComposer {...baseProps} />)
    const textarea = screen.getByLabelText('聊天输入框')
    fireEvent.change(textarea, { target: { value: '原始想法' } })
    fireEvent.click(screen.getByTitle('chat.polishButton'))

    await waitFor(() => {
      expect((textarea as HTMLTextAreaElement).value).toBe('原始想法')
    })
  })

  it('polish button shows polishing state while streaming', async () => {
    let release: () => void
    const gate = new Promise<void>((resolve) => { release = resolve })
    async function* chunks() {
      yield { type: 'chunk', data: '结果' }
      await gate
      yield { type: 'done', data: '' }
    }
    mockStreamTyped.mockReturnValue(chunks())

    render(<ChatComposer {...baseProps} />)
    fireEvent.change(screen.getByLabelText('聊天输入框'), { target: { value: '要润色的' } })
    fireEvent.click(screen.getByTitle('chat.polishButton'))

    await waitFor(() => {
      expect(screen.getByText('chat.polishing')).toBeTruthy()
    })
    release!()
  })
})

describe('ChatComposer kernel selector', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders kernel selector with default auto label', () => {
    render(<ChatComposer {...baseProps} />)
    expect(screen.getByText('自动内核')).toBeTruthy()
  })

  it('shows selected kernel label and fires onKernelChange on pick', () => {
    const onKernelChange = vi.fn()
    render(<ChatComposer {...baseProps} kernel="dsh" onKernelChange={onKernelChange} />)
    expect(screen.getByText('DSH 执行内核')).toBeTruthy()

    fireEvent.click(screen.getByText('DSH 执行内核'))
    fireEvent.click(screen.getByText('Claude Code'))
    expect(onKernelChange).toHaveBeenCalledWith('claude_code')
  })

  it('applies brand-soft style when a non-auto kernel is active', () => {
    const { container } = render(<ChatComposer {...baseProps} kernel="codex" onKernelChange={vi.fn()} />)
    const btn = screen.getByTitle('chat.kernelSelector').closest('button')
    expect(btn?.className).toContain('bg-brand-soft')
    expect(container).toBeTruthy()
  })

  it('选项集即共享词汇模块（A1② 单一来源，未复制列表）', () => {
    expect(COMPOSER_KERNEL_OPTIONS).toBe(CHAT_KERNEL_SELECTOR_OPTIONS)
  })

  it('每个选项渲染出来且回传真实内核名', () => {
    const onKernelChange = vi.fn()
    render(<ChatComposer {...baseProps} kernel={KERNEL_AUTO} onKernelChange={onKernelChange} />)
    const trigger = screen.getByTitle('chat.kernelSelector')
    fireEvent.click(trigger)
    for (const option of CHAT_KERNEL_SELECTOR_OPTIONS) {
      // 触发按钮与下拉项可能同名单，末位为下拉项
      const item = screen.getAllByText(option.label).slice(-1)[0]
      fireEvent.click(item)
      expect(onKernelChange).toHaveBeenLastCalledWith(option.value)
      fireEvent.click(screen.getByTitle('chat.kernelSelector'))
    }
    expect(onKernelChange).toHaveBeenCalledTimes(CHAT_KERNEL_SELECTOR_OPTIONS.length)
  })

  it('选择器所选值经 buildKernelSendParam 即后端实际收到的 kernel（CT-13）', () => {
    const params: Record<string, unknown> = {}
    for (const option of CHAT_KERNEL_SELECTOR_OPTIONS) {
      params[option.value] = buildKernelSendParam(option.value)
    }
    expect(params[KERNEL_AUTO]).toEqual({})
    expect(params[KERNEL_DSH]).toEqual({ kernel: 'dsh' })
    expect(params[KERNEL_CLAUDE_CODE]).toEqual({ kernel: 'claude_code' })
    expect(params[KERNEL_CODEX]).toEqual({ kernel: 'codex' })
  })
})

describe('ChatComposer attachments (Task #9)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({ streamTyped: mockStreamTyped, status: 'connected', on: mockOn })
  })

  it('添加文本附件后 chip 出现，发送时 onSend 带 wire attachments', async () => {
    const onSend = vi.fn()
    render(<ChatComposer {...baseProps} onSend={onSend} />)
    const file = new File(['你好世界'], 'note.txt', { type: 'text/plain' })
    const input = screen.getByTestId('attachment-file-input')
    fireEvent.change(input, { target: { files: [file] } })

    await waitFor(() => {
      expect(screen.getByTestId('attachment-strip')).toBeTruthy()
    })
    expect(screen.getByText('note.txt')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('聊天输入框'), { target: { value: '总结这个' } })
    fireEvent.click(screen.getByLabelText('发送消息'))

    await waitFor(() => {
      expect(onSend).toHaveBeenCalledTimes(1)
    })
    const [text, wire] = onSend.mock.calls[0]
    expect(text).toBe('总结这个')
    expect(Array.isArray(wire)).toBe(true)
    expect(wire[0].name).toBe('note.txt')
    expect(wire[0].data_base64).toBeTruthy()
  })

  it('发送后附件清空', async () => {
    const onSend = vi.fn()
    render(<ChatComposer {...baseProps} onSend={onSend} />)
    const file = new File(['x'], 'a.md', { type: 'text/markdown' })
    fireEvent.change(screen.getByTestId('attachment-file-input'), { target: { files: [file] } })
    await waitFor(() => expect(screen.getByText('a.md')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('聊天输入框'), { target: { value: 'go' } })
    fireEvent.click(screen.getByLabelText('发送消息'))
    await waitFor(() => expect(screen.queryByTestId('attachment-strip')).toBeNull())
  })

  it('不支持的类型给出可见拒绝原因不静默丢', async () => {
    render(<ChatComposer {...baseProps} />)
    const file = new File(['%PDF-1.4'], 'doc.pdf', { type: 'application/pdf' })
    fireEvent.change(screen.getByTestId('attachment-file-input'), { target: { files: [file] } })
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('暂只支持')
    })
  })

  it('chip 移除按钮删除对应附件', async () => {
    render(<ChatComposer {...baseProps} />)
    const file = new File(['y'], 'keep.txt', { type: 'text/plain' })
    fireEvent.change(screen.getByTestId('attachment-file-input'), { target: { files: [file] } })
    await waitFor(() => expect(screen.getByText('keep.txt')).toBeTruthy())
    fireEvent.click(screen.getByLabelText('移除附件 keep.txt'))
    await waitFor(() => expect(screen.queryByText('keep.txt')).toBeNull())
  })
})

describe('ChatComposer 剪贴板自动注入 (T2 #4)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({ streamTyped: mockStreamTyped, status: 'connected', on: mockOn })
  })

  it('收到 clipboard.changed 推送后弹出"附为上下文"提示，点击后作为文本附件追加', async () => {
    render(<ChatComposer {...baseProps} />)
    // 模拟后端 EventBridge 推送 clipboard.changed
    capturedHandlers['clipboard.changed']?.({
      event_type: 'clipboard.changed',
      payload: { text: '刚从网页复制的一段很有用的参考文字' },
    })

    await waitFor(() => expect(screen.getByTestId('clipboard-hint')).toBeTruthy())
    expect(screen.getByText('刚从网页复制的一段很有用的参考文字'.slice(0, 60))).toBeTruthy()

    fireEvent.click(screen.getByText('附为上下文'))

    // 文本被构造为文本附件（默认名 剪贴板文本.txt）
    await waitFor(() => expect(screen.getByText('剪贴板文本.txt')).toBeTruthy())
    // 提示消失
    expect(screen.queryByTestId('clipboard-hint')).toBeNull()
  })

  it('忽略按钮可关闭提示且不再出现', async () => {
    render(<ChatComposer {...baseProps} />)
    capturedHandlers['clipboard.changed']?.({
      event_type: 'clipboard.changed',
      payload: { text: '一段待忽略的复制文本' },
    })
    await waitFor(() => expect(screen.getByTestId('clipboard-hint')).toBeTruthy())
    fireEvent.click(screen.getByLabelText('忽略剪贴板提示'))
    await waitFor(() => expect(screen.queryByTestId('clipboard-hint')).toBeNull())
  })

  it('过短的复制文本不弹出提示', async () => {
    render(<ChatComposer {...baseProps} />)
    capturedHandlers['clipboard.changed']?.({
      event_type: 'clipboard.changed',
      payload: { text: 'x' },
    })
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.queryByTestId('clipboard-hint')).toBeNull()
  })
})
