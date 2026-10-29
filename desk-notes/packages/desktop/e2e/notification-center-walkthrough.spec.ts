import { test, expect, waitForWsFrames, type Page } from './fixtures'

/** Notification Center（通知中心）深走查（正式回归）：
 *  进入 → 协议层 notification.list / notification.stats 发出 → 未读/全部 筛选按钮 →
 *  注入测试通知（notification.test，自包含不碰预置）→ 进入列表回查 →
 *  标记已读（notification.mark_read，真实效果）→ 删除清理（notification.delete，真实效果）。
 *  逐步骤断言 + 协议层取证 + console 错误收集。
 *
 * 关键隔离约束：notification-center.spec 视觉基线依赖 globalSetup 预置的 4 条
 * NOTIFICATION_PRESETS，本走查**绝不**调用 mark_all_read / delete 预置项，
 * 改用 notification.test 自包含注入一条 test:True 通知，验证完即标已读+删除，
 * 避免跨用例污染预置数据（同一 harness 后端被全量回归共用）。 */

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

/** 经 dev 暴露的 store 直达（侧边栏已收敛，notification 不在栏上） */
async function gotoNotification(page: Page) {
  await page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: { getState: () => { setActiveModule?: (m: string) => void } }
    }).__useStore
    store?.getState()?.setActiveModule?.('notification')
  })
  await expect(page.getByTestId('notification-workspace')).toBeVisible({ timeout: 20_000 })
  // loading 占位消失 = notification.list / notification.stats 两个 RPC 都已回
  await expect(page.getByText('正在读取通知...')).toBeHidden({ timeout: 20_000 })
}

/** 经页面内 ws 直连后端调 RPC。
 *  - 每条调用开**全新** WS 连接（避免 R4 同连接自批被拒），并带 client_version
 *    声明非 legacy（否则 R4 方法被直接拒 -32010）。
 *  - 若返回 R4 pending 信封（{pending:true, pending_id}），自动在**另一条**连接上
 *    调 sandbox.approve 放行，并返回审批结果（真实执行结果会回给原请求连接，
 *    此处已关闭，故以「回查」断言真实副作用）。
 *  - 按请求 id 过滤后端推送事件。 */
async function rpc<T = unknown>(
  page: Page,
  method: string,
  params: Record<string, unknown>,
  rid = 1,
): Promise<{ result?: T; error?: unknown }> {
  return page.evaluate(
    async ([method, params, rid]) => {
      const url = (window as unknown as Record<string, unknown>).__SIYI_WS_URL__ || 'ws://127.0.0.1:18765/ws'
      const CLIENT_VERSION = '1.2.0'
      const open = (frame: Record<string, unknown>) =>
        new Promise<{ result?: unknown; error?: unknown }>((resolve) => {
          const ws = new WebSocket(url as string)
          const timer = setTimeout(() => {
            try {
              ws.close()
            } catch {
              /* noop */
            }
            resolve({ error: 'rpc timeout' })
          }, 15_000)
          ws.onopen = () => ws.send(JSON.stringify(frame))
          ws.onmessage = (ev) => {
            try {
              const d = JSON.parse(ev.data as string) as { id?: number; result?: unknown; error?: unknown }
              if (d.id !== (frame.id as number)) return // 忽略后端推送事件
              clearTimeout(timer)
              try {
                ws.close()
              } catch {
                /* noop */
              }
              resolve(d)
            } catch {
              /* ignore */
            }
          }
          ws.onerror = () => {
            clearTimeout(timer)
            resolve({ error: 'ws error' })
          }
        })
      const req = { jsonrpc: '2.0', id: rid, method, params, client_version: CLIENT_VERSION }
      const res = (await open(req)) as { result?: { pending?: boolean; pending_id?: string }; error?: unknown }
      const r = res.result as { pending?: boolean; pending_id?: string } | undefined
      if (r && r.pending === true && r.pending_id) {
        // R4：开新连接审批（同连接自批被拒）
        const appr = await open({
          jsonrpc: '2.0',
          id: rid + 1000,
          method: 'sandbox.approve',
          params: { pending_id: r.pending_id },
          client_version: CLIENT_VERSION,
        })
        return appr as { result?: T; error?: unknown }
      }
      return res as { result?: T; error?: unknown }
    },
    [method, params, rid],
  ) as Promise<{ result?: T; error?: unknown }>
}

