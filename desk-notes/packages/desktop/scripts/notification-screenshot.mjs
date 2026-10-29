/**
 * 通知中心视觉基线截图（阶段 4 验收）。
 *
 * 前置：后端 ws://127.0.0.1:18765 运行 + Vite dev server :5190 运行 + 已预置通知数据。
 * 运行：node scripts/notification-screenshot.mjs
 * 输出：packages/desktop/e2e-report/notification-baseline/
 */
import { chromium } from 'playwright'
import { mkdirSync, writeFileSync } from 'node:fs'
import path from 'node:path'

const OUT = path.join(process.cwd(), 'e2e-report', 'notification-baseline')
mkdirSync(OUT, { recursive: true })

const BREAKPOINTS = [
  { name: 'desktop-1440', width: 1440, height: 900 },
  { name: 'desktop-1024', width: 1024, height: 768 },
  { name: 'tablet-768', width: 768, height: 1024 },
  { name: 'mobile-390', width: 390, height: 844 },
]

const browser = await chromium.launch({ channel: 'chrome', headless: true })

for (const bp of BREAKPOINTS) {
  const page = await browser.newPage({ viewport: { width: bp.width, height: bp.height } })
  await page.goto('http://localhost:5190')
  await page.waitForSelector('[data-testid="nav-sidebar"]', { timeout: 20000 })
  await page.evaluate(() => {
    const store = window.__useStore
    store?.getState()?.setActiveModule?.('notification')
  })
  await page.waitForSelector('[data-testid="notification-workspace"]', { timeout: 15000 })
  await page.waitForFunction(
    () => !document.body.textContent.includes('正在读取通知...'),
    { timeout: 15000 },
  )
  await page.waitForTimeout(2000)
  const buffer = await page.screenshot()
  writeFileSync(path.join(OUT, `${bp.name}.png`), buffer)
  console.log(`saved ${bp.name}.png (${(buffer.length / 1024).toFixed(0)} KB)`)
  await page.close()
}

await browser.close()
console.log(`done -> ${OUT}`)
