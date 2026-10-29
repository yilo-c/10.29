import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { AutomationModule, isAutonomousJob, type CronJob } from './AutomationModule'
import { useStore } from '../../store/useStore'

const mockGetWsClient = vi.fn()

vi.mock('../../utils/ai', () => ({
  getWsClient: () => mockGetWsClient(),
}))

interface TestWsClient {
  call: ReturnType<typeof vi.fn>
}

function makeClient(): TestWsClient {
  return { call: vi.fn() }
}

const SAMPLE_JOB: CronJob = {
  job_id: '2da4f39e325a',
  name: '每日简报',
  action_id: 'subagent.spawn',
  schedule: '0 9 * * *',
  enabled: true,
  next_run_at: '2026-08-05T01:00:00Z',
  history: [],
}

// 一次性任务：repeat=none 且不算自主任务
const ONESHOT_JOB: CronJob = {
  ...SAMPLE_JOB,
  job_id: 'oneshot0001',
  name: '一次性清理',
  schedule: undefined,
  repeat: 'none',
  enabled: true,
}

// 停用的周期任务：静音后应作为「恢复」targets
const DISABLED_JOB: CronJob = {
  ...SAMPLE_JOB,
  job_id: 'disabledjob',
  name: '已停用的日报',
  schedule: undefined,
  repeat: 'daily',
  enabled: false,
}

function seedCalls(client: TestWsClient, overrides?: Partial<CronJob>[]) {
  const jobs = (overrides ?? [SAMPLE_JOB]).map((j) => ({ ...SAMPLE_JOB, ...j }))
  client.call.mockImplementation((method: string) => {
    if (method === 'cron.list') return Promise.resolve({ success: true, jobs })
    if (method === 'cron.status') return Promise.resolve({ success: true, status: { running: true, engine: 'apscheduler', job_count: jobs.length, last_error: '' } })
    if (method === 'cron.toggle') return Promise.resolve({ success: true, job_id: jobs[0]?.job_id, enabled: !jobs[0]?.enabled })
    if (method === 'cron.toggle_batch')
      return Promise.resolve({ success: true, changed_count: jobs.length, not_found: [], enabled: false })
    if (method === 'cron.run_now') return Promise.resolve({ success: true, job_id: jobs[0]?.job_id, result: 'ok' })
    if (method === 'cron.history') return Promise.resolve({ success: true, job_id: '2da4f39e325a', history: [] })
    return Promise.resolve({})
  })
}

