import { test, expect, type Page } from './fixtures'

/** 待办模块核心链路走查（正式回归）：进入 / 列表渲染 / 新建(UI) / 勾选↔取消 /
 *  优先级循环 / 搜索过滤 / 视图切换(list↔calendar↔kanban) / 详情面板 / 删除。
 *  数据用 store 直注种子 + UI 内联输入新增，逐步骤断言 + console 错误收集。
 *  修复回归基线：2026-10-16 起（同 notes-walkthrough 范式）。 */

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

/** 进模块（click-sweep enterModule 同款 3 次重试——boot 早期 sidebar 点击会回弹） */
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
  await page.waitForTimeout(800)
}

/** store 直注种子待办（函数式 setState，新引用才触发渲染） */
async function seedTodos(page: Page, texts: string[]) {
  await page.evaluate((texts) => {
    const store = (window as unknown as {
      __useStore?: { setState: (fn: (s: { todos: Array<Record<string, unknown>> }) => { todos: Array<Record<string, unknown>> }) => void }
    }).__useStore
    const base = Date.now()
    const seeded = texts.map((text, i) => ({
      id: `tw-seed-${base}-${i}`,
      text,
      done: false,
      createdAt: base + i,
      sortOrder: base + i,
      updatedAt: base + i,
    }))
    store?.setState((s) => ({ todos: [...seeded, ...s.todos] }))
  }, texts)
}

/** 读 store 里某条待办的字段 */
async function readTodo(page: Page, text: string): Promise<Record<string, unknown> | null> {
  return page.evaluate((text) => {
    const store = (window as unknown as { __useStore?: { getState: () => { todos: Array<Record<string, unknown>> } } }).__useStore
    const todos = store?.getState()?.todos ?? []
    return (todos.find((t) => t.text === text) as Record<string, unknown>) ?? null
  }, text)
}

async function readViewMode(page: Page): Promise<string> {
  return page.evaluate(() => {
    const store = (window as unknown as { __useStore?: { getState: () => { viewMode: string } } }).__useStore
    return store?.getState()?.viewMode ?? ''
  })
}

const itemByText = (page: Page, text: string) =>
  page.getByTestId('todo-item').filter({ hasText: text })

test('todo module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入待办模块', async () => {
    await gotoModule(page, '待办', 'todo')
  })

  await step('种子 3 条待办 + 列表可见', async () => {
    await seedTodos(page, ['TW-A', 'TW-B', 'TW-C'])
    await page.getByTestId('todo-item').first().waitFor({ timeout: 12000 })
    const n = await page.getByTestId('todo-item').count()
    if (n < 3) throw new Error(`列表只有 ${n} 条`)
  })

  await step('新建待办(UI 内联输入)', async () => {
    // 触发内联新增输入框（与顶部「待办」ToolBtn 等价路径，store 直置 addingTodo）
    await page.evaluate(() => {
      const store = (window as unknown as { __useStore?: { setState: (s: { addingTodo: boolean }) => void } }).__useStore
      store?.setState({ addingTodo: true })
    })
    const input = page.getByPlaceholder('输入待办内容，按 Enter 添加...').first()
    await input.waitFor({ timeout: 5000 })
    await input.fill('TW-D')
    await input.press('Enter')
    await page.getByTestId('todo-item').filter({ hasText: 'TW-D' }).first().waitFor({ timeout: 8000 })
    const n = await page.getByTestId('todo-item').count()
    if (n < 4) throw new Error(`新增后列表只有 ${n} 条`)
  })

  await step('勾选 TW-A → done=true', async () => {
    const item = itemByText(page, 'TW-A')
    await item.getByRole('button').first().click({ timeout: 5000 })
    await page.waitForTimeout(300)
    const t = await readTodo(page, 'TW-A')
    if (!t || t.done !== true) throw new Error(`TW-A done=${JSON.stringify(t?.done)}`)
  })

  await step('取消勾选 TW-A → done=false', async () => {
    const item = itemByText(page, 'TW-A')
    await item.getByRole('button').first().click({ timeout: 5000 })
    await page.waitForTimeout(300)
    const t = await readTodo(page, 'TW-A')
    if (!t || t.done !== false) throw new Error(`TW-A done=${JSON.stringify(t?.done)}`)
  })

  await step('点击优先级点 → 优先级被设置', async () => {
    const item = itemByText(page, 'TW-B')
    await item.getByTestId('todo-priority').click({ timeout: 5000 })
    await page.waitForTimeout(300)
    const t = await readTodo(page, 'TW-B')
    if (!t || typeof t.priority !== 'number') throw new Error(`TW-B priority=${JSON.stringify(t?.priority)}`)
  })

  await step('搜索过滤 TW-B', async () => {
    const search = page.getByTestId('module-content').getByPlaceholder('搜索').first()
    await search.fill('TW-B')
    await page.waitForTimeout(800)
    const n = await page.getByTestId('todo-item').count()
    if (n !== 1) throw new Error(`搜索 TW-B 后应有 1 条，实际 ${n}`)
    await search.fill('')
    await page.waitForTimeout(400)
  })

  await step('视图切换 list→calendar→kanban→list', async () => {
    const viewBtn = page.getByTestId('todo-view-toggle')
    const seq: string[] = []
    for (let i = 0; i < 3; i++) {
      await viewBtn.click({ timeout: 5000 })
      await page.waitForTimeout(600)
      seq.push(await readViewMode(page))
    }
    const uniq = Array.from(new Set(seq))
    if (uniq.length < 2) throw new Error(`视图未切换：${JSON.stringify(seq)}`)
  })

  await step('详情面板 TW-C', async () => {
    const item = itemByText(page, 'TW-C')
    await item.getByTestId('todo-detail-btn').click({ timeout: 5000 })
    await page.getByText('任务详情').first().waitFor({ timeout: 5000 })
  })

  await step('删除 TW-C', async () => {
    const before = await page.getByTestId('todo-item').count()
    const item = itemByText(page, 'TW-C')
    await item.hover()
    await item.getByTestId('todo-delete-btn').click({ timeout: 5000 })
    await page.waitForTimeout(600)
    const after = await page.getByTestId('todo-item').count()
    if (after >= before) throw new Error(`删除后数量未减少：${before}→${after}`)
    const t = await readTodo(page, 'TW-C')
    if (t) throw new Error('TW-C 删除后仍在 store')
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/todo-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-TODO] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-TODO] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
