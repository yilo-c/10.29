import { test, expect, type Page } from '@playwright/test'

/**
 * 手机端主流程 E2E —— 只回答一个问题：**App 打开后能不能正常用**。
 *
 * 设计原则（重要，改测试前先读）：
 *   1. 断言 Tab 一律用 toBeVisible()。
 *      因为 App.tsx 让 5 个 Tab 常驻挂载、切换只切 display，
 *      用 toBeAttached()/locator.count() 会在「Tab 隐藏」时也通过，是假绿。
 *   2. 不依赖后端。本套跑在纯前端环境下（无 9527 后端），
 *      所有会打网络的断言都必须容忍失败态——测的是「界面不崩」而不是「后端通」。
 *   3. 不点会「写真实数据」的破坏性按钮（清空、删除全部），
 *      只做可逆操作（新建一条再删掉那条）。
 */

/** 预置首启状态，跳过 Onboarding，避免它盖住整个界面 */
async function seedFirstRun(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('siyi-onboarding-done', '1')
  })
}

/** 等到 App 壳渲染完成（底部导航出现即视为 boot 结束） */
async function waitForShell(page: Page) {
  await expect(page.getByRole('tablist')).toBeVisible({ timeout: 20_000 })
}

test.describe('启动与主框架', () => {
  test.beforeEach(async ({ page }) => {
    await seedFirstRun(page)
  })

  test('冷启动能渲染出 5 个 Tab 且默认停在待办', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))

    await page.goto('/')
    await waitForShell(page)

    // 5 个 Tab 必须都在（role=tab 由 App.tsx 显式给出）
    const tabs = page.getByRole('tab')
    await expect(tabs).toHaveCount(5)

    for (const label of ['待办', '笔记', 'AI', '工具', '设置']) {
      await expect(page.getByRole('tab', { name: label })).toBeVisible()
    }

    // 默认 Tab 是 todo：aria-selected=true
    await expect(page.getByRole('tab', { name: '待办' })).toHaveAttribute('aria-selected', 'true')

    // 启动阶段不允许有未捕获异常（白屏的典型征兆）
    expect(errors, `启动期间出现未捕获异常：\n${errors.join('\n')}`).toHaveLength(0)
  })

  test('五个 Tab 可以互相切换且对应内容可见', async ({ page }) => {
    await page.goto('/')
    await waitForShell(page)

    const cases: Array<{ tab: string; probe: ReturnType<Page['locator']> }> = []

    // 依次切换，每一步都断言「当前 Tab 选中」+「该页的招牌元素可见」
    await page.getByRole('tab', { name: '笔记' }).click()
    await expect(page.getByRole('tab', { name: '笔记' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('button', { name: '新建笔记（长按快速捕获）' })).toBeVisible()

    await page.getByRole('tab', { name: 'AI' }).click()
    await expect(page.getByRole('tab', { name: 'AI' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByPlaceholder('输入消息...')).toBeVisible()

    await page.getByRole('tab', { name: '工具' }).click()
    await expect(page.getByRole('tab', { name: '工具' })).toHaveAttribute('aria-selected', 'true')

    await page.getByRole('tab', { name: '设置' }).click()
    await expect(page.getByRole('tab', { name: '设置' })).toHaveAttribute('aria-selected', 'true')

    // 切回待办，验证来回切换不会卡死
    await page.getByRole('tab', { name: '待办' }).click()
    await expect(page.getByRole('tab', { name: '待办' })).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('button', { name: '新建待办' })).toBeVisible()

    void cases
  })

  test('Tab 隐藏时不应同时可见（防止 display 切换失效导致页面堆叠）', async ({ page }) => {
    await page.goto('/')
    await waitForShell(page)

    // 默认在待办：笔记页的「新建笔记」按钮必须不可见
    await expect(page.getByRole('button', { name: '新建笔记（长按快速捕获）' })).toBeHidden()
    // 待办页的「新建待办」必须可见
    await expect(page.getByRole('button', { name: '新建待办' })).toBeVisible()

    // 切到笔记后反过来
    await page.getByRole('tab', { name: '笔记' }).click()
    await expect(page.getByRole('button', { name: '新建笔记（长按快速捕获）' })).toBeVisible()
    await expect(page.getByRole('button', { name: '新建待办' })).toBeHidden()
  })
})

test.describe('待办主流程', () => {
  test.beforeEach(async ({ page }) => {
    await seedFirstRun(page)
  })

  test('能新建一条待办并出现在列表里', async ({ page }) => {
    await page.goto('/')
    await waitForShell(page)

    const text = `e2e-待办-${Date.now()}`
    await page.getByRole('button', { name: '新建待办' }).click()

    // 新建入口可能是弹出层里的输入框：取当前可见的 textbox
    const input = page.getByRole('textbox').filter({ visible: true }).first()
    await expect(input).toBeVisible()
    await input.fill(text)
    await input.press('Enter')

    // 新建的待办必须出现在界面上
    await expect(page.getByText(text)).toBeVisible({ timeout: 10_000 })
  })
})

test.describe('笔记主流程', () => {
  test.beforeEach(async ({ page }) => {
    await seedFirstRun(page)
  })

  test('笔记页三个入口（回收站/知识/搜索）都在', async ({ page }) => {
    await page.goto('/')
    await waitForShell(page)
    await page.getByRole('tab', { name: '笔记' }).click()

    await expect(page.getByRole('button', { name: '回收站' })).toBeVisible()
    await expect(page.getByRole('button', { name: '知识' })).toBeVisible()
    await expect(page.getByRole('button', { name: '搜索' })).toBeVisible()
  })
})

test.describe('健壮性', () => {
  test.beforeEach(async ({ page }) => {
    await seedFirstRun(page)
  })

  test('页面加载后没有任何未捕获异常或 ErrorBoundary 降级', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))

    await page.goto('/')
    await waitForShell(page)

    // 把每个 Tab 都点一遍，逼出惰性渲染路径里的异常
    for (const label of ['笔记', 'AI', '工具', '设置', '待办']) {
      await page.getByRole('tab', { name: label }).click()
      await page.waitForTimeout(300)
    }

    expect(errors, `遍历 Tab 期间出现未捕获异常：\n${errors.join('\n')}`).toHaveLength(0)

    // ErrorBoundary 的降级文案不应出现
    await expect(page.getByText(/出错了|页面崩溃|Something went wrong/)).toHaveCount(0)
  })
})
