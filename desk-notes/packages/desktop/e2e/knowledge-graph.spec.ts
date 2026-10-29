import { test, expect, waitForWsFrames } from './fixtures'

/**
 * 知识图谱前端旅程 E2E（模拟客户交互）
 * 侧边栏「知识网络」→ 触发后端 knowledge.graph 拉取 → 渲染图谱视图 →
 * 切换「知识卡片」触发 knowledge.cards。
 *
 * 判据在协议层取证（waitForWsFrames），不依赖节点数量：真实后端里知识图
 * 谱的 entities 可能为空（尚未抽取实体），但前端→后端的取数请求必须发生，
 * 这才是「模块接线 + 跨系统协调」的真实证据。视图切换按钮存在性保证 UI 已挂载。
 */
test.describe('Knowledge graph journey', () => {
  const activeModule = (page: import('@playwright/test').Page) =>
    page.evaluate(() => {
      const store = (window as unknown as Record<string, unknown>).__useStore as
        | { getState: () => { activeModule: string } }
        | undefined
      return store?.getState()?.activeModule
    })

  test('打开知识网络并向后端请求 knowledge.graph，图谱视图挂载', async ({ page }) => {
    const sidebar = page.getByTestId('nav-sidebar')
    await sidebar.getByRole('button', { name: '知识网络' }).click()
    await page.waitForTimeout(600)

    // 父模块经 MERGED_DEFAULTS 落到默认子模块 knowledge-graph
    expect(await activeModule(page)).toBe('knowledge-graph')

    // 协议层取证：前端确实发起 knowledge.graph 请求
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'knowledge.graph'),
      20_000,
      'knowledge.graph 请求',
    )

    // 视图切换器存在 → KnowledgeGraphModule 已渲染
    await expect(page.getByRole('button', { name: '关系图谱' })).toBeVisible()
    await expect(page.getByRole('button', { name: '思维导图' })).toBeVisible()
  })

  test('切换到知识卡片触发 knowledge.cards 拉取', async ({ page }) => {
    const sidebar = page.getByTestId('nav-sidebar')
    await sidebar.getByRole('button', { name: '知识网络' }).click()
    await page.waitForTimeout(600)

    await page.getByRole('button', { name: '知识卡片' }).click()

    // 卡片视图挂载后会拉取 knowledge.cards
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'knowledge.cards'),
      20_000,
      'knowledge.cards 请求',
    )
  })
})
