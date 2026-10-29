/**
 * U4 未配置模块折叠：NavSidebar 消费后端 modules.status 的导航过滤测试。
 *
 * 判定式（与施工单一致）：模块在 status 列表且 configured=false → 隐藏导航项；
 * 不在列表 / 后端未升级（RPC 异常或 success:false）→ 显示（不误伤）。
 * 后端由并行批产出，本测试全部走 mock。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import NavSidebar from '../NavSidebar'
import { useStore } from '../../../store/useStore'

const callMock = vi.fn()

vi.mock('../../../utils/ai', () => ({
  getWsClient: () => ({
    status: 'connected',
    call: callMock,
    onStatusChange: () => () => {},
  }),
}))

/** 默认：modules.status 未实现（后端未升级）→ 抛错；task_queue.stats 正常返回 0 死信 */
function seedRpc(modulesStatus: unknown) {
  callMock.mockImplementation((method: string) => {
    if (method === 'task_queue.stats') {
      return Promise.resolve({ success: true, stats: { pending: 0, dead_letter: 0 } })
    }
    if (method === 'modules.status') {
      if (modulesStatus instanceof Error) return Promise.reject(modulesStatus)
      return Promise.resolve(modulesStatus)
    }
    return Promise.resolve({})
  })
}

beforeEach(() => {
  callMock.mockReset()
  useStore.setState({
    activeModule: 'chat',
    navCollapsed: false,
    locale: 'zh-CN',
    moduleAvailability: null,
  })
})

afterEach(() => {
  // 防止污染其他测试（store 为全局单例）
  useStore.setState({ moduleAvailability: null })
})

describe('NavSidebar 未配置模块折叠（U4）', () => {
  it('modules.status RPC 异常（后端未升级/未连接）→ 不误伤，SECTIONS 全部照常渲染', async () => {
    seedRpc(new Error('method not found'))
    render(<NavSidebar />)
    await waitFor(() => expect(callMock).toHaveBeenCalledWith('modules.status', {}))
    // 未知状态 = 全可用：四个分组的关键项都在（zh-CN 文案）
    expect(useStore.getState().moduleAvailability).toBeNull()
    expect(await screen.findByText('小问')).toBeTruthy()
    expect(screen.getByText('Agent 任务')).toBeTruthy()
    expect(screen.getByText('自动化')).toBeTruthy()
    expect(screen.getByText('记忆管理')).toBeTruthy()
    expect(screen.getByText('求职猎手')).toBeTruthy()
  })

  it('modules.status 返回 success:false → 不误伤，全部照常渲染', async () => {
    seedRpc({ success: false, error: 'internal' })
    render(<NavSidebar />)
    await waitFor(() => expect(useStore.getState().moduleAvailability).toBeNull())
    expect(await screen.findByText('记忆管理')).toBeTruthy()
    expect(screen.getByText('自动化')).toBeTruthy()
  })

  it('configured=true 的模块 + 不在列表的模块 → 全渲染，行为零变化（现状回归）', async () => {
    seedRpc({
      success: true,
      modules: [
        { module: 'memory', configured: true, hasData: true },
        { module: 'automation', configured: true, hasData: false },
        // reading 不在 SECTIONS 中（现状），即便报告 configured=false 也不影响渲染
        { module: 'reading', configured: false, hasData: false },
      ],
    })
    render(<NavSidebar />)
    await waitFor(() => expect(screen.getByText('记忆管理')).toBeTruthy())
    expect(screen.getByText('自动化')).toBeTruthy()
    expect(screen.getByText('小问')).toBeTruthy()
  })

  it('SECTIONS 内模块（memory）被判定 configured=false → 该项隐藏，组内其余项正常', async () => {
    seedRpc({
      success: true,
      modules: [
        { module: 'memory', configured: false, hasData: false },
        { module: 'agent', configured: true, hasData: true },
      ],
    })
    render(<NavSidebar />)
    await waitFor(() =>
      expect(useStore.getState().moduleAvailability?.memory?.configured).toBe(false),
    )
    // memory（记忆管理）被隐藏，但其同组（智能）其余项不受影响
    expect(screen.queryByText('记忆管理')).toBeNull()
    expect(screen.getByText('Agent 任务')).toBeTruthy()
    expect(screen.getByText('自动化')).toBeTruthy()
    expect(screen.getByText('求职猎手')).toBeTruthy()
    // 分组标题仍在（组内还有可见项）
    expect(screen.getByText('智能')).toBeTruthy()
  })

  it('整组全部隐藏（tools 组仅 tools 一项）→ 组标题一并消失', async () => {
    // 现状：'工具' 文案在 DOM 中出现 2 次 —— groupTools 组标题 + tools 导航项
    seedRpc({
      success: true,
      modules: [{ module: 'tools', configured: false, hasData: false }],
    })
    const { container } = render(<NavSidebar />)
    await waitFor(() =>
      expect(useStore.getState().moduleAvailability?.tools?.configured).toBe(false),
    )
    expect(container.querySelectorAll('.nav-section-label').length).toBe(3)
    // tools 项与其组标题都不再渲染
    const toolsLabels = [...container.querySelectorAll('.nav-item-label')].filter(
      (el) => el.textContent === '工具',
    )
    expect(toolsLabels.length).toBe(0)
    // 其余分组与项目正常
    expect(screen.getByText('小问')).toBeTruthy()
    expect(screen.getByText('知识网络')).toBeTruthy()
  })

  it('拉取结果写入 store：availability 映射含 hasData 透传', async () => {
    seedRpc({
      success: true,
      modules: [
        { module: 'reminder', configured: false, hasData: true },
        { module: 'external_content', configured: true, hasData: false },
      ],
    })
    render(<NavSidebar />)
    await waitFor(() => {
      const av = useStore.getState().moduleAvailability
      expect(av).not.toBeNull()
      expect(av?.reminder).toEqual({ configured: false, hasData: true })
      expect(av?.external_content).toEqual({ configured: true, hasData: false })
    })
  })
})