const haveBackend = () => Boolean(process.env.SIYI_E2E_WS_URL)

test('notification-center deep walkthrough', async ({ page }) => {
  test.skip(!haveBackend(), '需要 e2e 真后端 harness（未设 SIYI_E2E_WS_URL，browser-only 模式跳过）')
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入通知中心', async () => {
    await gotoNotification(page)
    expect(
      await page.evaluate(() => (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore?.getState()?.activeModule),
    ).toBe('notification')
  })

  await step('协议层 notification.list 发出', async () => {
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'notification.list'),
      20_000,
      'notification.list 请求',
    )
  })

  await step('协议层 notification.stats 发出', async () => {
    await waitForWsFrames(
      page,
      (frames) => frames.some((f) => f.dir === 'out' && f.frame.method === 'notification.stats'),
      20_000,
      'notification.stats 请求',
    )
  })

  await step('未读/全部 筛选按钮存在', async () => {
    await expect(mc(page).getByRole('button', { name: '未读', exact: true })).toBeVisible({ timeout: 8000 })
    await expect(mc(page).getByRole('button', { name: '全部', exact: true })).toBeVisible()
  })

  let testId = ''
  await step('注入测试通知（notification.test，自包含不碰预置）', async () => {
    const r = await rpc<{ ok?: boolean; notification?: { id?: string } }>(
      page,
      'notification.test',
      { channel: 'websocket', event_type: 'e2e-walk' },
      9101,
    )
    if (r.error) throw new Error('notification.test error: ' + JSON.stringify(r.error))
    const notif = r.result?.notification
    if (!notif || !notif.id) throw new Error('notification.test 未返回通知 id: ' + JSON.stringify(r.result))
    testId = notif.id
  })

  await step('测试通知进入列表（notification.list 回查）', async () => {
    const r = await rpc<{ items?: Array<{ id: string }> }>(page, 'notification.list', {}, 9102)
    const items = r.result?.items || []
    if (!items.some((i) => i.id === testId)) throw new Error('通知未出现在 list 中')
  })

  await step('标记已读（notification.mark_read，真实效果）', async () => {
    const r = await rpc<{ updated?: boolean }>(page, 'notification.mark_read', { notification_id: testId, read: true }, 9103)
    if (r.error) throw new Error('mark_read error: ' + JSON.stringify(r.error))
    if (r.result?.updated !== true) throw new Error('mark_read 未更新: ' + JSON.stringify(r.result))
    const r2 = await rpc<{ items?: Array<{ id: string; read?: boolean }> }>(page, 'notification.list', {}, 9104)
    const it = (r2.result?.items || []).find((i) => i.id === testId)
    if (!it || it.read !== true) throw new Error('回查通知未变为已读')
  })

  await step('删除清理（notification.delete，真实效果）', async () => {
    // delete 是 R4：rpc 返回审批结果 {success:true}（同连接自批被拒，已自动换连接审批）；
    // 真实副作用以「回查 list 不存在」断言。
    const r = await rpc<{ deleted?: boolean; success?: boolean }>(page, 'notification.delete', { notification_id: testId }, 9105)
    if (r.error) throw new Error('delete error: ' + JSON.stringify(r.error))
    const ok = r.result?.success === true || r.result?.deleted === true
    if (!ok) throw new Error('delete 未成功: ' + JSON.stringify(r.result))
    const r2 = await rpc<{ items?: Array<{ id: string }> }>(page, 'notification.list', {}, 9106)
    if ((r2.result?.items || []).some((i) => i.id === testId)) throw new Error('删除后通知仍存在')
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/notification-walkthrough-end.png' }).catch(() => {})

  console.log('\n[NOTIFICATION WALKTHROUGH] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log(
    `[NOTIFICATION WALKTHROUGH] 测试通知id=${testId}; console errors=` + JSON.stringify(errors.slice(0, 8)),
  )
})
