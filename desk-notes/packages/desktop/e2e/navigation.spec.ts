import { test, expect } from './fixtures'

test.describe('Module navigation (partitioned sidebar)', () => {
  const sidebar = (page: import('@playwright/test').Page) =>
    page.getByTestId('nav-sidebar')

  // 2026-09-09 导航收敛（NavSidebar SECTIONS）：侧边栏 10 分组项 + footer 设置。
  // 原「工作台 / 日历 / 通知中心」入口隐藏（模块路由保留，经 store 直达，见
  // dashboard/calendar/notification-center specs）；补登记新增的「求职猎手」。
  // C-12 实测对齐（2026-09-12）。父模块经 MERGED_DEFAULTS 路由到默认子模块。
  const modules = [
    { label: '小问', active: 'chat' },
    { label: '笔记', active: 'notes' },
    { label: '待办', active: 'todo' },
    { label: '知识网络', active: 'knowledge-graph' },
    { label: '学习', active: 'wordbank' },
    { label: 'Agent 任务', active: 'agent' },
    { label: '自动化', active: 'automation' },
    { label: '收件箱', active: 'inbox' },
    { label: '求职猎手', active: 'job-hunting' },
    { label: '工具', active: 'translate' },
    { label: '设置', active: 'settings' },
  ]

  for (const mod of modules) {
    test(`navigate to ${mod.active} module`, async ({ page }) => {
      const btn = sidebar(page).getByRole('button', { name: mod.label })
      await btn.click()
      await page.waitForTimeout(600)
      // Verify activeModule was set correctly via store
      const activeModule = await page.evaluate(() => {
        const store = (window as unknown as Record<string, unknown>).__useStore as
          | { getState: () => { activeModule: string } }
          | undefined
        return store?.getState()?.activeModule
      })
      expect(activeModule).toBe(mod.active)
    })
  }
})