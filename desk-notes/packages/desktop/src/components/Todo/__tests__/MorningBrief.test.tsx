import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import MorningBrief from '../MorningBrief'

// ─── Mock data ────────────────────────────────────────────
const mockHuangliData = {
  solarDate: '2026-05-13',
  lunar: { month: '四月', day: '廿七', fullDate: '二〇二六年四月廿七', isLeap: false },
  ganzhi: { year: '丙午', month: '癸巳', day: '己丑', time: '甲子' },
  shengxiao: { year: '马', yearZhi: '午' },
  yiJi: { yi: ['嫁娶', '开市'], ji: ['动土', '安葬'] },
  chongSha: { chong: '羊', sha: '东' },
  fangwei: { xi: '东北', cai: '正北', fu: '西南' },
  xiu: { name: '房', animal: '兔', luck: '吉', gong: '东方青龙' },
  jianChu: '除',
  jianChuLuck: '吉',
  pengZu: '己不破券二并井，丑不冠带主不还',
  naYin: '路旁土',
  jieQi: '',
  festivals: [],
  otherFestivals: [],
  bazi: '丙午 癸巳 己丑 甲子',
  dayGan: '己',
  dayZhi: '丑',
  weekDay: '星期三',
}

const mockBaziProfile = {
  fourPillars: { year: '丙午', month: '癸巳', day: '己丑', hour: '甲子' },
  heavenlyStems: { year: '丙', month: '癸', day: '己', hour: '甲' },
  earthlyBranches: { year: '午', month: '巳', day: '丑', hour: '子' },
  dayMaster: '己',
  wuxing: { 火: 2, 土: 2, 金: 1, 水: 2, 木: 1 },
  shengxiao: '马',
  naYin: '路旁土',
  strength: '中' as const,
  yongShen: ['火', '土'],
  jiShen: ['水', '木'],
  missingElement: '金',
  dayStemBranch: '己丑',
}

const mockAdvice = {
  level: '吉' as const,
  suitable: ['学习', '工作'],
  avoid: ['投资'],
  luckyDirection: '东南',
  clothingAdvice: '穿红色或黄色',
  summary: '今日运势不错',
}

const mockDailyGuidance = {
  huangli: mockHuangliData,
  advice: mockAdvice,
  todoStats: { total: 5, done: 2, pending: 3, overdue: 1, highPriority: 1 },
}

// ─── Module mocks ─────────────────────────────────────────
const mockStore: Record<string, unknown> = {
  todos: [],
  userProfile: null,
  aiEnabled: false,
  activeCat: 'all',
}

vi.mock('../../../store/useStore', () => ({
  useStore: (selector: (s: typeof mockStore) => unknown) => selector(mockStore),
}))

vi.mock('../../../i18n', async () => {
  const mod = await import('@desk-notes/shared/i18n/zh-CN')
  const zh = mod.default as Record<string, unknown>
  function getKey(obj: Record<string, unknown>, path: string): string | undefined {
    const parts = path.split('.')
    let cur: unknown = obj
    for (const p of parts) {
      if (cur === null || typeof cur !== 'object') return undefined
      cur = (cur as Record<string, unknown>)[p]
    }
    return typeof cur === 'string' ? cur : undefined
  }
  function interpolate(tpl: string, params?: Record<string, string | number>): string {
    if (!params) return tpl
    return tpl.replace(/\{(\w+)\}/g, (_, k) => (k in params ? String(params[k]) : `{${k}}`))
  }
  return {
    useTranslation: () => ({
      t: (key: string, params?: Record<string, string | number>) => {
        const raw = getKey(zh, key)
        return raw === undefined ? key : interpolate(raw, params)
      },
    }),
  }
})

vi.mock('../../../utils/astrology', () => ({
  getHuangli: vi.fn(),
  calculateBazi: vi.fn(),
  getDailyGuidance: vi.fn(),
  generateSummaryQuote: vi.fn(),
}))

vi.mock('../../../utils/astrology/weather', () => ({
  fetchWeather: vi.fn(),
  getWeatherEmoji: vi.fn(() => '☀️'),
}))

vi.mock('../../../utils/astrology/fengshui', () => ({
  getFengshuiAdvice: vi.fn(),
}))

vi.mock('../../../utils/astrology/ai-guidance', () => ({
  getAIGuidance: vi.fn(),
}))

import { getHuangli, calculateBazi, getDailyGuidance, generateSummaryQuote } from '../../../utils/astrology'
import { fetchWeather } from '../../../utils/astrology/weather'
import { getFengshuiAdvice } from '../../../utils/astrology/fengshui'
import { getAIGuidance } from '../../../utils/astrology/ai-guidance'

