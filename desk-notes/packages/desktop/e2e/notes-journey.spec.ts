import { test, expect, Page } from './fixtures'

/**
 * U0 笔记功能 · 用户模拟旅程（L2 双断言，2026-09-19）。
 *
 * 原则（结合本战役测试经验）：
 *  - 每步双断言：UI 显示状态 + 页面内另开 WS 直连 harness 后端查真源（notes.list 等）；
 *  - 冷启动重试：导航点击可能落在 handler 就绪前（chat.spec U0 实测），beforeEach 二次点击；
 *  - 排列为 store 动作（既有先例），用户动作为真实 UI（打字/搜索/删除确认/粘贴）。
 * 仅在 globalSetup 起了真后端时执行。
 */

const haveBackend = () => Boolean(process.env.SIYI_E2E_WS_URL)

/** 页面内 WS 直连真源（复用 notes.spec 的既证模式） */
async function wsTruth<T>(page: Page, method: string, params: Record<string, unknown>, deadlineMs = 15000): Promise<T> {
  const wsUrl = process.env.SIYI_E2E_WS_URL as string
  return page.evaluate(
    async ({ url, method, params, deadlineMs }) => {
      const deadline = Date.now() + deadlineMs
      const call = () => new Promise<unknown>((resolveOnce, reject) => {
        const ws = new WebSocket(url)
        const timer = setTimeout(() => { ws.close(); reject(new Error('ws timeout')) }, 8000)
        ws.onopen = () => ws.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method, params }))
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(String(ev.data))
            if (msg && msg.id === 1) { clearTimeout(timer); ws.close(); resolveOnce(msg.result ?? msg.error) }
          } catch { /* 非 JSON 帧忽略 */ }
        }
        ws.onerror = () => { clearTimeout(timer); reject(new Error('ws error')) }
      })
      while (Date.now() < deadline) {
        try { return (await call()) as T } catch { await new Promise((r) => setTimeout(r, 800)) }
      }
      throw new Error(`truth deadline exceeded: ${method}`)
    },
    { url: wsUrl, method, params, deadlineMs },
  ) as Promise<T>
}

/** 经 store 排列一条笔记（既有先例：notes.spec addFloatingNote） */
async function seedNote(page: Page, title: string, content: string) {
  await page.evaluate(({ title, content }) => {
    const store = (window as unknown as { __useStore?: { getState: () => { nextZ: number; addFloatingNote: (n: Record<string, unknown>) => void } } }).__useStore
    if (!store) throw new Error('__useStore 不可用（需 dev 模式）')
    const s = store.getState()
    s.addFloatingNote({
      id: `u0-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`,
      type: 'text', title, content,
      x: 40, y: 40, width: 320, height: 240, zIndex: s.nextZ,
      createdAt: Date.now(), updatedAt: Date.now(),
    })
  }, { title, content })
}

async function gotoNotes(page: Page) {
  const nav = page.getByTestId('nav-sidebar').getByRole('button', { name: '笔记' })
  await nav.click()
  await expect(nav).toBeAttached()
  // 冷启动竞态二次点击（chat.spec U0 模板）
  try {
    await expect(page.getByText('便签', { exact: false }).first()).toBeVisible({ timeout: 8000 })
  } catch {
    await nav.click()
  }
}


/** 打开指定标题的笔记：编辑器已开着（跨模块保留态，产品行为）则复用；否则搜索定位点击 */
async function openNoteByTitle(page: Page, title: string) {
  const ta = page.locator('textarea').first()
  const titleInput = page.locator('input[placeholder*="新笔记"]').first()
  if (await ta.isVisible().catch(() => false)) {
    const v = (await titleInput.inputValue().catch(() => '')) || ''
    if (v === title) return
    // 开着别的笔记 → 回列表
    await page.getByTitle('返回列表', { exact: true }).first().click().catch(() => {})
  }
  // 快路径：画布/列表卡片标题是文本节点（J1/J2 既证）；500ms 内不中再走搜索回退
  try {
    await page.getByText(title, { exact: false }).first().click({ timeout: 4000 })
  } catch {
    await page.getByPlaceholder(/搜索/).first().fill(title)
    await page.waitForTimeout(600)
    await page.getByText(title, { exact: false }).first().click()
  }
  await expect(page.locator('textarea').first()).toBeVisible({ timeout: 10000 })
}

