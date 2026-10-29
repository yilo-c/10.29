import { test, expect } from './fixtures'

test.describe('Smoke tests', () => {
  test('app loads without errors', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', err => errors.push(err.message))

    // Already waited for app boot via fixture
    await expect(page.locator('#root')).toBeVisible()
    expect(errors).toEqual([])
  })

  test('nav sidebar has all module navigation buttons', async ({ page }) => {
    // 2026-09-16 对齐实测：SECTIONS 全量平铺 = 核心 3（小问/笔记/待办）
    // + 知识 2（知识网络/学习）+ 智能 5（Agent 任务/自动化/收件箱/求职猎手/记忆管理）
    // + 工具 1（工具）= 11 个 nav-section 项，另加 footer「设置」1 个 nav-item = **12**。
    // 旧断言 11 漏算了 footer 设置（U3 批2 新增 memory 面板后 NavSidebar 已到 12 项）；
    // 断言依据是 NavSidebar.tsx 的 SECTIONS + footer 结构，改动导航请同步此处。
    const navButtons = page.locator('[data-testid="nav-sidebar"] button.nav-item')
    await expect(navButtons).toHaveCount(12)
    // Each button should be visible
    const count = await navButtons.count()
    for (let i = 0; i < count; i++) {
      await expect(navButtons.nth(i)).toBeVisible()
    }
  })

  test('status bar shows', async ({ page }) => {
    // StatusBar shows 思忆集 text
    await expect(page.locator('text=思忆集').first()).toBeVisible()
  })
})
