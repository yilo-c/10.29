import { test, expect } from './fixtures'

/** 2026-09-09 导航收敛后「工作台」不再出现在侧边栏、boot 默认模块也不是 dashboard。
 *  C-12 实测对齐：进入模块改经 dev 暴露的 store 直达，再验面板内容。 */
async function gotoDashboard(page: import('@playwright/test').Page) {
  await page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: { getState: () => { setActiveModule?: (m: string) => void } }
    }).__useStore
    store?.getState()?.setActiveModule?.('dashboard')
  })
  // 2026-09-16 修：原为固定 waitForTimeout(600) + 立即 querySelector，属"睡够就算数"。
  // 实测 DashboardModule 是 React.lazy（ContentRouter 内），冷 chunk 在 4 worker 并发下
  // 远超 600ms → openStats 拿不到按钮直接返回 false，用例假失败（同批另一条却重试通过，
  // 正是竞态特征）。改为对定位器显式等待，去掉一切固定 sleep。
  // 注意用 testid 而非 title：title 走 i18n（en 下是 "Statistics"），断言不该绑语言。
  await page.getByTestId('dashboard-stats-btn').waitFor({ state: 'visible', timeout: 20_000 })
}

/** 打开统计视图。按钮不可达时直接抛出（Playwright 自动等待），不再用 boolean 返回值。 */
async function openStats(page: import('@playwright/test').Page) {
  await page.getByTestId('dashboard-stats-btn').click()
}

test.describe('Dashboard and Statistics', () => {
  test.beforeEach(async ({ page }) => {
    await gotoDashboard(page)
  })

  test('dashboard renders', async ({ page }) => {
    // Dashboard should have card elements
    const cards = page.locator('[class*="rounded-xl"]')
    await expect(cards.first()).toBeVisible({ timeout: 5000 })
  })

  test('statistics button opens stats view', async ({ page }) => {
    await openStats(page)

    // Should show statistics header
    await expect(page.locator('text=数据统计').first()).toBeVisible({ timeout: 5000 })
    await expect(page.locator('text=任务总数').first()).toBeVisible()
    await expect(page.locator('text=完成率').first()).toBeVisible()
    await expect(page.locator('text=完成任务趋势').first()).toBeVisible()
    await expect(page.locator('text=笔记活跃度').first()).toBeVisible()
  })

  test('back button returns from statistics', async ({ page }) => {
    await openStats(page)
    await expect(page.locator('text=数据统计').first()).toBeVisible({ timeout: 5000 })

    // 返回按钮：P3c 图标迁移（FA→lucide）后 Icon 渲染 <svg data-icon="fa-chevron-left">，
    // 原选择器 `i.fa-chevron-left` 匹配的是 FA 的 <i> 形态，早已失效 → backClicked=false
    // 时静默跳过点击，后续断言必然失败。改用 data-icon 语义键（与 calendar.spec 一致）。
    const backBtn = page.locator('button:has([data-icon="fa-chevron-left"])').first()
    await expect(backBtn).toBeVisible({ timeout: 5000 })
    await backBtn.click()

    // 返回后应重见工作台控制条（统计视图已卸载）
    await expect(page.getByTestId('dashboard-stats-btn')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('text=数据统计').first()).toBeHidden()
  })
})
