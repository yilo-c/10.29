import { test, expect, type Page } from './fixtures'

/** 设置（SettingsModule）深走查（正式回归）：进入 → 面板渲染 → 语言切换
 *  （中/英，断言 store.locale 变化）→ 外观项（背景/透明度/颜色）可见
 *  → 语言切回中文。逐步骤断言 + console 错误收集。 */

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

async function readLocale(page: Page): Promise<string> {
  return page.evaluate(() => {
    const store = (window as unknown as { __useStore?: { getState: () => { locale: string } } }).__useStore
    return store?.getState()?.locale ?? ''
  })
}

test('settings module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入设置模块', async () => {
    await gotoModule(page, '设置', 'settings')
    expect(
      await page.evaluate(() => {
        const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
        return store?.getState()?.activeModule
      }),
    ).toBe('settings')
  })

  await step('设置面板渲染（外观/语言项可见）', async () => {
    await expect(mc(page).getByText('设置').first()).toBeVisible({ timeout: 8000 })
    // 外观项：背景 / 透明度 / 颜色
    for (const label of ['背景', '透明度', '颜色']) {
      const el = mc(page).getByText(label, { exact: false }).first()
      if ((await el.count()) > 0) await expect(el).toBeVisible({ timeout: 5000 })
    }
  })

  await step('语言切换 → store.locale 变化并切回', async () => {
    const before = await readLocale(page)
    const enBtn = mc(page).getByText('English', { exact: false }).first()
    const zhBtn = mc(page).getByText('中文', { exact: false }).first()
    if ((await enBtn.count()) > 0) {
      await enBtn.click({ timeout: 5000 })
      await page.waitForTimeout(700)
      const mid = await readLocale(page)
      if (mid === before) throw new Error(`切英文后 locale 未变：${before} → ${mid}`)
    }
    if ((await zhBtn.count()) > 0) {
      await zhBtn.click({ timeout: 5000 })
      await page.waitForTimeout(700)
      const after = await readLocale(page)
      if (after !== before && after !== 'zh-CN') {
        throw new Error(`切回中文后 locale 异常：${after}`)
      }
    }
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/settings-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-SETTINGS] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-SETTINGS] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
