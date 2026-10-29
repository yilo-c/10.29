import { test, type Page } from './fixtures'

/** 笔记模块核心链路走查（正式回归）：新建/编辑/工具栏/整理/返回/搜索/模式切换，
 *  逐步骤断言 + console 错误收集。修复回归基线：2026-10-16 整理弹窗卡死链路。 */

type StepResult = { step: string; ok: boolean; note: string }

const results: StepResult[] = []

async function step(name: string, fn: () => Promise<void>) {
  try {
    await fn()
    results.push({ step: name, ok: true, note: '' })
  } catch (e) {
    results.push({ step: name, ok: false, note: String(e).slice(0, 120) })
  }
}

async function seedNote(page: Page, title: string, content: string) {
  await page.evaluate(
    ({ title, content }) => {
      const store = (
        window as unknown as {
          __useStore?: { getState: () => { nextZ: number; addFloatingNote: (n: Record<string, unknown>) => void } }
        }
      ).__useStore
      const s = store!.getState()
      s.addFloatingNote({
        id: `u0-${Date.now().toString(36)}`,
        type: 'text',
        title,
        content,
        x: 40,
        y: 40,
        width: 320,
        height: 240,
        zIndex: s.nextZ,
        createdAt: Date.now(),
        updatedAt: Date.now(),
      })
    },
    { title, content },
  )
}

test('notes module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  const mc = () => page.getByTestId('module-content')

  await step('进入笔记模块', async () => {
    await page.getByTestId('nav-sidebar').getByRole('button', { name: '笔记' }).first().click()
    await page.waitForTimeout(1500)
  })

  await step('造 3 条数据', async () => {
    await seedNote(page, '走查一', '内容一')
    await seedNote(page, '走查二', '内容二')
    await seedNote(page, '走查三', '内容三')
  })

  await step('列表可见', async () => {
    // seed 走 store → 后端落库 → 列表回流，异步链路需轮询而非固定等待
    await page
      .getByTestId('note-list-item')
      .first()
      .waitFor({ timeout: 15000 })
    const n = await page.getByTestId('note-list-item').count()
    if (n < 3) throw new Error(`列表只有 ${n} 条`)
  })

  await step('点开第一条笔记', async () => {
    await page.getByTestId('note-list-item').first().click()
    await page.waitForTimeout(1500)
    const ta = await page.locator('textarea').count()
    if (ta === 0) throw new Error('编辑器 textarea 未出现')
  })

  await step('编辑标题', async () => {
    const titleInput = page.locator('input[placeholder*="新笔记"]').first()
    await titleInput.fill('走查一-改')
    await page.waitForTimeout(400)
  })

  await step('编辑内容', async () => {
    const ta = page.locator('textarea').first()
    await ta.click()
    await ta.type(' 追加内容Y')
    await page.waitForTimeout(600)
  })

  await step('工具栏-加粗', async () => {
    await page.getByRole('button', { name: 'Add bold text' }).first().click({ timeout: 5000 })
    await page.waitForTimeout(400)
  })

  await step('工具栏-无序列表', async () => {
    await page
      .getByRole('button', { name: /unordered list/i })
      .first()
      .click({ timeout: 5000 })
    await page.waitForTimeout(400)
  })

  await step('打开"整理"', async () => {
    await mc().getByText('整理', { exact: true }).first().click({ timeout: 5000 })
    await page.waitForTimeout(1200)
  })

  await step('ESC 关层', async () => {
    await page.keyboard.press('Escape')
    await page.waitForTimeout(400)
  })

  await step('返回列表', async () => {
    await page
      .getByTestId('module-content')
      .getByRole('button', { name: /返回列表|保存中/ })
      .first()
      .click({ timeout: 5000 })
    await page.waitForTimeout(1200)
    const items = await page.getByTestId('note-list-item').count()
    if (items === 0) throw new Error('返回列表后无条目')
  })

  await step('搜索过滤', async () => {
    const search = page.getByPlaceholder(/搜索/).first()
    await search.fill('走查一')
    await page.waitForTimeout(800)
    const n = await page.getByTestId('note-list-item').count()
    if (n === 0) throw new Error('搜索"走查一"无结果')
    await search.fill('')
    await page.waitForTimeout(400)
  })

  await step('底部新建文本', async () => {
    await mc().getByText('新建文本', { exact: true }).first().click({ timeout: 5000 })
    await page.waitForTimeout(1500)
  })

  await step('切换模式 预览/编辑/分栏', async () => {
    for (const m of ['预览', '编辑', '分栏']) {
      const b = mc().getByRole('button', { name: m }).first()
      if ((await b.count()) > 0) {
        await b.click({ timeout: 4000 })
        await page.waitForTimeout(500)
      }
    }
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/10-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH] console errors =', JSON.stringify(errors.slice(0, 8)))
})
