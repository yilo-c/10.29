import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import TranslationModule from '../TranslationModule'
import { addTranslationHistory, clearTranslationHistory } from '../../../utils/translationHistory'

// ── Hoisted mocks ──────────────────────────────────────────────────────────
const { mockGetWsClient, mockErrorStore } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
  mockErrorStore: { success: vi.fn(), warn: vi.fn(), error: vi.fn(), info: vi.fn() },
}))

vi.mock('@desk-notes/shared', () => ({
  useStore: Object.assign(vi.fn(), { getState: () => ({ activeProfileId: null, aiProfiles: [] }) }),
  getWsClient: mockGetWsClient,
}))

vi.mock('../../../utils/ai', () => ({
  getAIProvider: vi.fn(),
  DEFAULT_LOCAL_MODEL: 'qwen3-4b',
  DEFAULT_LOCAL_API_URL: 'http://127.0.0.1:11434',
}))

vi.mock('../../../FloatingBubble/WordCards', () => ({ default: () => null }))

vi.mock('../../WordDetailPanel', () => ({ default: () => null }))

vi.mock('../../../components/Common/SpeakButton', () => ({ default: () => null }))

vi.mock('../../../components/Common/Icon', () => ({
  default: ({ icon }: { icon: string }) => <i data-testid="icon" data-icon={icon} />,
}))

vi.mock('../../../utils/translationCache', () => ({
  TranslationCache: class { get() { return null } set() { /* noop */ } },
}))

vi.mock('../../../store/errorStore', () => ({ errorStore: mockErrorStore }))

vi.mock('../../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

describe('TranslationModule 历史面板（C-14④-1 读回）', () => {
  const mockCall = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    mockCall.mockImplementation(async () => [])
    mockGetWsClient.mockReturnValue({ call: mockCall, status: 'connected' })
  })

  it('opens history panel and merges backend translations.list results', async () => {
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'translations.list') {
        return [{ source: 'en', target: 'zh', sourceText: 'backend only entry', translatedText: '后端条目', timestamp: 123 }]
      }
      return []
    })
    render(<TranslationModule />)

    fireEvent.click(screen.getByText('translation.historyTitle'))

    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('translations.list', { limit: 50 })
      expect(screen.getByText('backend only entry')).toBeTruthy()
    })
  })

  it('keeps local history visible when translations.list fails', async () => {
    addTranslationHistory('local entry', '本地条目', 'en2zh', 'quick')
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'translations.list') throw new Error('offline')
      return []
    })
    render(<TranslationModule />)

    fireEvent.click(screen.getByText('translation.historyTitle'))
    await waitFor(() => expect(screen.getByText('local entry')).toBeTruthy())
  })

  it('backend entries dedupe against local history', async () => {
    addTranslationHistory('dup entry', '本地优先', 'en2zh', 'deep')
    mockCall.mockImplementation(async (method: string) => {
      if (method === 'translations.list') {
        return [{ source: 'en', sourceText: 'dup entry', translatedText: '后端重复', timestamp: 5 }]
      }
      return []
    })
    render(<TranslationModule />)

    fireEvent.click(screen.getByText('translation.historyTitle'))
    await waitFor(() => expect(screen.getByText('dup entry')).toBeTruthy())
    // 译文行渲染为 "→ {result}"
    expect(screen.getByText(/本地优先/)).toBeTruthy()
    expect(screen.queryByText(/后端重复/)).toBeNull()
    clearTranslationHistory()
  })
})
