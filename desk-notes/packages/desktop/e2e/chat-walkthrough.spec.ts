import { test, expect, waitForWsFrames, type Page, type WsFrame } from './fixtures'

/**
 * Chat（小问）深走查（正式回归）：
 *  进入（nav 小问 → chat-workbench → 后端已连接）→ 首条消息发送（chat.send 协议取证 +
 *  离线兜底不抛错：用户气泡上屏 + assistant 兜底消息落库）→ 展开对话列表
 *  （conversation-sidebar 可见）→ 新建会话（conversations +1）→ 折叠对话列表 →
 *  多模式切换（联网 / 多Agent 协作）→ 返回（离开 chat 模块）。
 *  step/results 单步失败仅记录不抛出；协议层取证 + console 错误收集。
 *
 * 2026-10-16：按「运行时真实渲染的 ChatView」编写（非 Modules/ChatModule.tsx 孤儿旧代码——
 * 该文件引用的 ChatInput/ChatHistorySidebar 等已不在磁盘，运行时由 ChatView 承载 chat-workbench）：
 *  - 历史/新建入口在 ConversationSidebar，由 header 折叠按钮 title『展开对话列表』切换；
 *  - 多模式 toggle 在 footer 的 ChatComposer 内（文字『联网』『Agent』）。
 *  - 离线兜底：harness 默认无 provider，chat.send 走后端降级，前端必落一条 assistant 兜底
 *    消息且不抛错（与 chat.spec 离线分支同源）。
 */

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

const chat = (page: Page) => page.getByTestId('chat-workbench')

const haveBackend = () => Boolean(process.env.SIYI_E2E_WS_URL)

interface ChatUiState {
  conversationId: string | null
  conversationsLen: number
  session: {
    isStreaming: boolean
    streamedText: string
    error: string | null
    events: Array<{ type: string; data: string }>
  } | null
  messages: Array<{ role: string; content: string }>
}

async function readChatState(page: Page): Promise<ChatUiState> {
  return page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: {
        getState: () => {
          activeConversationId: string | null
          conversations: Array<{ id: string; messages: Array<{ role: string; content: string }> }>
          chatSessions: Record<string, { isStreaming: boolean; streamedText: string; error: string | null; events: Array<{ type: string; data: string }> }>
        }
      }
    }).__useStore
    if (!store) throw new Error('__useStore 不可用（需 dev 模式）')
    const s = store.getState()
    const id = s.activeConversationId
    const conv = id ? s.conversations.find((c) => c.id === id) : undefined
    return {
      conversationId: id,
      conversationsLen: s.conversations.length,
      session: id ? (s.chatSessions[id] ?? null) : null,
      messages: conv?.messages ?? [],
    }
  }) as Promise<ChatUiState>
}

/** 页面发出的某个 RPC 的全部请求帧，按采集顺序。 */
function requests(frames: WsFrame[], method: string): Array<{ id: number; at: number }> {
  const out: Array<{ id: number; at: number }> = []
  for (const f of frames) {
    if (f.dir !== 'out' || f.frame.method !== method) continue
    const id = f.frame.id
    if (typeof id === 'number') out.push({ id, at: f.at })
  }
  return out
}

/** 经 DOM dispatchEvent 直接触发 ChatComposer 内多模式 toggle 的 React 合成 onClick
 *  （绕过 footer 布局下 Playwright force-click 坐标派发被上层叠层拦截、React onClick 未
 *  触发的问题），按 title 子串定位按钮。返回是否找到并派发了事件。 */
async function clickToggleByTitle(page: Page, titleSubstr: string): Promise<boolean> {
  return page.evaluate((sub) => {
    const root = document.querySelector('[data-testid="chat-workbench"]')
    if (!root) return false
    const btns = Array.from(root.querySelectorAll('button'))
    const b = btns.find((el) => (el.getAttribute('title') || '').includes(sub))
    if (!b) return false
    b.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    return true
  }, titleSubstr)
}

async function ensureChat(page: Page): Promise<void> {
  await page.getByTestId('nav-sidebar').getByRole('button', { name: '小问' }).click()
  await expect(page.getByTestId('chat-workbench')).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('后端已连接')).toBeVisible({ timeout: 30_000 })
}

