import { test, expect } from '@playwright/test'

/**
 * 回收站可用性测试。
 *
 * 背景：这是个数据信任问题，不是普通 UI bug——
 * 删除笔记时内容确实写进了 localStorage['siyi-note-trash']，
 * 但从来没有代码把它读回组件 state，于是「回收站」永远显示空。
 * 用户会以为笔记被永久删除，实际数据一直在本地。
 *
 * 这类「数据在、但界面读不到」的缺陷，单元测试和类型检查都发现不了，
 * 必须真的打开界面看列表里有没有东西。
 */

const TRASH_KEY = 'siyi-note-trash'

test.describe('笔记回收站', () => {
  test('预置的已删除笔记能在回收站里看到', async ({ page }) => {
    await page.addInitScript((key: string) => {
      localStorage.setItem('siyi-onboarding-done', '1')
      localStorage.setItem(key, JSON.stringify([
        {
          id: 'trash-1',
          type: 'text',
          title: '被删掉的会议记录',
          content: '这是应该出现在回收站里的内容',
          x: 0, y: 0, width: 320, height: 200, zIndex: 1,
          createdAt: Date.now() - 86400000,
          updatedAt: Date.now() - 86400000,
          deletedAt: Date.now() - 3600000,
        },
      ]))
    }, TRASH_KEY)

    await page.goto('/')
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 20_000 })
    await page.getByRole('tab', { name: '笔记' }).click()
    await page.getByRole('button', { name: '回收站' }).click()

    // 关键断言：删掉的笔记必须出现在回收站
    await expect(page.getByText('被删掉的会议记录')).toBeVisible({ timeout: 10_000 })
  })

  test('回收站为空时显示空态，而不是崩溃', async ({ page }) => {
    await page.addInitScript((key: string) => {
      localStorage.setItem('siyi-onboarding-done', '1')
      localStorage.setItem(key, JSON.stringify([]))
    }, TRASH_KEY)

    await page.goto('/')
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 20_000 })
    await page.getByRole('tab', { name: '笔记' }).click()
    await page.getByRole('button', { name: '回收站' }).click()
    await page.waitForTimeout(600)

    // 不应出现未捕获异常导致的 ErrorBoundary 降级
    await expect(page.getByText(/出错了|页面崩溃/)).toHaveCount(0)
  })

  test('回收站数据损坏时给出空列表而不是白屏', async ({ page }) => {
    await page.addInitScript((key: string) => {
      localStorage.setItem('siyi-onboarding-done', '1')
      // 故意写入非法 JSON
      localStorage.setItem(key, '{这不是合法JSON')
    }, TRASH_KEY)

    await page.goto('/')
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 20_000 })
    await page.getByRole('tab', { name: '笔记' }).click()
    await page.getByRole('button', { name: '回收站' }).click()
    await page.waitForTimeout(600)

    // 页面仍然可用（Tab 栏还在），没有崩到白屏
    await expect(page.getByRole('tablist')).toBeVisible()
    await expect(page.getByText(/出错了|页面崩溃/)).toHaveCount(0)
  })
})
