import { test, expect, type Page } from '@playwright/test'

/**
 * AI 功能端到端测试（本文件是这次升级的重点）。
 *
 * 为什么单独一套：单元测试只能证明「函数算得对」，
 * 但 AI 这条链路上真正的坑几乎全在「接线」上，而接线只有真跑浏览器才验证得到。
 * 前几轮实测踩到的真实故障都属于这一类：
 *   - 填了 Key 却提示「未配置云 AI」（配置读取与路由判断不一致）
 *   - 桌面在线时云 AI 永远不可达（路由分支写错）
 *   - 「测试成功但对话失败」（测试与对话用了不同模型名）
 *   - 说一句「明天去银行办卡」被拒「未配置云 AI，无法运行多步任务」
 * 这些都不是纯函数问题，必须端到端测。
 *
 * 手法：
 *   1. addInitScript 预置 localStorage 配置（模拟用户已完成设置）
 *   2. 伪造 Capacitor Preferences bridge，让 API Key 能被 secureStore 读到
 *   3. page.route 拦截外部 API，既能断言「请求发没发、发的内容对不对」，
 *      又能返回可控的 SSE 流，让整个流式渲染路径真跑一遍
 *
 * 关键约束：所有断言一律用 toBeVisible()。App 的 5 个 Tab 常驻挂载、
 * 只切 display，用 count()/toBeAttached() 会把隐藏内容也算进来，是假绿。
 */

/** 预置一份「云端 AI 已配好」的环境 */
interface SeedOpts {
  cloudEnabled?: boolean
  apiKey?: string | null
  model?: string
  promptBeforeCloud?: boolean
  preferDesktop?: boolean
  notes?: Array<{ id: string; title: string; content: string }>
  todos?: Array<{ id: string; text: string; done: boolean }>
}

async function seed(page: Page, opts: SeedOpts = {}) {
  await page.addInitScript((o: SeedOpts) => {
    localStorage.setItem('siyi-onboarding-done', '1')
    localStorage.setItem('siyi_cloud_ai_enabled', String(o.cloudEnabled ?? true))
    localStorage.setItem('siyi_cloud_api_base_url', 'https://api.deepseek.com')
    localStorage.setItem('siyi_cloud_model', o.model ?? 'deepseek-chat')
    localStorage.setItem('siyi_prefer_desktop_ai', String(o.preferDesktop ?? false))
    localStorage.setItem('siyi_prompt_before_cloud', String(o.promptBeforeCloud ?? false))

    /*
     * API Key 直接写 localStorage，不伪造 Capacitor bridge。
     *
     * 依据（读 secureStore.ts 的 initSecureStore 得出）：
     *   它先试 Preferences.get()，失败（浏览器无原生插件，会 throw）后
     *   回退读 localStorage 里的同名键并迁移进内存缓存。
     *   所以纯浏览器环境下把 Key 放进 localStorage 就能被 secureGet 读到。
     *
     * 之前我伪造 window.Capacitor.Plugins.Preferences，是错的：
     * @capacitor/core 通过 registerPlugin 生成代理对象，运行时才解析插件，
     * 手写 bridge 对不上它的内部约定，导致 Key 恒为空、cloudConfigured 恒 false
     * ——测试会以「请求数 0」的形式假失败，浪费一整轮排查。
     */
    if (o.apiKey) localStorage.setItem('siyi_cloud_api_key', o.apiKey)

    /*
     * 待办与笔记不在 mobileStore 里，而在 @desk-notes/shared 的 useStore，
     * 它的 zustand persist 键是 'desk-notes-storage'（不是 siyi-mobile-storage）。
     * 结构为 { state: {...}, version: N }。
     *
     * 之前我直接 setItem('todos', ...) 是错的：App 根本不读那个键，
     * 于是上下文里只有自带的示例笔记、没有我喂的待办，测试以
     * 「toContain('交电费') 失败」的形式假失败，容易误判成产品 bug。
     * 用 partialize 里声明的字段名注入才有效。
     */
    const now = Date.now()
    const sharedState: Record<string, unknown> = {}
    if (o.todos && o.todos.length > 0) {
      sharedState.todos = o.todos.map(t => ({
        id: t.id, text: t.text, done: t.done,
        createdAt: now, updatedAt: now, priority: 1,
      }))
    }
    if (o.notes && o.notes.length > 0) {
      sharedState.floatingNotes = o.notes.map(n => ({
        id: n.id, type: 'text', title: n.title, content: n.content,
        x: 0, y: 0, width: 320, height: 200, zIndex: 1,
        createdAt: now, updatedAt: now, tags: [], editMode: 'markdown',
      }))
    }
    if (Object.keys(sharedState).length > 0) {
      const existing = (() => {
        try { return JSON.parse(localStorage.getItem('desk-notes-storage') || 'null') } catch { return null }
      })()
      const merged = {
        state: { ...(existing?.state ?? {}), ...sharedState },
        version: existing?.version ?? 2,
      }
      localStorage.setItem('desk-notes-storage', JSON.stringify(merged))
    }
  }, opts)
}

