import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './fixtures'
import { NOTIFICATION_PRESETS } from './globalSetup'

/**
 * 通知中心视觉基线截图（阶段 4 验收）+ 预置数据下的渲染断言（2026-09-12 并入可跑集）。
 *
 * 前置：C-12 harness 真后端（globalSetup 在 spawn 前把 NOTIFICATION_PRESETS 四条
 * 写进临时数据目录的 local_storage['notifications']）。后端没有 notification.create
 * 这类写入 RPC（通知只由 EventBus 订阅事件经 NotificationDispatcher 落库），所以
 * 预置走「先写数据、再起后端」；预置失败时 globalSetup 置 SIYI_E2E_NOTIF_PRESET=0，
 * 这里整组 skip，不给假绿。
 *
 * 断点：1440 / 1024 / 768 / 390（方案验收要求）。
 *
 * 截图输出（2026-09-16 修正「每次跑都改写已跟踪基线」）：
 *   - 运行时产物（每次跑都写，供人肉查看当次构图）→ test-results/notification-baseline/
 *     （test-results/ 在 .gitignore 内，不会弄脏工作树）
 *   - 已跟踪的验收基线 e2e-report/notification-baseline/*.png **只在显式刷新时**写，
 *     即 `SIYI_E2E_UPDATE_BASELINE=1 pnpm test:e2e`。默认只读比对，不落盘。
 *   原实现无条件 writeFileSync 到 e2e-report/，导致每跑一次 E2E 就有 4 个已跟踪 PNG
 *   字节数变化，把「视觉基线」变成了「运行噪声」，还逼迫收工前用 git show 手工还原。
 *
 * 竞态修正（原来是 4 条里挂 1 条的主因）：不再用
 *   waitForSelector('text=通知中心') + waitForFunction(body 文本不含「正在读取通知…」)
 *   + 固定 waitForTimeout(2000)
 * 这套组合——body 全文文本判定会在「列表尚未渲染完」时被误判为已通过，固定 sleep
 * 又扛不住 RPC 抖动。改为对定位器直接自动轮询：loading 占位隐藏 + 4 个
 * notification-item-* testid 可见，才继续截图。
 */

const BREAKPOINTS = [
  { name: 'desktop-1440', width: 1440, height: 900 },
  { name: 'desktop-1024', width: 1024, height: 768 },
  { name: 'tablet-768', width: 768, height: 1024 },
  { name: 'mobile-390', width: 390, height: 844 },
]

// 基于本 spec 文件位置推导输出目录，避免 test runner cwd 差异
const PKG_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..')
/** 运行时产物目录（.gitignore 覆盖，随便写）。 */
const RUNTIME_DIR = path.join(PKG_DIR, 'test-results', 'notification-baseline')
/** 已跟踪的验收基线目录——只有 SIYI_E2E_UPDATE_BASELINE=1 时才写。 */
const BASELINE_DIR = path.join(PKG_DIR, 'e2e-report', 'notification-baseline')
const UPDATE_BASELINE = process.env.SIYI_E2E_UPDATE_BASELINE === '1'
mkdirSync(RUNTIME_DIR, { recursive: true })
if (UPDATE_BASELINE) mkdirSync(BASELINE_DIR, { recursive: true })

const haveBackend = () => Boolean(process.env.SIYI_E2E_WS_URL)
const havePreset = () => process.env.SIYI_E2E_NOTIF_PRESET === '1'
const UNREAD = NOTIFICATION_PRESETS.filter((n) => !n.read)
const READ = NOTIFICATION_PRESETS.filter((n) => n.read)

const itemRow = (page: import('@playwright/test').Page, id: string) => page.getByTestId(`notification-item-${id}`)

