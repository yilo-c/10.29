import { test, expect } from './fixtures'

/** 2026-09-09 导航收敛：「日历」从侧边栏隐藏（模块与路由保留）。
 *  改用 dev 暴露的 store 直达（同 notification-center.spec 模式），C-12 实测对齐。 */
async function gotoCalendar(page: import('@playwright/test').Page) {
  await page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: { getState: () => { setActiveModule?: (m: string) => void } }
    }).__useStore
    store?.getState()?.setActiveModule?.('calendar')
  })
  // 2026-09-16 修：原为固定 waitForTimeout(800)。日历是**两层** React.lazy
  // （ContentRouter → CalendarModule → CalendarView，后者还拉 lunar-javascript 等重依赖），
  // 实测空载挂载耗时 ~1.1s（探针 2026-09-16），4 worker 并发下更久；800ms 睡完就断言
  // → "element(s) not found" 假失败（同批 dashboard 用例也是同一病根）。
  // 改为等真实挂载信号：月视图导航箭头可见（它就在 CalendarView 的根节点里）。
  await expect(page.locator('button:has([data-icon="fa-chevron-left"])').first())
    .toBeVisible({ timeout: 20_000 })
}

test.describe('Calendar module', () => {
  test.beforeEach(async ({ page }) => {
    await gotoCalendar(page)
  })

  test('calendar renders month view', async ({ page }) => {
    // P3c 图标迁移（FA→lucide）后 Icon 渲染 SVG，测试钩子改为 data-icon 语义键
    // （Common/Icon.tsx 注释「保留 FA 语义键，供测试与样式钩子定位」）；C-12 实测对齐。
    await expect(page.locator('button:has([data-icon="fa-chevron-right"])').first()).toBeVisible({ timeout: 5000 })
    // 月视图标志：头部「YYYY年 M月」
    await expect(page.locator('text=/\\d{4}年\\s*\\d{1,2}月/').first()).toBeVisible()
  })

  test('navigates to next month', async ({ page }) => {
    const nextBtn = page.locator('button:has([data-icon="fa-chevron-right"])').first()
    const prevBtn = page.locator('button:has([data-icon="fa-chevron-left"])').first()
    await expect(nextBtn).toBeVisible()

    await nextBtn.click()
    await page.waitForTimeout(300)

    // Navigate back
    await prevBtn.click()
    await page.waitForTimeout(300)
  })

  // 周视图切换：按钮在日历头部，但 fixtures 强制布局会同时渲染浮动笔记层，
  // 把日历内容区挤出视口（overflow 裁剪、不可滚动），常规 click 与 force click 均不可达。
  // 这是测试环境布局限制，非日历功能 bug（月视图渲染/月份导航均通过）。
  // 待 fixtures 不再叠加浮动笔记层后启用。
  test.fixme('switches to week view — blocked by fixture layout', async ({ page }) => {
    const weekBtn = page.locator('button.text-\\[8px\\]', { hasText: '周' }).first()
    await weekBtn.waitFor({ state: 'visible', timeout: 5000 })
    await weekBtn.click()
  })
})