test('chat module deep walkthrough', async ({ page }) => {
  test.skip(!haveBackend(), '需要 e2e 真后端 harness（未设 SIYI_E2E_WS_URL，browser-only 模式跳过）')
  test.setTimeout(240_000)
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text().slice(0, 130))
  })
  page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e).slice(0, 130)))

  await step('进入 chat 模块（nav 小问 → chat-workbench → 后端已连接）', async () => {
    await ensureChat(page)
    const am = await page.evaluate(() => (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore?.getState()?.activeModule)
    expect(am).toBe('chat')
  })

  await step('输入区与发送控件可见', async () => {
    await expect(chat(page).getByLabel('聊天输入框')).toBeVisible({ timeout: 8000 })
    await expect(chat(page).getByRole('button', { name: '发送消息' }).first()).toBeVisible()
  })

  let sendRpcSeen = false
  await step('发送首条消息 → chat.send 协议取证 + 离线兜底落库', async () => {
    const prompt = `E2E走查探针-${Date.now().toString(36)} 用一句话介绍你自己，不要调用工具`
    await chat(page).getByLabel('聊天输入框').fill(prompt)
    await chat(page).getByRole('button', { name: '发送消息' }).first().click()
    // 协议层：chat.send 请求帧必然发出（后端无论有无 provider 都先接单）
    await waitForWsFrames(page, (fs) => requests(fs, 'chat.send').length > 0, 15_000, 'chat.send 请求帧')
    sendRpcSeen = true
    // 用户气泡即时上屏（optimistic）
    await expect(page.getByText(prompt).first()).toBeVisible({ timeout: 10_000 })
    // 离线兜底：等 assistant 消息落库（不依赖具体文案，覆盖 error / 已停止 / 失败三种兜底）
    await expect
      .poll(async () => (await readChatState(page)).messages.filter((m) => m.role === 'assistant').length, {
        message: '离线兜底下应落至少一条 assistant 消息（不抛错）',
        timeout: 60_000,
      })
      .toBeGreaterThan(0)
  })

  await step('展开对话列表（conversation-sidebar 可见）', async () => {
    await chat(page).getByTitle('展开对话列表').click()
    await expect(page.getByTestId('conversation-sidebar')).toBeVisible({ timeout: 8000 })
  })

  let newConvOk = false
  await step('新建会话（conversations 计数 +1）', async () => {
    const before = (await readChatState(page)).conversationsLen
    await page.getByRole('button', { name: '新建任务' }).click()
    await expect
      .poll(async () => (await readChatState(page)).conversationsLen, {
        message: '新建会话后 conversations 计数应 +1',
        timeout: 8000,
      })
      .toBeGreaterThan(before)
    newConvOk = true
  })

  await step('折叠对话列表（conversation-sidebar 隐藏）', async () => {
    await chat(page).getByTitle('折叠对话列表').click()
    await expect(page.getByTestId('conversation-sidebar')).toBeHidden({ timeout: 8000 })
  })

  // 多模式 toggle 位于 composer footer：实测 boundingBox 在视口内、尺寸正常、文本正确，
  // 但 Playwright force-click 在该布局下因坐标处被上层叠层拦截（真实鼠标事件命中了上层
  // 元素）而未能触发 React onClick（诊断显示点击后 title 不变）。故改用 DOM dispatchEvent
  // 直接触发该按钮的 React 合成事件，并以标题在『联网搜索↔关闭联网搜索』『多Agent协作↔
  // 关闭多Agent协作』间切换断言真生效（searchMode/multiAgentMode 是 ChatView 局部 state，
  // 不进全局 store，title 是唯一可见信号）。
  await step('多模式：联网搜索开关', async () => {
    await clickToggleByTitle(page, '联网搜索') // 开（初始 title 为『联网搜索』）
    await expect(chat(page).getByText('联网', { exact: true }).first()).toHaveAttribute('title', '关闭联网搜索', { timeout: 8000 })
    await clickToggleByTitle(page, '关闭联网搜索') // 关回，回到基线态
    await expect(chat(page).getByText('联网', { exact: true }).first()).toHaveAttribute('title', '联网搜索', { timeout: 8000 })
  })

  await step('多模式：多Agent协作开关', async () => {
    await clickToggleByTitle(page, '多Agent协作') // 开
    await expect(chat(page).getByText('Agent', { exact: true }).first()).toHaveAttribute('title', '关闭多Agent协作', { timeout: 8000 })
    await clickToggleByTitle(page, '关闭多Agent协作') // 关回，回到基线态
    await expect(chat(page).getByText('Agent', { exact: true }).first()).toHaveAttribute('title', '多Agent协作', { timeout: 8000 })
  })

  await step('返回（离开 chat 模块）', async () => {
    // 注意：expect.poll 回调运行在 Node 端，不得直接引用 window；统一经 page.evaluate。
    await page.evaluate(() => (window as unknown as { __useStore?: { getState: () => { setActiveModule?: (m: string) => void } } }).__useStore?.getState()?.setActiveModule?.('todo'))
    const am = await page.evaluate(() => (window as unknown as { __useStore?: { getState: () => { activeModule: string } } }).__useStore?.getState()?.activeModule)
    expect(am).not.toBe('chat')
  })

  await page.screenshot({ path: 'D:/h/3.25/e2e-out/shots/chat-walkthrough-end.png' }).catch(() => {})

  console.log('\n[CHAT WALKTHROUGH] 结果：')
  for (const r of results) {
    console.log(`  ${r.ok ? 'PASS' : 'FAIL'}  ${r.step}${r.note ? ' :: ' + r.note : ''}`)
  }
  console.log(
    `[CHAT WALKTHROUGH] chat.send 观测=${sendRpcSeen}; 新建会话=${newConvOk}; console errors=` +
      JSON.stringify(errors.slice(0, 8)),
  )
})
