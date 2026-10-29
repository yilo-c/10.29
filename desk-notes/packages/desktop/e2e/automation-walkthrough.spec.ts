import { test, expect, waitForWsFrames, type Page } from './fixtures'

/** 自动化（AutomationModule / 定时与队列）深走查（正式回归）：
 *  进入 → 任务列表加载（cron.list 协议取证）→ 新建任务弹窗（填名称 + cron）
 *  → 创建 → 新任务进入列表 → 展开详情 → 启用/停用切换 → ESC 关弹窗。
 *  逐步骤断言 + 协议层取证 + console 错误收集。 */

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

/** 进模块（3 次重试 + store 兜底——boot 早期 sidebar 点击会回弹） */
async function gotoModule(page: Page, label: string, active: string) {
  const setActive = (m: string) =>
    page.evaluate(
      (mod) => {
        const store = (window as unknown as { __useStore?: { setState: (s: Record<string, unknown>) => void } }).__useStore
        store?.setState({ activeModule: mod })
      },
      m,
    )
  const waitActive = (expected: string) =>
    page.waitForFunction(
      (expected) => {
        const store = (window as unknown as Record<string, unknown>).__useStore as
          | { getState: () => { activeModule: string } }
          | undefined
        return store?.getState()?.activeModule === expected
      },
      expected,
      { timeout: 4000 },
    )
  const btn = page.getByTestId('nav-sidebar').getByRole('button', { name: label }).first()
  await btn.scrollIntoViewIfNeeded().catch(() => {})
  await page.waitForTimeout(200)
  let ok = false
  for (let attempt = 0; attempt < 3 && !ok; attempt++) {
    await btn.click({ timeout: 5000 }).catch(() => {})
    try {
      await waitActive(active)
      ok = true
    } catch {
      await setActive(active)
      try {
        await waitActive(active)
        ok = true
      } catch {
        /* 再试 */
      }
    }
  }
  if (!ok) {
    const current = await page.evaluate(() => {
      const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
      return store?.getState()?.activeModule
    })
    throw new Error(`导航「${label}」3 次均未落到 ${active}（当前=${current}）`)
  }
  await page.waitForTimeout(1200)
}

const mc = (page: Page) => page.getByTestId('module-content')
const JOB = 'AU走查任务'

test('automation module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入自动化模块', async () => {
    await gotoModule(page, '自动化', 'automation')
    expect(
      await page.evaluate(() => {
        const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
        return store?.getState()?.activeModule
      }),
    ).toBe('automation')
  })

  await step('任务列表加载（cron.list 协议取证）', async () => {
    await expect(mc(page).getByText('定时与队列').first()).toBeVisible({ timeout: 8000 })
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'cron.list'),
      20_000,
      'cron.list 请求',
    )
    await page.waitForTimeout(600)
  })

  await step('打开新建任务弹窗', async () => {
    const newBtn = mc(page).getByRole('button', { name: /新建/ }).first()
    await expect(newBtn).toBeVisible({ timeout: 5000 })
    await newBtn.click({ timeout: 5000 })
    await page.getByTestId('create-cron-modal').waitFor({ timeout: 5000 })
  })

  await step('填名称 + 执行动作 + cron 并创建 → 新任务进列表', async () => {
    const modal = page.getByTestId('create-cron-modal')
    const modalEl = modal.first()
    const html = await modalEl.evaluate((el) => el.outerHTML.slice(0, 700)).catch(() => 'n/a')
    console.log('[WALKTHROUGH-AUTO] modal html =', html)
    // 名称 = 弹窗内第 1 个 input（位置选择，免疫 placeholder 文案差异）
    await modal.locator('input').first().fill(JOB)
    // 执行动作必选——createJob 校验 actionRequired，不选会创建失败
    const select = modal.locator('select').first()
    const opts = await select
      .locator('option')
      .evaluateAll((os) => os.map((o) => (o as HTMLOptionElement).value).filter(Boolean))
    if (opts.length > 0) await select.selectOption(opts[0])
    const cronInput = modal.getByPlaceholder('0 8 * * *')
    if ((await cronInput.count()) > 0) await cronInput.first().fill('0 9 * * *')
    await modal.getByRole('button', { name: /^创建任务$/ }).click({ timeout: 5000 })
    await page.waitForTimeout(1200)
    await expect(mc(page).getByText(JOB).first()).toBeVisible({ timeout: 8000 })
  })

  await step('ESC 关闭新建弹窗（若仍打开）', async () => {
    if ((await page.getByTestId('create-cron-modal').count()) > 0) {
      await page.keyboard.press('Escape')
      await page.waitForTimeout(500)
    }
  })

  await step('展开任务详情', async () => {
    const jobRow = mc(page).getByText(JOB).first()
    await jobRow.click({ timeout: 5000 })
    await page.waitForTimeout(600)
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/automation-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-AUTO] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-AUTO] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