test.describe('笔记用户模拟旅程（真后端）', () => {
  test.describe.configure({ timeout: 120_000 })
  test.skip(!haveBackend(), '需要 e2e 真后端 harness')

  test.beforeEach(async ({ page }) => {
    await gotoNotes(page)
  })

  test('J1 打字→自动保存→双断言（UI 编辑器 + 后端 notes.list）', async ({ page }) => {
    const title = `U0旅程-${Date.now().toString(36)}`
    await seedNote(page, title, '初始内容')
    // 用户动作：点击列表项打开编辑器
    await openNoteByTitle(page, title)
    const ta = page.locator('textarea').first()
    // 打字（真实键入，触发 500ms 防抖链）
    await ta.click()
    await ta.pressSequentially('用户模拟新增的句子', { delay: 40 })
    // 防抖 + 保存链落地窗口
    await page.waitForTimeout(1800)
    // 双断言 1：编辑器内容含新增句
    expect(await ta.inputValue()).toContain('用户模拟新增的句子')
    // 双断言 2：后端真源（WS notes.list）内容一致
    const truth = await wsTruth<Array<{ title?: string; content?: string }>>(page, 'notes.list', { limit: 200 })
    const hit = truth.find((n) => (n.title || '') === title)
    expect(hit, '后端 notes.list 应能查到该笔记').toBeTruthy()
    expect(hit?.content || '').toContain('用户模拟新增的句子')
  })

  test('J2 搜索过滤（UI 过滤 + 真源对账）', async ({ page }) => {
    const marker = `搜索标记${Date.now().toString(36)}`
    await seedNote(page, `${marker} 甲`, '')
    await seedNote(page, '普通笔记乙', '')
    const search = page.getByPlaceholder(/搜索/).first()
    await search.fill(marker)
    await page.waitForTimeout(600) // 200ms 防抖
    // UI：列表出现标记笔记，普通笔记乙被滤掉
    await expect(page.getByText(`${marker} 甲`).first()).toBeVisible()
    await expect(page.getByText('普通笔记乙')).toHaveCount(0)
    // 真源：notes.list 两条都在（过滤是前端行为，不是删除）
    const truth = await wsTruth<Array<{ title?: string }>>(page, 'notes.list', { limit: 200 })
    expect(truth.some((n) => (n.title || '').includes(marker))).toBe(true)
    expect(truth.some((n) => n.title === '普通笔记乙')).toBe(true)
  })

  test('J3 打不丢字：输入后立刻切模块再回来（V1-4 flush 链）', async ({ page }) => {
    const title = `不丢字-${Date.now().toString(36)}`
    await seedNote(page, title, '种子')
    await openNoteByTitle(page, title)
    const ta = page.locator('textarea').first()
    await ta.click()
    await ta.pressSequentially('切走前最后敲的字', { delay: 30 })
    // 立刻切走（不给防抖窗口留时间）→ 切回
    await page.getByTestId('nav-sidebar').getByRole('button', { name: '求职猎手' }).click()  // 仪表盘在侧栏是隐藏项（U0 实测），用可见模块切走
    await page.waitForTimeout(500)
    await gotoNotes(page)
    // 切模块后编辑态被保留（产品行为，U0 实测）：编辑器可能直接开着
    await openNoteByTitle(page, title)
    const ta2 = page.locator('textarea').first()
    expect(await ta2.inputValue()).toContain('切走前最后敲的字')
    // 真源
    const truth = await wsTruth<Array<{ title?: string; content?: string }>>(page, 'notes.list', { limit: 200 })
    expect(truth.find((n) => (n.title || '') === title)?.content || '').toContain('切走前最后敲的字')
  })

  // 【立案 U0-J4，2026-09-19】主编辑器删除按钮未接 R4 审批信封：notes.delete RPC 返回
  // -32010（需审批流），UI 无提示地本地移除笔记 → 后端未删 → 重载后「复活」= 沉默失败。
  // 修复方向：编辑器删除 → pending 信封 → 接审批卡（与 chat 审批 UI 同源）→ 批准后再移除。
  // 修复落地后把 fixme 改回 test 并走完整删除→回收站→恢复断言。
  test.fixme('J4 删除→回收站→恢复（诚实链路：list_trashed 真源）', async ({ page }) => {
    const title = `要删的-${Date.now().toString(36)}`
    // 真创建（WS RPC）→ 重载让 store 采用后端真实 uuid（种子笔记的本地 id 会让删除解析失败，U0 实测）
    const created = await wsTruth<{ id?: number | string }>(page, 'notes.create', { title, content: '将被删除' })
    expect(created?.id).toBeTruthy()
    await page.reload()
    await page.evaluate(() => (window as unknown as { __useStore?: unknown }).__useStore)
    await gotoNotes(page)
    await openNoteByTitle(page, title)
    // 编辑器删除按钮（title=删除）→ 原生 confirm → Playwright 接受
    page.once('dialog', (d) => d.accept())
    await page.getByTitle('删除', { exact: true }).first().click()
    // 真源：notes.list_trashed 应含该笔记（软删落库）
    const trashed = await wsTruth<Array<{ title?: string }>>(page, 'notes.list_trashed', {}, 20000)
    expect(trashed.some((n) => (n.title || '').includes(title)), '软删后应在回收站真源中').toBe(true)
    // UI：编辑器应自动退回浏览器（C4），原笔记不再出现在活动列表
    await expect(page.locator('textarea')).toHaveCount(0, { timeout: 10000 })
    // 恢复：回收站 UI → 恢复按钮 → 活动真源
    await page.getByTitle('回收站', { exact: true }).first().click()
    await page.getByText(title).first().waitFor({ state: 'visible', timeout: 10000 })
    await page.getByTitle('恢复', { exact: true }).first().click()
    await page.waitForTimeout(1200)
    const list = await wsTruth<Array<{ title?: string }>>(page, 'notes.list', { limit: 200 })
    expect(list.some((n) => (n.title || '').includes(title)), '恢复后应回到活动真源').toBe(true)
  })

  // 【立案 U0-J5，2026-09-20】取证结论（j5probe 实测，探针已删，git 历史可查）：
  // 剪贴板写入 ✓ / paste 事件到达 textarea ✓（原生监听 files=1）/ onPaste 已挂（React fiber hasPaste=true）/
  // 直调处理器无异常但静默早退 → 未插入。疑点：处理器内 ws 状态或 asset.put 响应被 errorStore 吞掉。
  // 接手姿势：临时恢复 __assetDebug 调试桩（git 历史版本有现成桩代码）单测取证。
  test.fixme('J5 粘贴图片 → 资产 RPC → 相对路径 markdown → 预览渲染（方案 A 全链）', async ({ page }) => {
    const title = `贴图-${Date.now().toString(36)}`
    await seedNote(page, title, '贴图前')
    await openNoteByTitle(page, title)
    const ta = page.locator('textarea').first()
    await ta.click()
    // 合成 paste 事件：1x1 PNG 进 DataTransfer
    // 真实剪贴板路径（合成 ClipboardEvent 不触发 React onPaste，U0 实测）
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'])
    const clipErr = await page.evaluate(async () => {
      try {
        const b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='
        const bin = atob(b64)
        const bytes = new Uint8Array(bin.length)
        for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
        const blob = new Blob([bytes], { type: 'image/png' })
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
        const readBack = await navigator.clipboard.read()
        return readBack.length ? '' : 'clipboard.read returned empty'
      } catch (e) {
        return String(e)
      }
    })
    expect(clipErr, '剪贴板写入应成功（否则本测试环境不支持）').toBe('')
    // 编辑器应插入 ![...](assets/....png)
    const value = await ta.inputValue()
    await expect.poll(() => ta.inputValue(), { timeout: 15000 }).toMatch(/!\[[^\]]*\]\(assets\/[A-Za-z0-9._-]+\.png\)/)
    // 真源：notes.asset.get 按插入的名字应能取回（success=true）
    const m = value.match(/!\[[^\]]*\]\((assets\/[A-Za-z0-9._-]+\.png)\)/)
    if (m) {
      const got = await wsTruth<{ success?: boolean; mime?: string }>(page, 'notes.asset.get', { name: m[1].replace(/^assets\//, '') })
      expect(got.success, `资产应可经后端取回: ${m[1]}`).toBe(true)
    }
    // 预览渲染：切预览模式 → img 元素出现（data URL 或占位）
    await page.getByTitle('预览模式', { exact: true }).first().click()
    await expect(page.locator('img').first()).toBeVisible({ timeout: 10000 })
  })

  test('J6 contentFormat 防复活（用户面）：含 HTML 的笔记打开后不被改写', async ({ page }) => {
    const raw = '# 保留\n\n<u>下划线</u> 与 <font color="red">红字</font>\n\n- 列表\n'
    const title = `不改写-${Date.now().toString(36)}`
    await seedNote(page, title, raw)
    await page.getByText(title, { exact: false }).first().click()
    const ta = page.locator('textarea').first()
    await expect(ta).toBeVisible({ timeout: 10000 })
    // 编辑器源码必须逐字保留（读即迁移已删除）
    expect(await ta.inputValue()).toBe(raw)
    await page.waitForTimeout(1200) // 若仍有迁移写回，这里会暴露
    const truth = await wsTruth<Array<{ title?: string; content?: string }>>(page, 'notes.list', { limit: 200 })
    expect(truth.find((n) => (n.title || '') === title)?.content || '').toBe(raw)
  })
})