describe('AutomationModule', () => {
  let client: TestWsClient

  beforeEach(() => {
    vi.clearAllMocks()
    useStore.setState({ locale: 'zh-CN' })
    client = makeClient()
    mockGetWsClient.mockReturnValue(client)
  })

  afterEach(() => {
    useStore.setState({ locale: 'zh-CN' })
  })

  it('成功加载定时任务列表并渲染', async () => {
    seedCalls(client)
    render(<AutomationModule />)

    expect(await screen.findByText('每日简报')).toBeTruthy()
    expect(screen.getByText('subagent.spawn')).toBeTruthy()
    expect(screen.getByTestId('automation-workspace')).toBeTruthy()
  })

  it('P0 修复：cron.list 必须带 { include_disabled: true }', async () => {
    seedCalls(client)
    render(<AutomationModule />)
    await screen.findByText('每日简报')

    const listCall = client.call.mock.calls.find((c) => c[0] === 'cron.list')
    expect(listCall).toBeTruthy()
    expect(listCall?.[1]).toEqual({ include_disabled: true })
  })

  it('显示 cron.status 引擎信息', async () => {
    seedCalls(client)
    render(<AutomationModule />)

    expect(await screen.findByText('apscheduler')).toBeTruthy()
  })

  it('停用项渲染「已停用」徽标', async () => {
    seedCalls(client, [DISABLED_JOB])
    render(<AutomationModule />)

    expect(await screen.findByText('已停用的日报')).toBeTruthy()
    expect(screen.getByTestId('cron-disabled-badge-disabledjob')).toBeTruthy()
    expect(screen.getByTestId('cron-row-disabledjob').getAttribute('data-disabled')).toBe('true')
  })

  it('空态显示暂无定时任务', async () => {
    seedCalls(client, [])
    render(<AutomationModule />)

    expect(await screen.findByText('暂无定时任务')).toBeTruthy()
  })

  it('list 返回失败时显示错误', async () => {
    client.call.mockResolvedValue({ success: false, reason: 'cron service down' })
    render(<AutomationModule />)

    expect(await screen.findByRole('alert')).toBeTruthy()
  })

  it('点击停用调用 cron.toggle', async () => {
    seedCalls(client)
    render(<AutomationModule />)

    await screen.findByText('每日简报')
    fireEvent.click(screen.getByRole('button', { name: /停用/ }))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('cron.toggle', { job_id: '2da4f39e325a', enabled: false })
    })
  })

  it('点击执行调用 cron.run_now', async () => {
    seedCalls(client)
    render(<AutomationModule />)

    await screen.findByText('每日简报')
    fireEvent.click(screen.getByRole('button', { name: /立即执行/ }))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('cron.run_now', { job_id: '2da4f39e325a' })
    })
  })

  it('点击历史展开/收起执行记录', async () => {
    seedCalls(client)
    render(<AutomationModule />)

    await screen.findByText('每日简报')
    fireEvent.click(screen.getByRole('button', { name: '历史' }))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith('cron.history', { job_id: '2da4f39e325a', limit: 20 })
    })
    expect(screen.getByTestId('cron-history-2da4f39e325a')).toBeTruthy()

    fireEvent.click(screen.getByText('收起'))
    await waitFor(() => {
      expect(screen.queryByTestId('cron-history-2da4f39e325a')).toBeNull()
    })
  })

  // ── U2（S3-5）：一键静音自主任务 ──

  it('isAutonomousJob: 周期/interval/cron 与带 schedule 的缺省 repeat 算自主任务，一次性不算', () => {
    const base = { job_id: 'x', name: 'n', action_id: 'a', enabled: true, history: [] }
    expect(isAutonomousJob({ ...base, repeat: 'daily' } as CronJob)).toBe(true)
    expect(isAutonomousJob({ ...base, repeat: 'cron' } as CronJob)).toBe(true)
    expect(isAutonomousJob({ ...base, repeat: 'interval' } as CronJob)).toBe(true)
    // 历史数据形状：repeat 缺省(undefined) 但有 cron schedule → 算自主任务
    expect(isAutonomousJob({ ...base, schedule: '0 8 * * *' } as unknown as CronJob)).toBe(true)
    // 一次性（repeat=none/once / 无 schedule）不算
    expect(isAutonomousJob({ ...base, repeat: 'none', schedule: '0 8 * * *' } as CronJob)).toBe(false)
    expect(isAutonomousJob({ ...base, repeat: 'once' } as CronJob)).toBe(false)
    expect(isAutonomousJob({ ...base, repeat: 'none' } as CronJob)).toBe(false)
  })

  it('一键静音：targets 含周期 job id、不含 one-shot id，toggle_batch(enabled=false)', async () => {
    // include_disabled 拉全量后，列表同时有启用周期项 + 一次性项 + 已停用周期项
    seedCalls(client, [SAMPLE_JOB, ONESHOT_JOB, DISABLED_JOB])
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<AutomationModule />)

    await screen.findByText('每日简报')
    fireEvent.click(screen.getByTestId('automation-mute-all'))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith(
        'cron.toggle_batch',
        expect.objectContaining({
          enabled: false,
          job_ids: expect.arrayContaining(['2da4f39e325a']),
        }),
      )
    })
    const args = client.call.mock.calls.find((c) => c[0] === 'cron.toggle_batch')?.[1] as {
      job_ids: string[]
    }
    // 一次性任务绝不能被静音
    expect(args.job_ids).not.toContain('oneshot0001')
    // 已停用项不在静音 targets（静音只针对 enabled 项）
    expect(args.job_ids).not.toContain('disabledjob')
    confirmSpy.mockRestore()
  })

  it('恢复自主任务：以 enabled=false 的周期 job 为 targets，toggle_batch(enabled=true)', async () => {
    // 静音后列表返回：停用周期项（disabledjob）+ 一次性项（oneshot0001，非自主，不恢复）
    seedCalls(client, [DISABLED_JOB, ONESHOT_JOB])
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<AutomationModule />)

    await screen.findByText('已停用的日报')
    fireEvent.click(screen.getByTestId('automation-restore-all'))

    await waitFor(() => {
      expect(client.call).toHaveBeenCalledWith(
        'cron.toggle_batch',
        expect.objectContaining({
          enabled: true,
          job_ids: ['disabledjob'],
        }),
      )
    })
    const args = client.call.mock.calls.find((c) => c[0] === 'cron.toggle_batch')?.[1] as {
      job_ids: string[]
    }
    expect(args.job_ids).not.toContain('oneshot0001')
    confirmSpy.mockRestore()
  })

  it('not_found 非空但 success=true 时仍按成功处理并显示提示', async () => {
    seedCalls(client, [SAMPLE_JOB])
    client.call.mockImplementation((method: string) => {
      if (method === 'cron.list') return Promise.resolve({ success: true, jobs: [SAMPLE_JOB] })
      if (method === 'cron.status') return Promise.resolve({ success: true, status: { running: true, engine: 'apscheduler', job_count: 1, last_error: '' } })
      if (method === 'cron.toggle_batch')
        return Promise.resolve({ success: true, changed_count: 1, not_found: ['ghost-id'], enabled: false })
      return Promise.resolve({})
    })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<AutomationModule />)

    await screen.findByText('每日简报')
    fireEvent.click(screen.getByTestId('automation-mute-all'))

    // 成功提示（role=status / data-testid），不是错误 alert
    const notice = await screen.findByTestId('automation-notice')
    expect(notice.textContent).toContain('已静音 1 个自主任务')
    expect(notice.textContent).toContain('1 个任务未找到')
    expect(screen.queryByRole('alert')).toBeNull()
    confirmSpy.mockRestore()
  })

  it('取消确认弹窗则不发起批量调用', async () => {
    seedCalls(client)
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(<AutomationModule />)

    await screen.findByText('每日简报')
    fireEvent.click(screen.getByTestId('automation-mute-all'))

    await new Promise((r) => setTimeout(r, 30))
    expect(client.call).not.toHaveBeenCalledWith('cron.toggle_batch', expect.anything())
    confirmSpy.mockRestore()
  })

  it('i18n：切 en locale 后按钮显示英文（走 useTranslation）', async () => {
    useStore.setState({ locale: 'en' })
    seedCalls(client)
    render(<AutomationModule />)

    await screen.findByText('每日简报')
    expect(screen.getByTestId('automation-mute-all').textContent).toBe('Mute autonomous tasks')
    expect(screen.getByTestId('automation-restore-all').textContent).toBe('Restore tasks')
    useStore.setState({ locale: 'zh-CN' })
  })
})
