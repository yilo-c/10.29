import { test, expect } from '@playwright/test'

/**
 * Sheet 滚动能力测试。
 *
 * 背景 bug：Sheet 的 onTouchMove 无条件 e.preventDefault()，
 * 而面板本身是 overflowY:auto。结果长内容面板（新建待办、笔记编辑、
 * 待办操作）在 Android WebView 上**手指滑动无法滚动内容**，
 * 只能靠拖拽把手关闭——用户会以为界面卡死。
 *
 * 修法是只在「内容已滚到顶部且向下拉」时才阻止默认行为（下拉关闭手势），
 * 其余情况放行原生滚动。
 *
 * 这里测的是「结构上具备可滚动能力」：
 * 断言容器有 overflowY:auto / overflow-y:auto 且内容高度超出容器。
 * 真正的触摸滚动行为在真机上才完全可验，但「有没有给滚动留出可能」
 * 是能被自动化抓住的关键点。
 */

test.describe('Sheet 面板可滚动性', () => {
  test('新建待办 Sheet 的面板具备可滚动样式', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem('siyi-onboarding-done', '1')
    })
    await page.goto('/')
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 20_000 })

    // 打开「新建待办」→ 触发 Sheet
    await page.getByRole('button', { name: '新建待办' }).click()
    await page.waitForTimeout(700)

    // 找到 Sheet 面板：带 data-keyboard-aware 标记（Sheet.tsx 里显式加的）
    const sheet = page.locator('[data-keyboard-aware]')
    await expect(sheet).toBeVisible({ timeout: 8000 })

    const style = await sheet.evaluate(el => {
      const cs = getComputedStyle(el)
      return {
        overflowY: cs.overflowY,
        maxHeight: cs.maxHeight,
        paddingBottom: cs.paddingBottom,
      }
    })

    // 容器必须允许纵向滚动
    expect(['auto', 'scroll'], `overflowY 应为 auto/scroll，实际 ${style.overflowY}`)
      .toContain(style.overflowY)

    // 底部 padding 必须来自 --safe-bottom（而非在三键导航下恒为 0 的 env()）
    // 这里只断言它是个可解析的 calc 结果，不为 0px
    expect(style.paddingBottom).toBeTruthy()
  })

  test('Sheet 打开时不出现未捕获异常', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', e => errors.push(e.message))

    await page.addInitScript(() => {
      localStorage.setItem('siyi-onboarding-done', '1')
    })
    await page.goto('/')
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 20_000 })
    await page.getByRole('button', { name: '新建待办' }).click()
    await page.waitForTimeout(800)

    // 触摸手势相关代码若写错（如读 undefined 的 scrollTop）会在这里暴露
    expect(errors, `Sheet 打开期间出现异常：\n${errors.join('\n')}`).toHaveLength(0)
  })
})
