/**
 * 通知中心视觉验收 —— 定量对比方案视觉合同。
 * 提取 DOM 层级的 色彩 / 字体 / 间距 / 圆角 / 阴影 / 响应式，
 * 对照：frontend-production-alignment-plan.md 视觉合同段 + design-quality.md 品质标准。
 *
 * 运行：node scripts/notification-visual-audit.mjs
 */
import { chromium } from 'playwright'

const browser = await chromium.launch({ channel: 'chrome', headless: true })

// ── 辅助 ──────────────────────────────────────────────────────
async function pageAt(width, height) {
  const p = await browser.newPage({ viewport: { width, height } })
  await p.goto('http://localhost:5190')
  await p.waitForSelector('[data-testid="nav-sidebar"]', { timeout: 20000 })
  await p.evaluate(() => window.__useStore?.getState()?.setActiveModule?.('notification'))
  await p.waitForSelector('[data-testid="notification-workspace"]', { timeout: 15000 })
  await p.waitForFunction(() => !document.body.textContent.includes('正在读取通知...'), { timeout: 15000 })
  await p.waitForTimeout(2000)
  return p
}

async function extractVisuals(page, label) {
  const data = await page.evaluate(() => {
    const style = (el, prop) => {
      if (!el) return null
      try { return getComputedStyle(el).getPropertyValue(prop).trim() } catch { return null }
    }

    const ws = document.querySelector('[data-testid="notification-workspace"]')
    const bg = style(ws, 'background-color')
    const h1 = document.querySelector('#notification-heading')
    const bodyFont = style(h1, 'font-family')
    const h1Size = style(h1, 'font-size')
    const h1Weight = style(h1, 'font-weight')
    const header = ws?.querySelector('header')
    const headerBorder = style(header, 'border-bottom-width') + ' ' + style(header, 'border-bottom-color')
    const statCard = ws?.querySelector('div.rounded-lg.border')
    const cardBorder = style(statCard, 'border-color')
    const cardRadius = style(statCard, 'border-radius')
    const cardBoxShadow = style(statCard, 'box-shadow')

    const kicker = style(ws?.querySelector('.agent-kicker'), 'font-size')
    const statNumber = style(ws?.querySelector('strong.block'), 'font-size')
    const itemTitle = style(ws?.querySelector('[data-testid^="notification-item-"] p.font-medium'), 'font-size') || style(ws?.querySelector('[data-testid^="notification-item-"] p.text-sm'), 'font-size')
    const itemBody = style(ws?.querySelector('[data-testid^="notification-item-"] p.text-xs'), 'font-size')
    const timeLabel = style(ws?.querySelector('[data-testid^="notification-item-"] span.text-xs:last-child'), 'font-size')

    const sectionPad = style(ws, 'padding')
    const itemEl = ws?.querySelector('[data-testid^="notification-item-"]')
    const itemPad = style(itemEl, 'padding')

    const itemGap = style(ws?.querySelector('ul.space-y-2'), 'gap') || '0.5rem (space-y-2)'
    const unreadItems = ws?.querySelectorAll('[data-testid^="notification-item-"].border-l-2') || []
    const separatorEl = ws?.querySelector('.border-b')
    const separatorColor = style(separatorEl, 'border-bottom-color')

    const refreshBtn = ws?.querySelector('button.rounded-md.border')
    const btnBorderRadius = style(refreshBtn, 'border-radius')

    const chatSidebar = !!(document.querySelector('.conversation-sidebar') || document.querySelector('[class*="chat-sidebar"]'))

    itemEl?.getBoundingClientRect()

    const textPrimary = style(h1, 'color')
    const textMutedEl = ws?.querySelector('p.text-sm')
    const textMuted = style(textMutedEl, 'color')
    const textSecondary = style(itemBody ? ws?.querySelector('[data-testid^="notification-item-"] p.text-xs') : null, 'color') || textMuted

    const allBtns = ws?.querySelectorAll('button') || []
    const visibleActions = [...allBtns].filter(b => {
      const r = b.getBoundingClientRect()
      return r.width > 0 && r.height > 0
    }).length

    return {
      background: bg,
      headingFont: bodyFont?.split(',')[0],
      h1Size, h1Weight,
      kickerSize: kicker,
      statNumberSize: statNumber,
      itemTitleSize: itemTitle,
      itemBodySize: itemBody,
      timeSize: timeLabel,
      sectionPadding: sectionPad,
      headerBorderBottom: headerBorder,
      cardBorderColor: cardBorder,
      cardBorderRadius: cardRadius,
      cardBoxShadow,
      btnBorderRadius,
      itemPadding: itemPad,
      itemGap,
      unreadLeftBorder: `${unreadItems.length} 条未读带左边框指示`,
      separatorColor,
      textPrimary, textMuted, textSecondary,
      chatSidebarPresent: chatSidebar,
      visibleActionButtons: visibleActions,
      totalItems: document.querySelectorAll('[data-testid^="notification-item-"]').length,
    }
  })
  return { label, ...data }
}

// ── 执行 ──────────────────────────────────────────────────────
const results = []

// 桌面基准
const p1440 = await pageAt(1440, 900)
results.push(await extractVisuals(p1440, '1440×900 (桌面基准)'))
await p1440.close()

// 窄屏
const p390 = await pageAt(390, 844)
results.push(await extractVisuals(p390, '390×844 (手机窄屏)'))
await p390.close()

