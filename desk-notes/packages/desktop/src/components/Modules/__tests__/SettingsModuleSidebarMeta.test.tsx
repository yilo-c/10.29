/**
 * U4 修复回归：SettingsModule 侧边栏管理 MODULE_META 补缺 key。
 *
 * 此前第二份 MODULE_META 缺 memory/automation/task-queue/usage/notification 等，
 * 导致这些模块在「设置 → 侧边栏管理」页因 `if (!meta) return null` 静默消失。
 * 本测试断言：默认 sidebarConfig 引用的模块在补 key 后全部可见。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import SettingsModule from '../SettingsModule'
import { useStore } from '../../../store/useStore'
import { getDefaultSidebarConfig } from '@desk-notes/shared'

const callMock = vi.fn()

// CloudSyncPanel / SettingsModule 内部经 desktop 桥接取 ws client：统一 mock。
// status 置为 connecting，让 CloudSync 的 sync.status 轮询自行短路跳过。
vi.mock('../../../utils/ai', () => ({
  getWsClient: () => ({
    status: 'connecting',
    call: callMock,
    onStatusChange: () => () => {},
  }),
}))

beforeEach(() => {
  callMock.mockReset()
  callMock.mockResolvedValue({})
  useStore.setState({ locale: 'zh-CN' })
})

describe('SettingsModule 侧边栏管理 META 补缺（U4）', () => {
  it('默认配置引用但此前 META 缺 key 的模块（memory/agent/job-hunting/info/rss/... 共7个）全部渲染', () => {
    // 前提校验：这些 key 确实被 sidebarConfig.items 引用（消失根因是 META 缺 key）
    const cfg = getDefaultSidebarConfig()
    const keys = cfg.items.map((it) => it.key)
    for (const k of ['memory', 'agent', 'job-hunting', 'info', 'rss', 'system-monitor', 'app-manager']) {
      expect(keys, `${k} 应在 sidebarConfig.items 中`).toContain(k)
    }

    render(<SettingsModule />)
    // 此前因 `if (!meta) return null` 静默消失，补 key 后应出现在管理列表
    // （zh-CN：memory=记忆管理 agent=Agent 任务 job-hunting=求职猎手 info=信息
    //   rss=RSS 订阅 system-monitor=系统监控 app-manager=应用管理）
    for (const label of ['记忆管理', 'Agent 任务', '求职猎手', '信息', 'RSS 订阅', '系统监控', '应用管理']) {
      expect(screen.getAllByText(label).length, `模块「${label}」应在侧边栏管理列表`).toBeGreaterThan(0)
    }
  })

  it('既有正常模块不受影响（回归）', () => {
    render(<SettingsModule />)
    for (const label of ['小问', '笔记', '待办', '设置']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0)
    }
  })
})
