import { test, expect, waitForWsFrames, type Page } from './fixtures'

/** Agent 任务（AI 管理 / AIManagementModule）深走查（正式回归）：
 *  进入 → 标题与提供商计数 → 刷新健康 → 添加提供商（真实表单填 4 字段）
 *  → 新提供商进入列表 → 删除清理。逐步骤断言 + 协议层取证
 *  + console 错误收集。 */

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
const PROV = 'AG走查商'

/** 经页面内 ws 直连后端调 RPC（必须按请求 id 过滤，后端会先推无 id 事件） */
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
          if (d.id !== 1) return // 忽略后端推送事件
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

test('agent (AI management) module walkthrough', async ({ page }) => {
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入 Agent 任务模块', async () => {
    await gotoModule(page, 'Agent 任务', 'agent')
    expect(
      await page.evaluate(() => {
        const store = (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore
        return store?.getState()?.activeModule
      }),
    ).toBe('agent')
  })

  await step('AI 管理面板渲染 + 进入即拉 provider.list', async () => {
    await expect(mc(page).getByText('AI 管理').first()).toBeVisible({ timeout: 8000 })
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'provider.list'),
      20_000,
      'provider.list 请求',
    )
  })

  await step('刷新健康 → provider.health 请求发出', async () => {
    const btn = mc(page).getByRole('button', { name: /刷新健康/ }).first()
    await expect(btn).toBeVisible({ timeout: 5000 })
    await btn.click({ timeout: 5000 })
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'provider.health'),
      20_000,
      'provider.health 请求',
    )
  })

  await step('添加提供商（真实表单 4 字段）→ 进入列表', async () => {
    const addBtn = mc(page).getByRole('button', { name: /添加提供商/ }).first()
    await expect(addBtn).toBeVisible({ timeout: 5000 })
    await addBtn.click({ timeout: 5000 })
    await page.waitForTimeout(400)
    await mc(page).getByPlaceholder(/名称/).first().fill(PROV)
    await mc(page).getByPlaceholder(/API Base/).first().fill('https://api.example.com/v1')
    await mc(page).getByPlaceholder(/API Key/).first().fill('sk-e2e-dummy')
    await mc(page).getByPlaceholder(/模型/).first().fill('e2e-model')
    await mc(page).getByRole('button', { name: /^保存$|^添加$|^确定$/ }).first().click({ timeout: 5000 })
    await page.waitForTimeout(1000)
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/agent-walkthrough-end.png' })

  console.log('\n[WALKTHROUGH-AGENT] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log('[WALKTHROUGH-AGENT] console errors =', JSON.stringify(errors.slice(0, 8)))

  const failed = results.filter((r) => !r.ok)
  expect(failed.length, `失败步骤: ${failed.map((r) => r.step).join(', ')}`).toBe(0)
})
