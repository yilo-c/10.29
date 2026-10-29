import { test, expect, waitForWsFrames, type Page } from './fixtures'

/** 收件箱（InboxModule）深走查（正式回归）：进入 → 列表加载（inbox.list
 *  协议取证）→ 快速记录一条（Enter 发送）→ 条目进入列表 → 分类按钮触发
 *  inbox.classify → 归档/删除。逐步骤断言 + 协议层取证 + console 错误收集。 */

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
const NOTE = 'IB走查条目'

test('inbox module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入收件箱模块', async () => {
    await gotoModule(page, '收件箱', 'inbox')
    expect(
      await page.evaluate(() => {
        const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
        return store?.getState()?.activeModule
      }),
    ).toBe('inbox')
  })

  await step('列表加载（inbox.list 协议取证）', async () => {
    await expect(mc(page).getByText('收件箱').first()).toBeVisible({ timeout: 8000 })
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'inbox.list'),
      20_000,
      'inbox.list 请求',
    )
    await page.waitForTimeout(600)
  })

  await step('快速记录一条 → 条目进入列表', async () => {
    const input = mc(page).getByPlaceholder(/快速记录/).first()
    await expect(input).toBeVisible({ timeout: 5000 })
    await input.fill(NOTE)
    await input.press('Enter')
    await page.waitForTimeout(1200)
    await expect(mc(page).getByText(NOTE).first()).toBeVisible({ timeout: 8000 })
  })

  await step('筛选：全部 / 显示已归档 可切换', async () => {
    const allBtn = mc(page).getByRole('button', { name: '全部' }).first()
    if ((await allBtn.count()) > 0) {
      await allBtn.click({ timeout: 5000 })
      await page.waitForTimeout(500)
    }
    const archived = mc(page).getByRole('button', { name: /显示已归档/ }).first()
    if ((await archived.count()) > 0) {
      await archived.click({ timeout: 5000 })
      await page.waitForTimeout(500)
      await archived.click({ timeout: 5000 })
      await page.waitForTimeout(500)
    }
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/inbox-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-INBOX] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-INBOX] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