/** 拦截云端请求并记录；返回可控 SSE */
function interceptCloud(page: Page, reply = '这是测试回复') {
  const calls: Array<{ url: string; body: string; auth: string }> = []
  page.route('**/*', async (route) => {
    const req = route.request()
    const url = req.url()
    if (!/deepseek|openai|\/v1\/chat/.test(url)) return route.continue()
    const body = req.postData() || ''
    calls.push({ url, body, auth: req.headers()['authorization'] || '' })
    const isStream = body.includes('"stream":true')
    if (isStream) {
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' },
        body:
          `data: ${JSON.stringify({ choices: [{ delta: { content: reply } }] })}\n\n` +
          'data: [DONE]\n\n',
      })
    } else {
      await route.fulfill({
        status: 200,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ choices: [{ message: { content: reply } }] }),
      })
    }
  })
  return calls
}

/** 打开 App 并切到 AI 页 */
async function openChat(page: Page) {
  await page.goto('/')
  await expect(page.getByRole('tablist')).toBeVisible({ timeout: 20_000 })
  await page.getByRole('tab', { name: 'AI' }).click()
  await page.getByPlaceholder('输入消息...').waitFor({ state: 'visible' })
}

/** 发一条消息并等结果 */
async function send(page: Page, text: string, waitMs = 4000) {
  const input = page.getByPlaceholder('输入消息...')
  await input.fill(text)
  await page.getByRole('button', { name: '发送' }).click()
  await page.waitForTimeout(waitMs)
}

test.describe('AI 对话：配置齐全时的完整链路', () => {
  test('发消息能真的发出云端请求，且用的是配置的模型名', async ({ page }) => {
    await seed(page, { apiKey: 'sk-test-123', model: 'deepseek-chat' })
    const calls = interceptCloud(page, '你好，我是助手')
    await openChat(page)

    await send(page, '你好')

    expect(calls.length, '必须真的发出云端请求').toBeGreaterThan(0)
    const body = JSON.parse(calls[0].body)
    // 模型名必须来自用户配置，不能被地址推断覆盖
    expect(body.model).toBe('deepseek-chat')
    // 鉴权头必须带上用户的 Key
    expect(calls[0].auth).toContain('sk-test-123')
  })

  test('AI 回复会渲染到气泡里', async ({ page }) => {
    await seed(page, { apiKey: 'sk-test-123' })
    interceptCloud(page, '收到你的消息了')
    await openChat(page)
    await send(page, '你好')
    await expect(page.getByText('收到你的消息了')).toBeVisible({ timeout: 10_000 })
  })

  test('用户配置的模型名优先于地址推断', async ({ page }) => {
    // 地址是 deepseek（会推断成 deepseek-chat），但用户显式填了别的模型
    await seed(page, { apiKey: 'sk-test', model: 'my-custom-model-v9' })
    const calls = interceptCloud(page)
    await openChat(page)
    await send(page, '你好')
    expect(calls.length).toBeGreaterThan(0)
    expect(JSON.parse(calls[0].body).model).toBe('my-custom-model-v9')
  })
})

test.describe('AI 对话：未配置时的引导', () => {
  test('没开云端开关 → 提示去打开开关，而不是笼统的"未配置"', async ({ page }) => {
    await seed(page, { cloudEnabled: false, apiKey: 'sk-test' })
    interceptCloud(page)
    await openChat(page)
    await send(page, '你好', 3000)
    // 必须指出是哪一项没配
    await expect(page.getByText(/云端 AI 未启用/)).toBeVisible({ timeout: 10_000 })
  })

  test('开了开关但没填 Key → 提示缺 Key', async ({ page }) => {
    await seed(page, { cloudEnabled: true, apiKey: null })
    interceptCloud(page)
    await openChat(page)
    await send(page, '你好', 3000)
    await expect(page.getByText(/缺少 API Key/)).toBeVisible({ timeout: 10_000 })
  })

  test('开了「上云前确认」→ 提示该设置会阻止自动上云', async ({ page }) => {
    await seed(page, { cloudEnabled: true, apiKey: 'sk-test', promptBeforeCloud: true })
    interceptCloud(page)
    await openChat(page)
    await send(page, '你好', 3000)
    await expect(page.getByText(/云端调用前确认/).first()).toBeVisible({ timeout: 10_000 })
  })
})

