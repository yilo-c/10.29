import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import FlashcardModule from './FlashcardModule'
import { useStore } from '../../store/useStore'

// Flashcard 的 getWsClient 来自 @desk-notes/shared（与多数模块不同），
// 用 importOriginal 仅替换 getWsClient，保留真实 useTranslation。
const h = vi.hoisted(() => ({ client: { status: 'connected', call: vi.fn() } as any }))
vi.mock('@desk-notes/shared', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@desk-notes/shared')>()
  return { ...mod, getWsClient: () => h.client }
})

const DECK_A = { id: 1, name: '高考词汇' }
const DECK_B = { id: 2, name: 'GRE 核心' }
const CARDS = [
  { id: 11, word: 'abandon', meaning: '放弃', tags: [] },
  { id: 12, word: 'absorb', meaning: '吸收', tags: [] },
]

beforeEach(() => {
  vi.clearAllMocks()
  h.client.status = 'connected'
  useStore.setState({ locale: 'zh-CN' })
})

function seedDeckRpc() {
  h.client.call.mockImplementation(async (method: string, params: any) => {
    if (method === 'wordbook.list') return [DECK_A, DECK_B]
    if (method === 'word.list') return params?.wordbook_id === DECK_A.id ? CARDS : []
    if (method === 'word.list_due') return []
    return []
  })
}

describe('FlashcardModule', () => {
  it('挂载经 wordbook.* 端到端加载卡组，并自动选中首个卡组拉卡片', async () => {
    seedDeckRpc()
    render(<FlashcardModule />)

    // 卡组名来自 wordbook.list，证明非本地壳
    expect(await screen.findByText('高考词汇')).toBeTruthy()
    expect(screen.getByText('GRE 核心')).toBeTruthy()

    // 自动选中首卡组 → 以其 wordbook_id 拉 word.list
    await waitFor(() =>
      expect(h.client.call).toHaveBeenCalledWith('word.list', { wordbook_id: DECK_A.id }),
    )
    expect(h.client.call).toHaveBeenCalledWith('wordbook.list', {})
  })

  it('后端断连时降级为「无法加载牌组」而非崩溃', async () => {
    h.client.status = 'disconnected'
    render(<FlashcardModule />)

    expect(await screen.findByText('无法加载牌组')).toBeTruthy()
  })

  it('点击第二个卡组标签，以对应 wordbook_id 重新拉取卡片', async () => {
    seedDeckRpc()
    render(<FlashcardModule />)

    const deckB = await screen.findByText('GRE 核心')
    fireEvent.click(deckB)

    await waitFor(() =>
      expect(h.client.call).toHaveBeenCalledWith('word.list', { wordbook_id: DECK_B.id }),
    )
  })
})
