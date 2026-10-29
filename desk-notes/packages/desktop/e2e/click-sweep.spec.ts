/**
 * 全功能点击巡检 v1（2026-10-16，用户需求驱动）
 *
 * 目的：回答两件事——
 *   L1 可达性：每个模块的每个按钮都点得动、有可访问名（无 aria-label 的重灾区）
 *   L2 安全：点了不崩（pageerror）、不刷 console.error、弹窗有兜底
 *
 * 边界（重要，别越界）：本 spec **不判断"功能达到需求"**——那要 L3 效果断言
 * （逐功能人工定义期望，见 docs/e2e-click-sweep.md）。v1 先扫清"点不动/点了就炸"。
 *
 * 三层放行策略：
 *   - BLOCK_WORDS（删除/发送/生成…）：只枚举不点，列清单人工过
 *     （不可逆语义 or 触发真 AI 调用烧 token——沙箱也烧）
 *   - 其余按钮：真实点击 + 250ms 观察窗 + dialog 检测（ESC 兜底收回）
 *   - 侧边栏导航按钮天然排除（只扫 module-content 容器内）
 */

import { test, expect, type Page } from './fixtures'

const MODULES = [
  { label: '小问', active: 'chat' },
  { label: '笔记', active: 'notes' },
  { label: '待办', active: 'todo' },
  // active 名与 ContentRouter switch case 实测对齐：「学习」点击落 'wordbank'
  // （ContentRouter case 'wordbank'✓；navigation.spec 里 'wordbook' 是陈账断言，
  //  已登记待修）；「知识网络」落 'knowledge-graph'（同 navigation.spec 已验证）。
  { label: '知识网络', active: 'knowledge-graph' },
  { label: '学习', active: 'wordbank' },
  { label: 'Agent 任务', active: 'agent' },
  { label: '自动化', active: 'automation' },
  { label: '收件箱', active: 'inbox' },
  { label: '求职猎手', active: 'job-hunting' },
  { label: '工具', active: 'translate' },
  { label: '设置', active: 'settings' },
]

/** 点击即不可逆 / 烧真 AI token 的按钮——v1 只枚举不点 */
const BLOCK_WORDS = [
  '删除', '移除', '清空', '回滚', '重启', '关闭', '退出', '发送', '提交',
  '审批', '确认', '购买', '支付', '恢复', '执行', '生成', '摘要', '总结',
  '分析', '识别', '同步拉取', '导出', '重置', '格式化', '覆盖',
]

interface SweepEntry {
  name: string
  reason: string
}

interface SweepReport {
  total: number
  clicked: SweepEntry[]
  blocked: SweepEntry[]
  labelLess: SweepEntry[]
  clickFailed: SweepEntry[]
  disabled: SweepEntry[]
  openedDialog: SweepEntry[]
  dialogStuck: SweepEntry[]
  navigatedAway: SweepEntry[]
}

/** 覆盖层选择器：标准 role dialog **+ 本仓全屏 modal 家族全 z 档**
 *  （z-[10002] AISettingsPanel 等、z-[99998] SetupWizard、z-[99999] update
 *  notifier、z-[10001] Trash/Backup、z-50 AIManagementModule 等——
 *  z-50 漏网曾致 Agent 3 按钮 clickFailed 全灭，2026-10-16 教训）。 */
const OVERLAY_SELECTOR =
  '[role="dialog"], [role="alertdialog"], ' +
  '.fixed.inset-0.z-\\[10002\\], .fixed.inset-0.z-\\[99998\\], .fixed.inset-0.z-\\[99999\\], ' +
  '.fixed.inset-0.z-\\[10001\\], .fixed.inset-0.z-50, .absolute.inset-0.z-50'

/** 覆盖层是否"真挡着"：DOM 存在 ≠ 可见（AnimatePresence 退场动画 / display:none
 *  期间 count() 仍为 1，但视觉与点击已不受影响——v1 于此误报 63 次 stuck）。 */
async function overlayBlocking(page: Page): Promise<boolean> {
  const el = page.locator(OVERLAY_SELECTOR)
  if ((await el.count()) === 0) return false
  try {
    if (!(await el.first().isVisible())) return false
    const box = await el.first().boundingBox()
    return !!box && box.width > 50 && box.height > 50
  } catch {
    return false
  }
}

