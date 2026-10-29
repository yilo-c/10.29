import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render } from '@testing-library/react'
import ChatModule from '../ChatModule'

// ── Hoisted mocks ──────────────────────────────────────────────────────────
// These must be defined with vi.hoisted() so they exist before
// the vi.mock() factories are evaluated (Vitest hoists those factories).
const { mockUseStore, mockGetWsClient, mockGetAIProvider } = vi.hoisted(() => ({
  mockUseStore: vi.fn(),
  mockGetWsClient: vi.fn(),
  mockGetAIProvider: vi.fn(),
}))

// ── Module-level mocks ────────────────────────────────────────────────────

// Fully mock desktop store (prevents module-level subscribe from running)
// The relative path must match the import path in ChatModule.tsx exactly,
// since vitest resolves vi.mock() relative to the test file's location.
vi.mock('../../../store/useStore', () => ({
  useStore: mockUseStore,
}))

vi.mock('../../../utils/ai', () => ({
  getWsClient: mockGetWsClient,
  getAIProvider: mockGetAIProvider,
}))

vi.mock('../../../utils/chatSync', () => ({
  loadBackendConversations: vi.fn().mockResolvedValue([]),
}))

vi.mock('../../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
  // ChatModule 内部 formatTime 等直接调 getTranslations()，需补齐 chatModule 文案键
  getTranslations: () => ({
    chatModule: new Proxy({
      timeTodayFmt: '今天 {time}',
      timeYesterdayFmt: '昨天 {time}',
      newConversation: '新对话',
    } as Record<string, string>, {
      get: (target, prop) => target[prop as string] ?? String(prop),
    }),
  }),
}))

// Mock @desk-notes/shared — needs useStore with subscribe/getState/setState
// for the real desktop store module's module-level side effects.
vi.mock('@desk-notes/shared', () => {
  const storeFn: any = (sel: any) => sel({
    aiEnabled: true,
    conversations: [],
    activeConversationId: null,
    floatingNotes: [],
  })
  storeFn.subscribe = vi.fn()
  storeFn.getState = vi.fn(() => ({ conversations: [], floatingNotes: [], aiEnabled: true }))
  storeFn.setState = vi.fn()
  return {
    useStore: storeFn,
    setTrashHandler: vi.fn(),
    // B3：统一事件分发注册表 + 后端事件 store（A4 SandboxTimeoutHints 依赖）
    onBackendEvent: () => () => {},
    useBackendEventsStore: ((sel: any) => sel({ sandboxTimeouts: [], dismissSandboxTimeout: vi.fn() })) as any,
    WsAIProvider: class {
      processText = vi.fn().mockResolvedValue('')
      getConfig = () => ({})
      updateConfig = vi.fn()
      preferredProvider = undefined
      preferredModel = undefined
    },
    getWsClient: () => ({ status: 'disconnected', onStatusChange: () => vi.fn(), call: vi.fn(), connect: vi.fn(), on: () => vi.fn(), stream: vi.fn() }),
    resetWsClient: vi.fn(),
    getAIProvider: vi.fn(),
  }
})

// Sub-component stubs
vi.mock('../../Todo/MorningBrief', () => ({
  default: () => <div data-testid="morning-brief">MorningBrief</div>,
}))

vi.mock('../ModelSelector', () => ({
  ModelSelector: () => null,
}))

vi.mock('../RelatedMemories', () => ({
  default: () => null,
}))

vi.mock('../ChainProgress', () => ({
  default: () => null,
}))

// ── Tests ──────────────────────────────────────────────────────────────────

describe('ChatModule', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    // Build a state object that matches the Zustand store shape consumed
    // by ChatModule. The mock for useStore(selector) calls the selector
    // with this state, so each store-accessor call gets the right value.
    const defaultState = {
      aiEnabled: true,
      conversations: [],
      activeConversationId: null,
      createConversation: vi.fn().mockReturnValue('test-conv-id'),
      deleteConversation: vi.fn(),
      setActiveConversation: vi.fn(),
      addMessage: vi.fn(),
      clearConversation: vi.fn(),
      setConversationTitle: vi.fn(),
    }

    // useStore(selector) => selector(state)
    mockUseStore.mockImplementation(
      (selector: (s: typeof defaultState) => unknown) => selector(defaultState),
    )
    // Zustand stores also expose static .setState() and .getState()
    // The component calls both (e.g. setState for batch updates,
    // getState for reading live state inside async handlers).
    const storeMock = mockUseStore as unknown as ReturnType<typeof vi.fn> & { setState: ReturnType<typeof vi.fn>; getState: ReturnType<typeof vi.fn> }
    storeMock.setState = vi.fn()
    storeMock.getState = vi.fn(() => ({
      ...defaultState,
    }))

    // WS client: start disconnected so the "sync from backend" effect
    // (which expects backendConnected = true) does not fire.
    mockGetWsClient.mockReturnValue({
      status: 'disconnected',
      onStatusChange: vi.fn().mockReturnValue(vi.fn()),
      call: vi.fn(),
      on: vi.fn().mockReturnValue(vi.fn()),
      stream: vi.fn(),
    })

    mockGetAIProvider.mockReturnValue({
      processText: vi.fn().mockResolvedValue(''),
    })
  })

  it('renders without crashing', () => {
    const { container } = render(<ChatModule />)
    expect(container).toBeTruthy()
  })
})