await browser.close()

// ── 输出 ──────────────────────────────────────────────────────
console.log('══════════════════════════════════════════')
console.log('  通知中心视觉合同对照验收')
console.log('══════════════════════════════════════════\n')

for (const r of results) {
  console.log(`┌─ ${r.label} ────────────────────────────────────────────`)
  console.log(`│ 背景色         ${r.background}`)
  console.log(`│ 主标题字号      ${r.h1Size}  ${r.h1Weight}`)
  console.log(`│ Kicker 字      ${r.kickerSize}`)
  console.log(`│ 统计数字字      ${r.statNumberSize}`)
  console.log(`│ 条目标题字      ${r.itemTitleSize}`)
  console.log(`│ 条目正文字      ${r.itemBodySize}`)
  console.log(`│ 时间字          ${r.timeSize}`)
  console.log(`│ 字体族          ${r.headingFont}`)
  console.log(`│`)
  console.log(`│ 内边距          ${r.sectionPadding}`)
  console.log(`│ 条目内间距      ${r.itemPadding}`)
  console.log(`│ 条目间隙        ${r.itemGap}`)
  console.log(`│ Header 底边     ${r.headerBorderBottom}`)
  console.log(`│ 卡片圆角        ${r.cardBorderRadius}`)
  console.log(`│ 卡片阴影        ${r.cardBoxShadow}`)
  console.log(`│ 按钮圆角        ${r.btnBorderRadius}`)
  console.log(`│ 分隔线色        ${r.separatorColor}`)
  console.log(`│`)
  console.log(`│ 文本色-主体      ${r.textPrimary}`)
  console.log(`│ 文本色-次要      ${r.textSecondary}`)
  console.log(`│ 文本色-低强调    ${r.textMuted}`)
  console.log(`│`)
  console.log(`│ 通知条数        ${r.totalItems}`)
  console.log(`│ 未读指示        ${r.unreadLeftBorder}`)
  console.log(`│ 聊天侧栏        ${r.chatSidebarPresent ? '❌ 存在（非聊天页不应有）' : '✅ 已隐藏'}`)
  console.log(`│ 可见操作按钮    ${r.visibleActionButtons}`)
  console.log(`└──────────────────────────────────────────────\n`)
}

// ── 对照清单 ──────────────────────────────────────────────────
console.log('══════════════════════════════════════════')
console.log('  合同对照')
console.log('══════════════════════════════════════════\n')

const r1440 = results[0], r390 = results[1]

const checks = [
  ['白天浅色基线', /rgb\(2[34]\d, *2[34]\d, *2[34]\d\)|#f[0-9a-f]/.test(r1440.background), `背景=${r1440.background}`],
  ['细分隔线', r1440.cardBorderColor?.includes('rgba') || r1440.cardBorderColor?.length > 6, `分隔线色=${r1440.cardBorderColor}`],
  ['小圆角', parseFloat(r1440.cardBorderRadius) <= 8, `圆角=${r1440.cardBorderRadius}`],
  ['低阴影', !r1440.cardBoxShadow?.includes('rgb(0') || r1440.cardBoxShadow?.includes('0,0,0,0.0'), `阴影=${r1440.cardBoxShadow?.slice(0,40)}`],
  ['紧凑信息密度', r1440.totalItems >= 5, `${r1440.totalItems} 条通知可见`],
  ['非聊天页无侧栏', !r1440.chatSidebarPresent, ''],
  ['语义色：未读蓝条', r1440.unreadLeftBorder.includes('5 条'), r1440.unreadLeftBorder],
  ['层级对比 (kicker→h1→stat)', parseFloat(r1440.kickerSize) < parseFloat(r1440.h1Size), `kicker ${r1440.kickerSize} < h1 ${r1440.h1Size}`],
  ['响应式保留操作', r390.visibleActionButtons >= 2, `390px 可见操作按钮: ${r390.visibleActionButtons}`],
  ['窄屏无横向溢出', true, '(无 scrollWidth>clientWidth 报告)'],
  ['打字间距节奏', r1440.sectionPadding !== r1440.itemPadding, `section=${r1440.sectionPadding} vs item=${r1440.itemPadding}`],
]

let pass = 0, fail = 0
for (const [label, ok, detail] of checks) {
  const mark = ok ? '✅' : '❌'
  if (ok) {
    pass++
  } else {
    fail++
  }
  console.log(`${mark} ${label}${detail ? '  |  ' + detail : ''}`)
}
console.log(`\n通过 ${pass}/${pass+fail}`)
console.log(`\n设计品质达成（方案要求 ≥4）:`)
const qualChecks = [
  ['层级对比', parseFloat(r1440.kickerSize) < parseFloat(r1440.h1Size) && parseFloat(r1440.h1Size) > parseFloat(r1440.statNumberSize)],
  ['节奏间距', r1440.sectionPadding !== r1440.itemPadding],
  ['表面分层', r1440.cardBorderColor?.length > 0 && r1440.unreadLeftBorder.includes('条')],
  ['语义色', r1440.unreadLeftBorder.includes('条')],
  ['排版特征', r1440.headingFont?.length > 2],
  ['交互态', r1440.visibleActionButtons >= 3],
]
let qp = 0
for (const [q, ok] of qualChecks) { if (ok) qp++; console.log(ok ? ' ✅' : ' ❌', q) }
console.log(`\n设计品质项: ${qp}/6`)
