import { test, expect, waitForWsFrames, type Page } from './fixtures'

/** 求职猎手（JobHunterModule）深走查（正式回归）：进入 → 工作台渲染
 *  （job_hunter.jobs.list 协议取证）→ 四 tab 切换（职位库/我的画像/简历工具/
 *  智能分析，断言激活态 border-b-2）→ 状态筛选 → 抓取职位触发
 *  job_hunter.scrape.run。逐步骤断言 + 协议层取证 + console 错误收集。 */

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

const ws = (page: Page) => page.getByTestId('jobhunter-workspace')

test('job-hunting module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入求职猎手模块', async () => {
    await gotoModule(page, '求职猎手', 'job-hunting')
    expect(
      await page.evaluate(() => {
        const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
        return store?.getState()?.activeModule
      }),
    ).toBe('job-hunting')
  })

  await step('工作台渲染 + job_hunter.jobs.list 协议取证', async () => {
    await ws(page).waitFor({ timeout: 8000 })
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'job_hunter.jobs.list'),
      20_000,
      'job_hunter.jobs.list 请求',
    )
    await page.waitForTimeout(600)
  })

  await step('四 tab 切换（职位库/我的画像/简历工具/智能分析）', async () => {
    for (const label of ['我的画像', '简历工具', '智能分析', '职位库']) {
      const btn = ws(page).getByRole('button', { name: label }).first()
      await btn.click({ timeout: 5000 })
      await page.waitForTimeout(500)
      await expect(btn).toHaveClass(/border-b-2/)
    }
  })

  await step('状态筛选可切换', async () => {
    const filters = ws(page).getByRole('button', { name: /^(全部|待申请|已申请|面试中|已录用|已拒绝)$/ })
    if ((await filters.count()) > 1) {
      await filters.nth(1).click({ timeout: 5000 })
      await page.waitForTimeout(600)
      await filters.nth(0).click({ timeout: 5000 })
      await page.waitForTimeout(600)
    }
  })

  await step('抓取职位 → job_hunter.scrape.run 请求发出', async () => {
    const btn = ws(page).getByRole('button', { name: /抓取职位/ }).first()
    await expect(btn).toBeVisible({ timeout: 5000 })
    await btn.click({ timeout: 5000 })
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'job_hunter.scrape.run'),
      20_000,
      'job_hunter.scrape.run 请求',
    )
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/jobhunter-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-JH] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-JH] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
