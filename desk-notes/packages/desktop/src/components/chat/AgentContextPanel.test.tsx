import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { AgentContextPanel } from './AgentContextPanel'

// ── Hoisted mocks ──────────────────────────────────────────────────────────
const { mockGetWsClient } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
}))

vi.mock('../../utils/ai', () => ({ getWsClient: mockGetWsClient }))
// i18n 与 ChatComposer.test 一致：t 返回 key 原文，断言用 key
vi.mock('../../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const baseProps = {
  events: [] as never[],
  connected: true,
  streaming: false,
  onCollapse: vi.fn(),
}

describe('AgentContextPanel kernel badge', () => {
  const mockCall = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({ call: mockCall, status: 'connected' })
    // 默认：非关键接口返回空
    mockCall.mockImplementation((method: string) => {
      if (method === 'kernel.status') {
        return Promise.resolve({
          route: 'gateway',
          effective_engine: 'gateway',
          degraded: false,
          gateway_present: true,
          gateway_name: 'dsh',
          kernel_type: 'dsh',
          message: '执行内核已启用（dsh）',
        })
      }
      if (method === 'agent.list') {
        return Promise.resolve({ success: true, agents: [], count: 0 })
      }
      if (method === 'trace.stats') {
        return Promise.resolve(null)
      }
      if (method === 'usage.today') {
        return Promise.resolve(null)
      }
      return Promise.resolve(null)
    })
  })

  it('shows kernel badge with gateway name when connected', async () => {
    render(<AgentContextPanel {...baseProps} />)

    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('kernel.status')
    })
    await waitFor(() => {
      expect(screen.getByText('dsh')).toBeTruthy()
      expect(screen.getByText('就绪')).toBeTruthy()
    })
  })

  it('shows degraded state when kernel unavailable', async () => {
    mockCall.mockImplementation((method: string) => {
      if (method === 'kernel.status') {
        return Promise.resolve({
          route: 'gateway',
          effective_engine: 'butler(fallback)',
          degraded: true,
          gateway_present: false,
          gateway_name: '',
          kernel_type: 'none',
          message: '执行内核不可用，已降级到遗留管家',
        })
      }
      return Promise.resolve(null)
    })
    render(<AgentContextPanel {...baseProps} />)

    await waitFor(() => {
      expect(screen.getByText('降级')).toBeTruthy()
    })
    const badge = screen.getByText('降级').closest('.agent-kernel-badge')
    expect(badge?.className).toContain('agent-kernel-badge--degraded')
  })

  it('hides kernel badge when disconnected', async () => {
    render(<AgentContextPanel {...baseProps} connected={false} />)
    expect(mockCall).not.toHaveBeenCalled()
    expect(screen.queryByText('执行内核')).toBeNull()
  })
})

describe('AgentContextPanel agent list and stats', () => {
  const mockCall = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({ call: mockCall, status: 'connected' })
  })

  it('lists available agents from agent.list', async () => {
    mockCall.mockImplementation((method: string) => {
      if (method === 'agent.list') {
        return Promise.resolve({
          success: true,
          agents: [
            { agent_id: 'claude_code', display_name: 'Claude Code', is_available: true, status: 'idle', capabilities: ['code_review', 'reasoning'] },
            { agent_id: 'codex', display_name: 'Codex', is_available: true, status: 'busy', capabilities: ['code_generation'] },
            { agent_id: 'trae', display_name: 'Trae', is_available: false, status: 'idle', capabilities: [] },
          ],
          count: 3,
        })
      }
      return Promise.resolve(null)
    })
    render(<AgentContextPanel {...baseProps} />)

    await waitFor(() => {
      expect(screen.getByText('可指挥 Agent')).toBeTruthy()
      // "Claude Code" 也出现在内核策略下拉选项中 → 用 getAllByText 或限定 agent-list 内
      expect(screen.getAllByText('Claude Code').length).toBeGreaterThan(0)
      expect(screen.getAllByText('Codex').length).toBeGreaterThan(0)
    })
    // 不可用的 trae 不展示
    expect(screen.queryByText('Trae')).toBeNull()
    // busy 状态展示"执行中"
    expect(screen.getByText('执行中')).toBeTruthy()
  })

  it('shows run stats and usage when backend provides them', async () => {
    mockCall.mockImplementation((method: string) => {
      if (method === 'trace.stats') {
        return Promise.resolve({ days: 7, total_runs: 11, success_rate: 1.0, by_status: { completed: 9, running: 2 } })
      }
      if (method === 'usage.today') {
        return Promise.resolve({ enabled: true, calls: 192, tokens: 180931, cost: 0.03 })
      }
      return Promise.resolve(null)
    })
    render(<AgentContextPanel {...baseProps} />)

    await waitFor(() => {
      expect(screen.getByText('运行概况')).toBeTruthy()
      expect(screen.getByText('11')).toBeTruthy()       // total_runs
      expect(screen.getByText('100%')).toBeTruthy()     // success_rate
      expect(screen.getByText('192')).toBeTruthy()      // calls
      expect(screen.getByText('180.9k')).toBeTruthy()   // tokens (180931/1000)
    })
  })

  it('keeps panel minimal when observability APIs are empty', async () => {
    mockCall.mockImplementation(() => Promise.resolve(null))
    render(<AgentContextPanel {...baseProps} />)
    // 非关键接口静默降级：内核徽章不显示（无数据），面板仍渲染
    await new Promise((r) => setTimeout(r, 50))
    expect(screen.getByTestId('agent-context-panel')).toBeTruthy()
    expect(screen.queryByText('运行概况')).toBeNull()
  })
})