/** 换视口后把主面板重新居中（与 fixtures 同一套几何），保证各断点基线构图稳定。 */
async function recenterPanel(page: import('@playwright/test').Page): Promise<void> {
  await page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: { getState: () => { panel: { x: number; y: number } }; setState: (s: Record<string, unknown>) => void }
    }).__useStore
    if (!store) return
    const s = store.getState()
    const cx = Math.round((window.innerWidth - Math.min(window.innerWidth * 0.92, 900)) / 2)
    const cy = Math.round(Math.max(window.innerHeight * 0.06, 16))
    store.setState({ panel: { ...s.panel, x: cx, y: cy } })
  })
}

async function gotoNotificationCenter(page: import('@playwright/test').Page): Promise<void> {
  // dev 模式暴露 __useStore，直达 notification 模块（侧边栏已收敛，通知入口不在栏上）
  await page.evaluate(() => {
    const store = (window as unknown as {
      __useStore?: { getState: () => { setActiveModule?: (m: string) => void } }
    }).__useStore
    store?.getState()?.setActiveModule?.('notification')
  })
  await expect(page.getByTestId('notification-workspace')).toBeVisible({ timeout: 20_000 })
  // loading 占位消失 = notification.list / notification.stats 两个 RPC 都已回
  await expect(page.getByText('正在读取通知...')).toBeHidden({ timeout: 20_000 })
  // 数据到位：逐条预置记录可见（用 testid 定位，不用「列表总条数」——
  // 后端可能另有真事件产生的通知，按条数硬等于 4 会自己制造 flaky）
  for (const preset of NOTIFICATION_PRESETS) {
    await expect(itemRow(page, preset.id)).toBeVisible({ timeout: 20_000 })
  }
}

test.describe('通知中心视觉基线', () => {
  // 慢机器上 fixtures 的 boot 预算（config 默认 30s，含 beforeEach）不够，放宽到 90s。
  test.describe.configure({ timeout: 90_000 })

  for (const bp of BREAKPOINTS) {
    test(`截图 ${bp.name}`, async ({ page }) => {
      test.skip(!haveBackend(), '需要 e2e 真后端 harness（未设 SIYI_E2E_WS_URL，browser-only 模式跳过）')
      test.skip(!havePreset(), '需要 globalSetup 预置 notification 记录（SIYI_E2E_NOTIF_PRESET!=1）')

      await page.setViewportSize({ width: bp.width, height: bp.height })
      await recenterPanel(page)
      await gotoNotificationCenter(page)

      // —— 预置数据如实呈现（内容级，不止"有节点"）——
      await expect(page.getByRole('heading', { name: '通知中心' }).first()).toBeVisible()
      for (const preset of NOTIFICATION_PRESETS) {
        await expect(itemRow(page, preset.id)).toContainText(preset.body)
      }
      // 优先级徽标：high→「高优先级」、urgent→「紧急」
      await expect(page.getByText('高优先级', { exact: true }).first()).toBeVisible()
      await expect(page.getByText('紧急', { exact: true }).first()).toBeVisible()
      // notification.stats 通道健康（证明 stats 也真跑通，不只是 list）
      await expect(page.getByTestId('notification-workspace')).toContainText('websocket')

      // 未读筛选是真过滤：未读预置条目留下、已读那条消失（点回「全部」后再截图）
      await page.getByRole('button', { name: '未读', exact: true }).click()
      for (const preset of UNREAD) await expect(itemRow(page, preset.id)).toBeVisible()
      for (const preset of READ) await expect(itemRow(page, preset.id)).toBeHidden()
      await page.getByRole('button', { name: '全部', exact: true }).click()
      for (const preset of NOTIFICATION_PRESETS) await expect(itemRow(page, preset.id)).toBeVisible()

      const buffer = await page.screenshot()
      writeFileSync(path.join(RUNTIME_DIR, `${bp.name}.png`), buffer)
      if (UPDATE_BASELINE) {
        writeFileSync(path.join(BASELINE_DIR, `${bp.name}.png`), buffer)
      }
    })
  }
})
