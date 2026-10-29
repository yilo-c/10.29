import { test, expect, waitForWsFrames, type Page } from './fixtures'

/** 知识网络模块深走查（正式回归）：进入 → 默认关系图谱 → 视图切换三态
 *  （关系图谱 / 思维导图 / 知识卡片）→ 常显标签开关 → 切回，逐步骤断言
 *  ＋协议层取证 ＋ console 错误收集。
 *  与既有 knowledge-graph.spec.ts（journey，只验证请求发生+按钮可见）互补：
 *  本走查额外锁死「切换后目标视图真正激活」这一效果断言。 */

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
  await page.waitForTimeout(1000)
}

const mc = (page: Page) => page.getByTestId('module-content')

/** 断言某视图按钮处于激活态（激活 class = bg-fluent-blue/15） */
async function expectViewActive(page: Page, label: string, active: boolean) {
  const btn = mc(page).getByRole('button', { name: label }).first()
  if (active) await expect(btn).toHaveClass(/bg-fluent-blue/)
  else await expect(btn).not.toHaveClass(/bg-fluent-blue/)
}

test('knowledge-graph module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入知识网络模块', async () => {
    await gotoModule(page, '知识网络', 'knowledge-graph')
    expect(await page.evaluate(() => {
      const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
      return store?.getState()?.activeModule
    })).toBe('knowledge-graph')
    // 协议层取证：确实向后端请求了 knowledge.graph
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'knowledge.graph'),
      20_000,
      'knowledge.graph 请求',
    )
  })

  await step('默认激活关系图谱', async () => {
    await expectViewActive(page, '关系图谱', true)
  })

  await step('切换到思维导图 → 该视图激活', async () => {
    await mc(page).getByRole('button', { name: '思维导图' }).first().click({ timeout: 5000 })
    await page.waitForTimeout(700)
    await expectViewActive(page, '思维导图', true)
    await expectViewActive(page, '关系图谱', false)
  })

  await step('切换到知识卡片 → 激活且拉取 knowledge.cards', async () => {
    await mc(page).getByRole('button', { name: '知识卡片' }).first().click({ timeout: 5000 })
    await page.waitForTimeout(700)
    await expectViewActive(page, '知识卡片', true)
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'knowledge.cards'),
      20_000,
      'knowledge.cards 请求',
    )
  })

  await step('切回关系图谱', async () => {
    await mc(page).getByRole('button', { name: '关系图谱' }).first().click({ timeout: 5000 })
    await page.waitForTimeout(700)
    await expectViewActive(page, '关系图谱', true)
    await expectViewActive(page, '知识卡片', false)
  })

  await step('常显标签开关可切换（graph 视图下）', async () => {
    const btn = mc(page).getByRole('button', { name: '常显标签' }).first()
    await expect(btn).toBeVisible()
    const before = await btn.getAttribute('class')
    await btn.click({ timeout: 5000 })
    await page.waitForTimeout(500)
    const after = await btn.getAttribute('class')
    if (before === after) throw new Error('常显标签点击后 class 未变化')
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/knowledge-graph-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-KG] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-KG] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