describe('AgentContextPanel kernel policy hot-swap', () => {
  const mockCall = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    mockGetWsClient.mockReturnValue({ call: mockCall, status: 'connected' })
    mockCall.mockImplementation((method: string) => {
      if (method === 'kernel.status') {
        return Promise.resolve({
          route: 'gateway',
          effective_engine: 'gateway',
          degraded: false,
          gateway_present: true,
          gateway_name: 'dsh',
          kernel_type: 'auto',
          message: '执行内核已启用（dsh）',
        })
      }
      if (method === 'agent.list') {
        return Promise.resolve({ success: true, agents: [], count: 0 })
      }
      if (method === 'trace.stats') return Promise.resolve(null)
      if (method === 'usage.today') return Promise.resolve(null)
      if (method === 'settings.get') {
        // 当前配置：auto 带候选（保留子字段，仅改 type 的测试前提）
        return Promise.resolve(JSON.stringify({ type: 'auto', auto: { candidates: ['dsh', 'claude_code', 'codex'], max_failures: 2 } }))
      }
      if (method === 'settings.set') return Promise.resolve(true)
      return Promise.resolve(null)
    })
  })

  it('renders policy select with current type loaded', async () => {
    render(<AgentContextPanel {...baseProps} />)
    const select = await screen.findByTestId('kernel-policy-select') as HTMLSelectElement
    expect(select.value).toBe('auto')
    // 选项齐全
    const values = Array.from(select.options).map((o) => o.value)
    expect(values).toContain('auto')
    expect(values).toContain('dsh')
    expect(values).toContain('claude_code')
    expect(values).toContain('codex')
    expect(values).toContain('none')
  })

  it('applies new policy preserving sub-config via settings.set', async () => {
    render(<AgentContextPanel {...baseProps} />)
    const select = await screen.findByTestId('kernel-policy-select') as HTMLSelectElement

    // 触发变更（react-testing-library fireEvent.change）
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.change(select, { target: { value: 'claude_code' } })

    await waitFor(() => {
      expect(mockCall).toHaveBeenCalledWith('settings.set', expect.any(Object))
    })
    const setCall = mockCall.mock.calls.find((c) => c[0] === 'settings.set')
    const params = setCall?.[1] as { key: string; value: string }
    expect(params.key).toBe('agent_kernel')
    const cfg = JSON.parse(params.value) as Record<string, unknown>
    expect(cfg.type).toBe('claude_code')
    // 保留 auto 子配置
    expect((cfg.auto as Record<string, unknown>).candidates).toEqual(['dsh', 'claude_code', 'codex'])
    // 成功后刷新 kernel.status
    await waitFor(() => {
      const ksCalls = mockCall.mock.calls.filter((c) => c[0] === 'kernel.status').length
      expect(ksCalls).toBeGreaterThanOrEqual(2)
    })
  })

  it('shows saved feedback after successful switch', async () => {
    render(<AgentContextPanel {...baseProps} />)
    const select = await screen.findByTestId('kernel-policy-select') as HTMLSelectElement
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.change(select, { target: { value: 'codex' } })

    await waitFor(() => {
      expect(screen.getByText('chat.kernelPolicySaved')).toBeTruthy()
    })
  })

  it('shows failed feedback when settings.set rejects', async () => {
    mockCall.mockImplementation((method: string) => {
      if (method === 'settings.set') return Promise.resolve(false)
      if (method === 'settings.get') return Promise.resolve(null)
      return Promise.resolve(null)
    })
    render(<AgentContextPanel {...baseProps} />)
    const select = await screen.findByTestId('kernel-policy-select') as HTMLSelectElement
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.change(select, { target: { value: 'dsh' } })

    await waitFor(() => {
      expect(screen.getByText('chat.kernelPolicyFailed')).toBeTruthy()
    })
  })
})
