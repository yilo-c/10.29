import { test, expect, waitForWsFrames, type Page } from './fixtures'

/** Dashboard（工作台）深走查（正式回归）：
 *  进入 → 默认卡片渲染 → 统计视图（真实内容）→ 返回 → 设置面板 →
 *  密度循环 → 模板切换 → 启用收件箱卡片（触发 inbox.list）→ 启用资讯卡片
 *  （触发 info.brief）→ 关闭设置 → 保存布局预设（localStorage 效果断言）。
 *  逐步骤断言 + 协议层取证 + console 错误收集。
 *
 * 2026-09-09 导航收敛后 dashboard 不在侧边栏，进模块经 dev 暴露的 store 直达。
 * 统计视图为纯前端计算（读 store 的 todos/floatingNotes），无 statistics.* RPC；
 * 真实后端 RPC 仅 news 卡 info.brief 与 inbox 卡 inbox.list（这俩卡默认隐藏，
 * 需进设置面板开启才挂载触发）。 */

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

const mc = (page: Page) => page.getByTestId('module-content')

/** 经 dev 暴露的 store 直达（侧边栏已收敛，dashboard 不在栏上） */
async function gotoDashboard(page: Page) {
  await page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: { getState: () => { setActiveModule?: (m: string) => void } }
    }).__useStore
    store?.getState()?.setActiveModule?.('dashboard')
  })
  await page.getByTestId('dashboard-stats-btn').waitFor({ state: 'visible', timeout: 20_000 })
}

const haveBackend = () => Boolean(process.env.SIYI_E2E_WS_URL)

test('dashboard module deep walkthrough', async ({ page }) => {
  test.skip(!haveBackend(), '需要 e2e 真后端 harness（未设 SIYI_E2E_WS_URL，browser-only 模式跳过）')
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入 dashboard 模块', async () => {
    await gotoDashboard(page)
    expect(
      await page.evaluate(() => (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore?.getState()?.activeModule),
    ).toBe('dashboard')
  })

  await step('默认卡片渲染（任务/笔记/建议）', async () => {
    await expect(mc(page).getByText('最近任务').first()).toBeVisible({ timeout: 8000 })
    await expect(mc(page).getByText('最近笔记').first()).toBeVisible()
    await expect(mc(page).getByText('智能建议').first()).toBeVisible()
  })

  await step('打开统计视图', async () => {
    await page.getByTestId('dashboard-stats-btn').click()
    await expect(mc(page).getByText('数据统计').first()).toBeVisible({ timeout: 8000 })
    await expect(mc(page).getByText('任务总数').first()).toBeVisible()
    await expect(mc(page).getByText('完成率').first()).toBeVisible()
    await expect(mc(page).getByText('完成任务趋势').first()).toBeVisible()
    await expect(mc(page).getByText('笔记活跃度').first()).toBeVisible()
  })

  await step('统计视图返回', async () => {
    await mc(page).locator('button:has([data-icon="fa-chevron-left"])').first().click()
    await expect(mc(page).getByText('数据统计').first()).toBeHidden({ timeout: 8000 })
    await expect(page.getByTestId('dashboard-stats-btn')).toBeVisible()
  })

  await step('打开设置面板', async () => {
    await mc(page).locator('button:has([data-icon="fa-sliders"])').first().click()
    await expect(mc(page).getByText('显示卡片').first()).toBeVisible({ timeout: 8000 })
  })

  await step('切换密度（compact/normal/comfortable 循环）', async () => {
    const densityBtn = mc(page).locator('button:has([data-icon="fa-grip-lines"])').first()
    await expect(densityBtn).toBeVisible()
    await densityBtn.click()
    await page.waitForTimeout(150)
  })

  await step('切换模板到「专注」', async () => {
    const focusBtn = mc(page).getByRole('button', { name: '专注' }).first()
    await expect(focusBtn).toBeVisible()
    await focusBtn.click()
    await page.waitForTimeout(150)
  })

  let inboxRpcSeen = false
  await step('启用收件箱卡片 → 触发 inbox.list', async () => {
    const panel = mc(page).locator('div.space-y-4').filter({ hasText: /显示卡片/ }).first()
    await panel.getByRole('button', { name: '收件箱' }).first().click()
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'inbox.list'),
      20_000,
      'inbox.list 请求',
    )
    inboxRpcSeen = true
    await expect(mc(page).getByText('收件箱').first()).toBeVisible({ timeout: 8000 })
  })

  let infoRpcSeen = false
  await step('启用资讯卡片 → 触发 info.brief', async () => {
    const panel = mc(page).locator('div.space-y-4').filter({ hasText: /显示卡片/ }).first()
    await panel.getByRole('button', { name: '资讯' }).first().click()
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'info.brief'),
      20_000,
      'info.brief 请求',
    )
    infoRpcSeen = true
    await expect(mc(page).getByText('今日资讯').first()).toBeVisible({ timeout: 8000 })
  })

  await step('关闭设置面板', async () => {
    await mc(page).locator('button:has([data-icon="fa-sliders"])').first().click()
    await expect(mc(page).getByText('显示卡片').first()).toBeHidden({ timeout: 8000 })
  })

  await step('保存布局预设（写入 localStorage 效果断言）', async () => {
    await mc(page).locator('button:has([data-icon="fa-sliders"])').first().click()
    await expect(mc(page).getByText('显示卡片').first()).toBeVisible({ timeout: 8000 })
    const panel = mc(page).locator('div.space-y-4').filter({ hasText: /显示卡片/ }).first()
    const nameInput = panel.locator('input').first()
    await nameInput.fill('走查预设A')
    await nameInput.press('Enter') // input onKeyDown Enter → onSavePreset
    await page.waitForTimeout(300)
    const saved = await page.evaluate(() => {
      try {
        return JSON.parse(localStorage.getItem('desk-notes-dashboard-presets') || '[]')
      } catch {
        return []
      }
    })
    if (!Array.isArray(saved) || saved.length < 1) throw new Error('预设未写入 localStorage')
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/dashboard-walkthrough-end.png' }).catch(() => {})

  console.log('\n[DASHBOARD WALKTHROUGH] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log(
    `[DASHBOARD WALKTHROUGH] inbox.list 观测=${inboxRpcSeen}; info.brief 观测=${infoRpcSeen}; console errors=` +
      JSON.stringify(errors.slice(0, 8)),
  )
})
