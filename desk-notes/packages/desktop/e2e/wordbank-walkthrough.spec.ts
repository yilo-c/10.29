import { test, expect, waitForWsFrames, type Page } from './fixtures'

/** 学习（wordbank）模块深走查（正式回归）：进入 → 新建单词本 → 新 tab 出现 →
 *  添加单词 → 单词入列表 → 搜索过滤 → 开始复习（挂载复习视图 + word.list_due
 *  协议取证）→ 退出复习。逐步骤断言 + console 错误收集。 */

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

const BOOK = 'WB走查本'
const WORD = 'walkthroughly'
const MEAN = '走查造词义'

/** 经页面内 ws 直连后端调 RPC，返回完整 JSON-RPC 信封（便于 error 诊断） */
async function rpc<T = unknown>(
  page: Page,
  method: string,
  params: Record<string, unknown>,
): Promise<{ result?: T; error?: unknown }> {
  return page.evaluate(
    ([method, params]) => {
      const url =
        (window as unknown as Record<string, unknown>).__SIYI_WS_URL__ || 'ws://127.0.0.1:18765/ws'
      return new Promise((resolve) => {
        const ws = new WebSocket(url as string)
        const timer = setTimeout(() => {
          try { ws.close() } catch { /* noop */ }
          resolve({ error: 'rpc timeout' })
        }, 15000)
        ws.onopen = () => {
          ws.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }))
        }
        ws.onmessage = (ev) => {
          const d = JSON.parse(ev.data as string) as { id?: number; result?: T; error?: unknown }
          // 后端会先推 vocabulary.word_added 等无 id 的推送事件，必须按 id 过滤，
          // 否则会把事件误当 RPC 响应并提前关闭连接。
          if (d.id !== 1) return
          clearTimeout(timer)
          try { ws.close() } catch { /* noop */ }
          resolve(d)
        }
        ws.onerror = () => {
          clearTimeout(timer)
          resolve({ error: 'ws error' })
        }
      })
    },
    [method, params],
  ) as Promise<{ result?: T; error?: unknown }>
}

test('wordbank module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  let bookId = 0
  let wordId = 0

  await step('进入学习模块', async () => {
    await gotoModule(page, '学习', 'wordbank')
    expect(
      await page.evaluate(() => {
        const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
        return store?.getState()?.activeModule
      }),
    ).toBe('wordbank')
  })

  await step('新建单词本 → 新 tab 出现', async () => {
    await mc(page).getByTitle('新建单词本').click({ timeout: 5000 })
    const input = mc(page).getByPlaceholder('单词本名称').first()
    await input.waitFor({ timeout: 5000 })
    await input.fill(BOOK)
    await input.press('Enter')
    await page.waitForTimeout(900)
    const tab = mc(page).getByRole('button', { name: BOOK }).first()
    await expect(tab).toBeVisible({ timeout: 8000 })
    // 协议层取证：词本确实落到后端
    const r = await rpc<Array<{ id: number; name: string }>>(page, 'wordbook.list', {})
    const list = r.result ?? []
    const hit = list.find((b) => b.name === BOOK)
    if (!hit) throw new Error(`wordbook.list 未含「${BOOK}」，信封=${JSON.stringify(r)}`)
    bookId = hit.id
  })

  await step('添加单词 → 进入列表', async () => {
    // 走真实表单路径（RPC 直连后端不会触发前端重载，列表不会刷新）
    const addBtn = mc(page).getByRole('button', { name: /添加单词/ }).first()
    await addBtn.click({ timeout: 5000 })
    await page.waitForTimeout(400)
    const wordInput = mc(page).getByPlaceholder('输入单词').first()
    await wordInput.waitFor({ timeout: 5000 })
    await wordInput.fill(WORD)
    const meanInput = mc(page).getByPlaceholder('输入释义').first()
    await meanInput.fill(MEAN)
    await mc(page).getByRole('button', { name: /^保存$/ }).first().click({ timeout: 5000 })
    await page.waitForTimeout(1000)
    await expect(mc(page).getByText(WORD, { exact: false }).first()).toBeVisible({ timeout: 8000 })
    // 协议层取证：单词确实落到后端（先取全量，再按词定位；若全量也没有则失败）
    const r2 = await rpc<Array<{ id?: number; word?: string }>>(page, 'word.list', {})
    const all = r2.result ?? []
    const hit = all.find((w) => w.word === WORD)
    if (!hit) throw new Error(`word.list 全量未含「${WORD}」，信封=${JSON.stringify(r2).slice(0, 200)}`)
    wordId = hit.id ?? 0
  })

  await step('搜索过滤', async () => {
    const search = mc(page).getByPlaceholder('搜索单词...').first()
    await search.fill(WORD)
    await page.waitForTimeout(800)
    await expect(mc(page).getByText(WORD, { exact: false }).first()).toBeVisible({ timeout: 5000 })
    await search.fill('')
    await page.waitForTimeout(400)
  })

  await step('开始复习 → 复习视图挂载 + word.list_due', async () => {
    await mc(page).getByRole('button', { name: /复习/ }).first().click({ timeout: 5000 })
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'word.list_due'),
      20_000,
      'word.list_due 请求',
    )
    await page.waitForTimeout(700)
  })

  await step('退出复习 → 回到列表', async () => {
    const exit = mc(page).getByRole('button', { name: /退出/ }).first()
    await exit.click({ timeout: 5000 })
    await page.waitForTimeout(800)
    await expect(mc(page).getByText(WORD, { exact: false }).first()).toBeVisible({ timeout: 8000 })
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/wordbank-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-WB] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-WB] bookId =', bookId, 'wordId =', wordId)
  console.log('[WALKTHROUGH-WB] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
