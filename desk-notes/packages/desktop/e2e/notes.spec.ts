import { test, expect } from './fixtures'

/**
 * C-12 补测：建笔记 → 列表出现（真后端路径）。
 *
 * 链路：UI 进「笔记」模块 → 走与工具栏/速记相同的 store 动作
 * addFloatingNote（noteSlice 内部 syncCall('notes.create') → WsClient → 后端
 * personal_note_repository，全部落在 harness 的临时 SIYI_DATA_DIR）。
 * 断言两层：
 *  1) 前端列表即时出现（NotesBrowser 条目渲染标题）；
 *  2) 后端真实收到：页面内另开一条 WS 直连 harness 后端轮询 notes.list，
 *     标题命中才算过——证明写路径穿透到 SQLite，而非仅 localStorage。
 *
 * 仅在 globalSetup 起了后端（SIYI_E2E_WS_URL 存在）时执行；browser-only 模式跳过。
 */

const haveBackend = () => Boolean(process.env.SIYI_E2E_WS_URL)

test.describe('Notes create → list (real backend)', () => {
  test('created note appears in UI list and is persisted via backend notes.list', async ({ page }) => {
    test.skip(!haveBackend(), '需要 e2e 真后端 harness（未设 SIYI_E2E_WS_URL，browser-only 模式跳过）')

    await page.getByTestId('nav-sidebar').getByRole('button', { name: '笔记' }).click()

    const title = `E2E建笔记-${Date.now().toString(36)}`
    await page.evaluate((t) => {
      const store = (window as unknown as {
        __useStore?: {
          getState: () => {
            nextZ: number
            addFloatingNote: (n: Record<string, unknown>) => void
          }
        }
      }).__useStore
      if (!store) throw new Error('__useStore 不可用（需 dev 模式）')
      const s = store.getState()
      s.addFloatingNote({
        id: `e2e-${Date.now().toString(36)}`,
        type: 'text',
        title: t,
        content: 'C-12 e2e：建笔记→列表出现（真后端 notes.create 路径）',
        x: 40, y: 40, width: 280, height: 200,
        zIndex: s.nextZ,
        createdAt: Date.now(),
      })
    }, title)

    // 1) 前端列表出现
    await expect(page.getByText(title).first()).toBeVisible({ timeout: 10_000 })

    // 2) 后端落库：页面内 WS 直连 harness 后端，轮询 notes.list 直到命中
    const wsUrl = process.env.SIYI_E2E_WS_URL as string
    const persisted = await page.evaluate(async ({ url, t }) => {
      const deadline = Date.now() + 30_000
      const listOnce = () => new Promise<Array<{ title?: string }>>((resolve, reject) => {
        const ws = new WebSocket(url)
        const timer = setTimeout(() => { ws.close(); reject(new Error('ws timeout')) }, 8000)
        ws.onopen = () => ws.send(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'notes.list', params: { limit: 500, include_empty: true } }))
        ws.onmessage = (ev) => {
          try {
            const msg = JSON.parse(String(ev.data))
            if (msg && msg.id === 1) { clearTimeout(timer); ws.close(); resolve(Array.isArray(msg.result) ? msg.result : []) }
          } catch { /* 非 JSON 帧忽略 */ }
        }
        ws.onerror = () => { clearTimeout(timer); reject(new Error('ws error')) }
      })
      while (Date.now() < deadline) {
        try {
          const notes = await listOnce()
          if (notes.some((n) => (n.title ?? '') === t)) return true
        } catch { /* 后端瞬断/未就绪，重试到 deadline */ }
        await new Promise((r) => setTimeout(r, 1000))
      }
      return false
    }, { url: wsUrl, t: title })

    expect(persisted, '后端 notes.list 应能查到 harness 期间创建的笔记').toBe(true)
  })
})
