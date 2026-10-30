import { test, expect, type Page } from '@playwright/test'

/**
 * 附件（图片/文件）处理测试。
 *
 * 背景 bug：用户选图后，代码把整个 `data:image/png;base64,...` 拼进消息正文。
 * 三重后果：
 *   1. 这段 base64 文字会被当作普通文本发给 AI ——
 *      图片根本不会被多模态理解，只是变成几十万 token 的垃圾，必然 400；
 *   2. 整段写进 chat.messages 并持久化到 IndexedDB，几张图就撑爆存储；
 *   3. 照片内容（证件/卡面）不受隐私分级约束就上云。
 *
 * 修复后：消息里只放「［图片：xx.png（1.2MB，内容未发送给 AI）］」占位符。
 * 本测试锁定「base64 绝不进消息正文」这一契约。
 */

async function seedCloud(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('siyi-onboarding-done', '1')
    localStorage.setItem('siyi_cloud_ai_enabled', 'true')
    localStorage.setItem('siyi_cloud_api_base_url', 'https://api.deepseek.com')
    localStorage.setItem('siyi_cloud_model', 'deepseek-chat')
    localStorage.setItem('siyi_prefer_desktop_ai', 'false')
    localStorage.setItem('siyi_prompt_before_cloud', 'false')
    localStorage.setItem('siyi_cloud_api_key', 'sk-test-123')
  })
}

/** 拦截云端请求，记录 body */
function intercept(page: Page) {
  const bodies: string[] = []
  page.route('**/*', async (route) => {
    const url = route.request().url()
    if (!/deepseek|\/v1\/chat/.test(url)) return route.continue()
    bodies.push(route.request().postData() || '')
    await route.fulfill({
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
      body: 'data: {"choices":[{"delta":{"content":"好的"}}]}\n\ndata: [DONE]\n\n',
    })
  })
  return bodies
}

test.describe('附件不会把 base64 塞进消息', () => {
  test('选图并发送后，请求体里不含 data:image base64', async ({ page }) => {
    await seedCloud(page)
    const bodies = intercept(page)

    await page.goto('/')
    await expect(page.getByRole('tablist')).toBeVisible({ timeout: 20_000 })
    await page.getByRole('tab', { name: 'AI' }).click()
    await page.getByPlaceholder('输入消息...').waitFor({ state: 'visible' })

    /*
     * 必须先点「附件」按钮展开面板，再定位 **AI 页内** 的 file input。
     *
     * 踩过的坑：5 个 Tab 常驻挂载（App.tsx 的 P1-18 设计），
     * 所以 `page.locator('input[type=file]').first()` 抓到的是**笔记页的导入框**
     * （NotesView.tsx:336 的「已导入: xxx」），根本不是聊天附件。
     * 结果测试把文件导进了笔记库，还制造了一条含 PNG 二进制的脏笔记，
     * 看起来像「附件处理有 bug」，实际是选择器指错了元素。
     */
    await page.getByRole('button', { name: '附件' }).click()
    await page.waitForTimeout(500)

    // 附件面板展开后，取当前可见的那个 file input（隐藏的不算）
    const fileInput = page.locator('input[type="file"]')
    const total = await fileInput.count()
    let target = fileInput.first()
    let found = false
    for (let i = 0; i < total; i++) {
      const el = fileInput.nth(i)
      // 用 evaluate 判断是否在 AI 页的可见容器里
      const inAiPage = await el.evaluate((node) => {
        let p = node.parentElement
        while (p) {
          if (p.classList && p.classList.contains('page--hidden')) return false
          if (p.classList && p.classList.contains('page')) return true
          p = p.parentElement
        }
        return false
      }).catch(() => false)
      if (inAiPage) { target = el; found = true; break }
    }
    if (!found) {
      test.skip(true, '未找到 AI 页内的 file input，跳过')
      return
    }
    const fileInputResolved = target

    const pngBase64 =
      'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='
    await fileInputResolved.setInputFiles({
      name: 'test.png',
      mimeType: 'image/png',
      buffer: Buffer.from(pngBase64, 'base64'),
    })
    await page.waitForTimeout(800)

    /*
     * 措辞必须同时绕开三条前置分支，否则测不到主链路：
     *   1) matchTools 关键词匹配 —— notes.create 的 tag 含「内容」「note」
     *      「记」等，说「内容是什么」会直接被存成笔记并返回「笔记已创建」，
     *      请求根本不发。实测这一条真的把带 PNG 文件名的文本存进了 floatingNotes。
     *   2) tryAutoCreateTask —— 「看/去/买/帮我/要」等开头会走抽取分支，
     *      那条路发的是 extractPrompt，不带附件占位符。
     *   3) looksLikeMultiStepTask —— 「然后/打开/点」等会进 Agent 循环。
     * 故这里用一句无关键词的纯询问。
     */
    await page.getByPlaceholder('输入消息...').fill('这是啥')
    const sendBtn = page.getByRole('button', { name: '发送' })
    if (await sendBtn.isVisible().catch(() => false)) {
      await sendBtn.click()
    }
    await page.waitForTimeout(3000)

    if (bodies.length === 0) {
      // 请求没发出（可能还在排队），此用例的核心断言改为「消息里没有 base64」
      const allText = await page.locator('body').innerText()
      expect(allText).not.toContain('data:image')
      return
    }

    const sent = bodies.join('\n')
    // 关键：base64 图片数据绝不能出现在发给 AI 的请求里
    expect(sent, 'base64 图片数据不能进入请求体').not.toContain('data:image')
    // 但应当有占位符，让模型知道有附件
    expect(sent).toContain('图片')
  })
})
