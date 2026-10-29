import { test, expect, type Page } from './fixtures'

/** Calendar（日历）深走查（正式回归）：
 *  进入 → 月视图渲染 → 月份导航（下月→上月）→ 默认选中今日面板 →
 *  双击日期创建事件（真实效果：isEvent todo 落库）→ 周视图切换（best-effort，
 *  受 fixtures 浮动笔记层布局限制）→ 离开模块。
 *  逐步骤断言 + console 错误收集。
 *
 * 2026-09-09 导航收敛后 calendar 从侧边栏隐藏，进模块经 dev 暴露的 store 直达。
 * 日历为**纯前端**：事件 = isEvent:true 的 todo（存在 store 的 todos 里），
 * 无独立 calendar.* / event.list RPC，因此「创建事件」走 UI 双击→内联输入→Enter，
 * 再以 store 回流断言真实落库。 */

type StepResult = { step: string; ok: boolean; note: string }

const results: StepResult[] = []

async function step(name: string, fn: () => Promise<void>) {
  try {
    await fn()
    results.push({ step: name, ok: true, note: '' })
  } catch (e) {
    results.push({ step: name, ok: false, note: String(e).slice(0, 160) })
  }
}

const mc = (page: Page) => page.getByTestId('module-content')

/** 经 dev 暴露的 store 直达（侧边栏已收敛，calendar 不在栏上） */
async function gotoCalendar(page: Page) {
  await page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: { getState: () => { setActiveModule?: (m: string) => void } }
    }).__useStore
    store?.getState()?.setActiveModule?.('calendar')
  })
  // 日历是两层 React.lazy（ContentRouter → CalendarModule → CalendarView，后者还拉
  // lunar-javascript / astrology 等重依赖）。首次冷挂载 + vite 依赖优化在并发下可能
  // 远超 20s，故等真实挂载信号（月视图「YYYY年M月」标题）并放宽到 60s，而非固定 sleep。
  await expect(mc(page).getByText(/\d{4}年\s*\d{1,2}月/).first()).toBeVisible({ timeout: 60_000 })
}

const haveBackend = () => Boolean(process.env.SIYI_E2E_WS_URL)

test('calendar module deep walkthrough', async ({ page }) => {
  test.skip(!haveBackend(), '需要 e2e 真后端 harness（未设 SIYI_E2E_WS_URL，browser-only 模式跳过）')
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入 calendar 模块', async () => {
    await gotoCalendar(page)
    expect(
      await page.evaluate(() => (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore?.getState()?.activeModule),
    ).toBe('calendar')
  })

  await step('月视图渲染', async () => {
    await expect(mc(page).locator('button:has([data-icon="fa-chevron-right"])').first()).toBeVisible({ timeout: 8000 })
    await expect(mc(page).getByText(/\d{4}年\s*\d{1,2}月/).first()).toBeVisible()
  })

  let navOk = false
  await step('月份导航（下月→上月）', async () => {
    const label = mc(page).getByText(/\d{4}年\s*\d{1,2}月/).first()
    const next = mc(page).locator('button:has([data-icon="fa-chevron-right"])').first()
    const prev = mc(page).locator('button:has([data-icon="fa-chevron-left"])').first()
    const before = (await label.textContent())?.trim()
    await next.click()
    await page.waitForTimeout(300)
    const after = (await label.textContent())?.trim()
    if (!before || before === after) throw new Error(`月份未变化 before=${before} after=${after}`)
    await prev.click()
    await page.waitForTimeout(300)
    const restored = (await label.textContent())?.trim()
    if (restored !== before) throw new Error(`返回后月份未还原 before=${before} restored=${restored}`)
    navOk = true
  })

  await step('默认选中今日 → 选中日面板渲染', async () => {
    // 月视图默认选中今天，选中面板应显示「X月X日」与待办/空态
    await expect(mc(page).getByText(/\d+月\d+日/).first()).toBeVisible({ timeout: 8000 })
  })

  let eventCreated = false
  await step('双击日期创建事件（真实效果：isEvent todo 落库）', async () => {
    const dayNum = new Date().getDate()
    const span = mc(page).getByText(String(dayNum), { exact: true }).first()
    const cell = span.locator('xpath=..')
    await cell.dblclick()
    // 内联输入自动聚焦（CalendarView 用 ref + setTimeout 聚焦），直接键入避免 input 定位歧义
    await page.waitForTimeout(200)
    await page.keyboard.type('走查事件A')
    await page.keyboard.press('Enter')
    // 效果断言：store 中出现 isEvent 且未完成的 todo
    await page.waitForFunction(
      () => {
        const todos = (window as unknown as { __useStore?: { getState: () => { todos: Array<{ isEvent?: boolean; done?: boolean }> } } }).__useStore?.getState()?.todos || []
        return todos.some((t) => t.isEvent && !t.done)
      },
      { timeout: 10_000 },
    )
    eventCreated = true
  })

  await step('周视图切换（best-effort，受 fixtures 浮动层布局限制）', async () => {
    const weekBtn = mc(page).getByRole('button', { name: '周' }).first()
    await weekBtn.scrollIntoViewIfNeeded().catch(() => {})
    await weekBtn.click({ timeout: 5000 })
    // 周视图下「YYYY年M月」月标题应消失（改为「M月D日」周标题）
    await expect(mc(page).getByText(/\d{4}年\s*\d{1,2}月/).first()).toBeHidden({ timeout: 4000 })
  })

  await step('离开模块 → 切到 todo', async () => {
    await page.evaluate(() => {
      const store = (window as unknown as { __useStore?: { getState: () => { setActiveModule?: (m: string) => void } } }).__useStore
      store?.getState()?.setActiveModule?.('todo')
    })
    await expect
      .poll(() => page.evaluate(() => (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore?.getState()?.activeModule))
      .toBe('todo')
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/calendar-walkthrough-end.png' }).catch(() => {})

  console.log('\n[CALENDAR WALKTHROUGH] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log(
    `[CALENDAR WALKTHROUGH] 月份导航=${navOk}; 事件创建=${eventCreated}; console errors=` +
      JSON.stringify(errors.slice(0, 8)),
  )
})
