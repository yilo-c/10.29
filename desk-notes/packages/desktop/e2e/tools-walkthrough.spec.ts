import { test, expect, type Page } from './fixtures'

/** 工具（translate / TabbedModule）深走查（正式回归）：进入 → 五 tab
 *  切换（翻译 / 双端传送 / 信息 / 系统监控 / OCR-转录），逐 tab 断言激活态。
 *  与 l3-effects 的 info 离线降级用例互补（那里测效果，这里测容器切换）。 */

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

test('tools module walkthrough (5 tabs)', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入工具模块', async () => {
    await gotoModule(page, '工具', 'translate')
    expect(
      await page.evaluate(() => {
        const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
        return store?.getState()?.activeModule
      }),
    ).toBe('translate')
  })

  for (const label of ['双端传送', '信息', '系统监控', 'OCR/转录', '翻译']) {
    await step(`切换到「${label}」`, async () => {
      const btn = mc(page).getByRole('button', { name: label }).first()
      await expect(btn).toBeVisible({ timeout: 5000 })
      await btn.click({ timeout: 5000 })
      await page.waitForTimeout(700)
      await expect(btn).toHaveClass(/border-b-2|bg-|text-fluent-blue/)
    })
  }

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/tools-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-TOOLS] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-TOOLS] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