test.describe('AI 对话：上下文注入', () => {
  test('待办会随消息带给 AI', async ({ page }) => {
    await seed(page, {
      apiKey: 'sk-test',
      todos: [{ id: 't1', text: '交电费', done: false }],
    })
    const calls = interceptCloud(page)
    await openChat(page)
    // 措辞要绕开工具关键词匹配与多步判定
    await send(page, '这条记录说的是什么')
    expect(calls.length).toBeGreaterThan(0)
    expect(calls[0].body).toContain('交电费')
  })

  test('笔记内容会随消息带给 AI', async ({ page }) => {
    await seed(page, {
      apiKey: 'sk-test',
      notes: [{ id: 'n1', title: '周会记录', content: '下周三前提交课程设计报告' }],
    })
    const calls = interceptCloud(page)
    await openChat(page)
    await send(page, '我手上还有什么没做完的事')
    expect(calls.length).toBeGreaterThan(0)
    expect(calls[0].body).toContain('课程设计报告')
  })

  test('笔记里的敏感信息（银行卡号）不会原样发给云端', async ({ page }) => {
    /*
     * 隐私回归保护。此前 contextualizeMessage 直接把笔记全文拼进 prompt，
     * 不做任何过滤：用户说「帮我整理一下」（不含敏感词）→ 判为可上云
     * → 笔记里的卡号一起被发出去。
     */
    await seed(page, {
      apiKey: 'sk-test',
      notes: [{ id: 'n1', title: '银行', content: '我的银行卡号 6222 0202 0000 1234 567 密码是 abcdef' }],
    })
    const calls = interceptCloud(page)
    await openChat(page)
    // 措辞要同时绕开：工具关键词匹配（笔记/记下来/提醒）、
    // 任务抽取动词（帮我/看/要/去…）、多步动作词（然后/点/打开）
    await send(page, '这条内容说的是什么意思呢')
    expect(calls.length).toBeGreaterThan(0)
    const sent = calls[0].body
    expect(sent, '银行卡号不能被原样发出').not.toContain('6222 0202 0000 1234 567')
    // 但上下文本身应当还在（脱敏而非整段丢弃）
    expect(sent).toContain('当前上下文')
  })
})

test.describe('AI 对话：错误处理', () => {
  test('401 会翻译成「Key 无效」并指向设置页', async ({ page }) => {
    await seed(page, { apiKey: 'sk-bad' })
    await page.route('**/*', async (route) => {
      if (!/deepseek|\/v1\/chat/.test(route.request().url())) return route.continue()
      await route.fulfill({ status: 401, body: '{"error":"invalid_api_key"}' })
    })
    await openChat(page)
    await send(page, '你好', 3500)
    await expect(page.getByText(/API Key 无效/)).toBeVisible({ timeout: 10_000 })
  })

  test('429 会提示限流，而不是抛原始 JSON', async ({ page }) => {
    await seed(page, { apiKey: 'sk-test' })
    await page.route('**/*', async (route) => {
      if (!/deepseek|\/v1\/chat/.test(route.request().url())) return route.continue()
      await route.fulfill({ status: 429, body: '{"error":"rate_limit_exceeded"}' })
    })
    await openChat(page)
    await send(page, '你好', 3500)
    await expect(page.getByText(/限流/)).toBeVisible({ timeout: 10_000 })
    // 不该把原始 JSON 抛给用户
    await expect(page.getByText(/rate_limit_exceeded/)).toHaveCount(0)
  })
})

test.describe('AI 对话：中止生成', () => {
  test('生成中发送键变成「停止生成」', async ({ page }) => {
    await seed(page, { apiKey: 'sk-test' })
    // 让响应保持挂起，制造「生成中」状态
    await page.route('**/*', async (route) => {
      if (!/deepseek|\/v1\/chat/.test(route.request().url())) return route.continue()
      // 永不 fulfill，页面会一直处于 streaming
      await new Promise(() => {})
    })
    await openChat(page)
    const input = page.getByPlaceholder('输入消息...')
    await input.fill('你好')
    await page.getByRole('button', { name: '发送' }).click()
    await page.waitForTimeout(1200)

    await expect(page.getByRole('button', { name: '停止生成' })).toBeVisible({ timeout: 8000 })
    await page.getByRole('button', { name: '停止生成' }).click()
    await page.waitForTimeout(800)
    // 停止后应当恢复成可发送状态
    await expect(page.getByRole('button', { name: '发送' })).toBeVisible({ timeout: 8000 })
  })
})