// ─── Tests ────────────────────────────────────────────────
describe('MorningBrief', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStore.todos = []
    mockStore.userProfile = null
    mockStore.aiEnabled = false
    mockStore.activeCat = 'all'

    vi.mocked(getHuangli).mockReturnValue(mockHuangliData)
    vi.mocked(calculateBazi).mockReturnValue(null)
    vi.mocked(getDailyGuidance).mockReturnValue(mockDailyGuidance)
    vi.mocked(generateSummaryQuote).mockReturnValue('今日适合学习和工作')
    // Never resolve by default to prevent act() warnings from weather effect
    vi.mocked(fetchWeather).mockReturnValue(new Promise(() => {}))
  })

  it('renders header and expanded content by default', () => {
    render(<MorningBrief />)
    expect(screen.getByText('晨间简报')).toBeTruthy()
    expect(screen.getByText(/四月/)).toBeTruthy()
    expect(screen.getByText(/丙午年/)).toBeTruthy()
  })

  it('collapses content on header click', () => {
    render(<MorningBrief />)
    expect(screen.getByText(/四月/)).toBeTruthy()
    fireEvent.click(screen.getByText('晨间简报'))
    expect(screen.queryByText(/四月/)).toBeNull()
  })

  it('toggles expand/collapse on repeated clicks', () => {
    render(<MorningBrief />)
    const btn = screen.getByText('晨间简报')
    expect(screen.getByText(/四月/)).toBeTruthy()
    fireEvent.click(btn)
    expect(screen.queryByText(/四月/)).toBeNull()
    fireEvent.click(btn)
    expect(screen.getByText(/四月/)).toBeTruthy()
  })

  it('shows weather data when fetch resolves', async () => {
    vi.mocked(fetchWeather).mockResolvedValue({
      city: '北京', condition: '晴', temp: 25,
      feelsLike: 24, humidity: 30, windSpeed: 3, iconCode: 'clear-day',
      fetchedAt: Date.now(),
    })
    render(<MorningBrief />)
    await waitFor(() => {
      expect(screen.getByText(/25°C/)).toBeTruthy()
    })
  })

  it('shows bazi profile when userProfile has birthDate', () => {
    mockStore.userProfile = { birthDate: '1990-01-01', birthHour: 8, gender: 'male' }
    vi.mocked(calculateBazi).mockReturnValue(mockBaziProfile)
    render(<MorningBrief />)
    expect(screen.getByText(/身中/)).toBeTruthy()
    expect(screen.getByText(/穿红色或黄色/)).toBeTruthy()
  })

  it('shows AI loading state then advice', async () => {
    mockStore.userProfile = { birthDate: '1990-01-01', birthHour: 8, gender: 'male' }
    mockStore.aiEnabled = true
    vi.mocked(calculateBazi).mockReturnValue(mockBaziProfile)

    let resolveAI!: (v: { text: string; cached: boolean }) => void
    vi.mocked(getAIGuidance).mockReturnValue(new Promise(r => { resolveAI = r }))

    render(<MorningBrief />)
    expect(screen.getByText('AI 分析中…')).toBeTruthy()

    await act(async () => { resolveAI({ text: '整理待办事项', cached: false }) })

    expect(screen.queryByText('AI 分析中…')).toBeNull()
    expect(screen.getByText('整理待办事项')).toBeTruthy()
  })

  it('shows guidance quote and todo stats', () => {
    render(<MorningBrief />)
    expect(screen.getByText('今日适合学习和工作')).toBeTruthy()
    expect(screen.getByText(/5 项/)).toBeTruthy()
    expect(screen.getByText(/1 过期/)).toBeTruthy()
  })

  it('shows fengshui tip with lucky direction', () => {
    vi.mocked(getFengshuiAdvice).mockReturnValue({
      deskOrientation: '书桌朝向东南',
      seatPlacement: '座位靠实墙',
      enhancement: '放置绿色植物',
      avoid: '背后有窗',
    })
    render(<MorningBrief />)
    expect(screen.getByText(/吉方/)).toBeTruthy()
    expect(screen.getByText('放置绿色植物')).toBeTruthy()
    expect(vi.mocked(getFengshuiAdvice)).toHaveBeenCalledWith('life', '东南')
  })

  it('handles weather fetch error gracefully', async () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(fetchWeather).mockRejectedValue(new Error('net'))
    render(<MorningBrief />)
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith('[weather] fetch failed:', 'net')
    })
    spy.mockRestore()
  })

  it('handles AI guidance error gracefully', async () => {
    mockStore.userProfile = { birthDate: '1990-01-01', birthHour: 8, gender: 'male' }
    mockStore.aiEnabled = true
    vi.mocked(calculateBazi).mockReturnValue(mockBaziProfile)
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    vi.mocked(getAIGuidance).mockRejectedValue(new Error('ai error'))
    render(<MorningBrief />)
    await waitFor(() => {
      expect(spy).toHaveBeenCalledWith('[astro] AI guidance failed:', 'ai error')
    })
    spy.mockRestore()
  })

  it('omits fengshui and todo stats when advice is missing', () => {
    vi.mocked(getDailyGuidance).mockReturnValue({
      ...mockDailyGuidance,
      advice: null,
      todoStats: { total: 0, done: 0, pending: 0, overdue: 0, highPriority: 0 },
    })
    render(<MorningBrief />)
    expect(screen.queryByText(/吉方/)).toBeNull()
    expect(screen.queryByText(/项/)).toBeNull()
  })
})