/**
 * 点击后覆盖层兜底：
 *   1. ESC（覆盖响应 ESC 的弹窗）
 *   2. 覆盖层内“取消/关闭”按钮（create-cron-modal 这类不监听 ESC 的）
 *   3. backdrop 角点点击（AISettingsPanel 的 backdrop 有 onClick=onClose）
 * 返回 true=已处理干净；false=仍挡着（调用方负责恢复：刷新重进模块）。
 */
async function dismissDialogs(page: Page, rep: SweepReport, name: string): Promise<boolean> {
  if (!(await overlayBlocking(page))) return true
  rep.openedDialog.push({ name, reason: 'overlay opened' })
  await page.keyboard.press('Escape')
  await page.waitForTimeout(150)
  if (await overlayBlocking(page)) {
    const closeBtn = page.locator(OVERLAY_SELECTOR).first().getByRole('button', { name: /取消|关闭|完成|Close|Cancel|×/ }).first()
    if ((await closeBtn.count()) > 0) {
      await closeBtn.click({ timeout: 2000 }).catch(() => {})
      await page.waitForTimeout(150)
    }
  }
  if (await overlayBlocking(page)) {
    // backdrop 角点（panel 居中，角落必为 backdrop；AISettingsPanel backdrop 可点关）
    await page.mouse.click(5, 5)
    await page.waitForTimeout(150)
  }
  if (await overlayBlocking(page)) {
    rep.dialogStuck.push({ name, reason: 'ESC/关闭/backdrop 均未关掉（产品侧 modal 缺陷）' })
    return false
  }
  return true
}

/** 停用/暂停类优先放行（比"分析/导出"等黑名单词更安全的语义） */
const SAFE_WORDS = ['停用', '暂停', '禁用', '取消', '刷新', '重试', '查看', '详情', '复制']

function matchBlockWord(name: string): string | null {
  if (SAFE_WORDS.some((w) => name.includes(w))) return null
  for (const w of BLOCK_WORDS) {
    if (name.includes(w)) return w
  }
  return null
}

/**
 * 为什么不用 .all() 的静态 handle：点击 tab/某些按钮后 React 重渲染，
 * 旧 handle 集体 detach → click 全超时（设置模块 49/65 clickFailed 即此）。
 * 改为每步按 index 重新解析 locator，已处理过的**按钮名**记集合防重复点击；
 * 列表长度变化（新增/移除节点）→ 从头再扫一轮，最多 4 轮防死循环。
 *
 * v1 防爆策略：
 *  - 无名按钮（图标按钮）：只登记不点——本身是待修缺陷，且位置漂移会让
 *    多轮扫描重复处理直至超时（实测设置模块 43 个无名按钮）。
 *  - 点击导致 activeModule 漂移（模块内导航）：记录并停下，不追扫。
 */
async function sweepButtons(
  page: Page,
  area: ReturnType<Page['getByTestId']>,
  rep: SweepReport,
  moduleActive: string,
  onStuck: () => Promise<void>,
): Promise<number> {
  const processed = new Set<string>()
  for (let round = 0; round < 4; round++) {
    const count = await area.getByRole('button').count()
    let acted = false
    let i = 0
    while (i < count) {
      const btn = area.getByRole('button').nth(i)
      let visible = false
      try {
        visible = await btn.isVisible()
      } catch {
        i++
        continue
      }
      if (!visible) {
        i++
        continue
      }
      const name = ((await btn.getAttribute('aria-label')) || (await btn.innerText().catch(() => '')) || '').trim()
      const key = name || `#idx${i}`
      if (processed.has(key)) {
        i++
        continue
      }
      processed.add(key)

      if (!name) {
        rep.labelLess.push({ name: '(空)', reason: '无 aria-label 且无可读文本（v1 只登记不点）' })
        i++
        continue
      }
      // disabled 按钮（未填表单时的保存/检测类）单独分类——不是"点不动"缺陷
      const enabled = await btn.isEnabled().catch(() => true)
      if (!enabled) {
        rep.disabled.push({ name, reason: 'disabled（v1 不点：多为表单未填的合法态）' })
        i++
        continue
      }
      const blockedBy = matchBlockWord(name)
      if (blockedBy) {
        rep.blocked.push({ name, reason: `blocked:${blockedBy}` })
        i++
        continue
      }

      // 点前兜底：上一轮残留弹窗先关（否则点击被遮挡 → clickFailed 螺旋）
      const preCleanOk = await dismissDialogs(page, rep, `(pre-clean) ${name}`)
      if (!preCleanOk) await onStuck()
      // 滚动容器内按钮（settings 侧栏/tab）：先显式滚入 + 等布局动画稳定，
      // 否则 Playwright actionability（stable=两帧同位）在 scroll 动画中反复重试至超时
      await btn.scrollIntoViewIfNeeded().catch(() => {})
      await page.waitForTimeout(100)
      try {
        await btn.click({ timeout: 4000 })
      } catch (exc) {
        rep.clickFailed.push({ name, reason: String(exc).slice(0, 120) })
        i++
        continue
      }
      rep.clicked.push({ name, reason: 'ok' })
      acted = true
      await page.waitForTimeout(150)
      const postCleanOk = await dismissDialogs(page, rep, name)
      if (!postCleanOk) await onStuck()

      // 模块内导航（点了按钮跳去别的模块）→ 记录并停下，不追扫
      const nowActive = await page.evaluate(() => {
        const store = (window as unknown as Record<string, unknown>).__useStore as
          | { getState: () => { activeModule: string } }
          | undefined
        return store?.getState()?.activeModule
      })
      if (nowActive !== moduleActive) {
        rep.navigatedAway.push({ name, reason: `跳到 ${nowActive}` })
        break
      }
      // 列表结构变化（tab 切换/节点增删）→ 重新解析，从头扫新出现的按钮
      if ((await area.getByRole('button').count()) !== count) break
      i++
    }
    const after = await area.getByRole('button').count()
    if (!acted && after === count) break
  }
  return processed.size
}
/** 进入模块并等 activeModule 生效 + 模块内首按钮渲染。
 *  重试 3 次：sidebar 在 framer-motion 布局动画 + auto-scroll 下偶发点击命中
 *  漂移（实测「学习」点成了「笔记」→ activeModule 停在 notes）。 */
async function enterModule(page: Page, label: string, active: string): Promise<void> {
  // 不用 exact：带未读 badge 的侧边栏按钮 accessibility name 带数字后缀
  // （「笔记 1」），exact:true 匹配失败 → 3 次重试全 miss（实测教训）；
  // sidebar 内 contains 匹配足够唯一（其余按钮名互不为子串）。
  const btn = page.getByTestId('nav-sidebar').getByRole('button', { name: label }).first()
  await btn.scrollIntoViewIfNeeded().catch(() => {})
  await page.waitForTimeout(200)
  let ok = false
  for (let attempt = 0; attempt < 3 && !ok; attempt++) {
    await btn.click({ timeout: 5000 }).catch(() => {})
    try {
      await page.waitForFunction(
        (expected) => {
          const store = (window as unknown as Record<string, unknown>).__useStore as
            | { getState: () => { activeModule: string } }
            | undefined
          return store?.getState()?.activeModule === expected
        },
        active,
        { timeout: 4000 },
      )
      ok = true
    } catch {
      // 命中漂移（点进别的模块）→ 重试
    }
  }
  if (!ok) {
    // 三次仍不生效 = 真导航缺陷，硬失败（带当前 activeModule 便于定位）
    const current = await page.evaluate(() => {
      const store = (window as unknown as Record<string, unknown>).__useStore as
        | { getState: () => { activeModule: string } }
        | undefined
      return store?.getState()?.activeModule
    })
    throw new Error(`导航「${label}」3 次均未落到 ${active}（当前 activeModule=${current}）`)
  }
  // 等模块内至少一个按钮渲染出来（父模块路由到默认子模块也要时间）
  await page.getByTestId('module-content').getByRole('button').first().waitFor({ timeout: 10000 })
}

function diagnosticsTag(rep: SweepReport): string {
  return [
    `total=${rep.total}`,
    `clicked=${rep.clicked.length}`,
    `blocked=${rep.blocked.length}`,
    `labelLess=${rep.labelLess.length}`,
    `clickFailed=${rep.clickFailed.length}`,
    `disabled=${rep.disabled.length}`,
    `dialogs=${rep.openedDialog.length}`,
    `dialogStuck=${rep.dialogStuck.length}`,
    `navigated=${rep.navigatedAway.length}`,
  ].join(' ')
}

/** 已知低危告警（HTML 模板瑕疵/React 规范/CSP 头格式）——降级为报告项，不进硬断言。
 *  说明：这些值得逐项修（已登记 follow-up），但不是运行时功能错误。 */
function isNoiseError(text: string): boolean {
  return (
    text.startsWith('Warning: validateDOMNesting') ||
    text.includes('Each child in a list should have a unique') ||
    text.includes('is using uppercase HTML') ||
    text.includes('Invalid DOM property') ||
    text.includes("The Content-Security-Policy directive")
  )
}

for (const mod of MODULES) {
  test(`click sweep: ${mod.label} (${mod.active})`, async ({ page }) => {
    // sweep 一模块要点几十个按钮（150ms 观察窗），设置模块 65+ 按钮 → 放宽
    test.setTimeout(300_000)
    const consoleErrors: string[] = []
    const warnErrors: string[] = []
    const pageErrors: string[] = []
    page.on('console', (msg) => {
      if (msg.type() !== 'error') return
      const text = msg.text().slice(0, 200)
      if (isNoiseError(text)) warnErrors.push(text)
      else consoleErrors.push(text)
    })
    page.on('pageerror', (err) => pageErrors.push(String(err).slice(0, 200)))

    await enterModule(page, mod.label, mod.active)
    const area = page.getByTestId('module-content')
    await expect(area).toBeVisible()

    const rep: SweepReport = {
      total: 0,
      clicked: [],
      blocked: [],
      labelLess: [],
      clickFailed: [],
      disabled: [],
      openedDialog: [],
      dialogStuck: [],
      navigatedAway: [],
    }

    // 恢复回调：遮罩关不掉（产品 modal 缺陷）→ 刷新 + 重进模块。
    // reload 重置 React 内存状态 → overlay 消失；重进走 sidebar 导航（同enterModule）
    const recover = async () => {
      await page.reload()
      await page.waitForFunction(
        () => {
          const store = (window as unknown as Record<string, unknown>).__useStore as
            | { getState: () => { bootPhase: string } }
            | undefined
          return store?.getState()?.bootPhase === 'ready'
        },
        { timeout: 20000 },
      )
      await enterModule(page, mod.label, mod.active)
    }

    await sweepButtons(page, area, rep, mod.active, recover)
    rep.total = await area.getByRole('button').count()

    // ── 交体检报告（CI 日志 + HTML report step 可展开）──
    console.log(`\n[CLICK-SWEEP] ${mod.label} :: ${diagnosticsTag(rep)}`)
    for (const e of rep.blocked) console.log(`  [blocked] ${e.name} (${e.reason})`)
    for (const e of rep.labelLess) console.log(`  [no-label] ${e.reason}`)
    for (const e of rep.clickFailed) console.log(`  [click-failed] ${e.name} :: ${e.reason}`)
    for (const e of rep.disabled) console.log(`  [disabled] ${e.name}`)
    for (const e of rep.openedDialog) console.log(`  [dialog] ${e.name}`)
    for (const e of rep.dialogStuck) console.log(`  [dialog-stuck] ${e.name} :: ${e.reason}`)
    for (const e of rep.navigatedAway) console.log(`  [navigated] ${e.name} :: ${e.reason}`)
    for (const e of warnErrors) console.log(`  [warn] ${e}`)
    for (const e of consoleErrors) console.log(`  [console.error] ${e}`)

    // ── L2 硬断言 ──
    // v1 口径：dialogStuck/clickFailed 为**报告项**（产品侧 modal 无 ESC/无名
    // 关闭按钮的缺陷族，单列 follow-up 跟踪）；硬断言只拦运行时级事故。
    expect(pageErrors, `模块 ${mod.label} 出现未捕获异常（白屏级）`).toEqual([])
    expect(
      consoleErrors,
      `模块 ${mod.label} 出现 console.error（RPC 失败/渲染错误会走这里）`,
    ).toEqual([])
    // 模块容器仍活着（ErrorBoundary 未接管）
    await expect(area).toBeVisible()
  })
}
