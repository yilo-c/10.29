import { chromium } from 'playwright'

const browser = await chromium.launch({ channel: 'chrome', headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
await page.goto('http://localhost:5190')
await page.waitForSelector('[data-testid="nav-sidebar"]', { timeout: 20000 })
await page.evaluate(() => { window.__useStore?.getState()?.setActiveModule?.('notification') })
await page.waitForSelector('[data-testid="notification-workspace"]', { timeout: 15000 })
await page.waitForFunction(() => !document.body.textContent.includes('正在读取通知...'), { timeout: 15000 })
await page.waitForTimeout(1500)

const result = await page.evaluate(() => {
  const bodyText = document.body.textContent || ''
  const items = document.querySelectorAll('[data-testid^="notification-item-"]')
  const badges = [...document.querySelectorAll('span')].filter(el => /紧急|高优先级/.test(el.textContent || ''))
  const unreadDots = document.querySelectorAll('span.h-2.w-2.rounded-full.bg-fluent-blue')
  const errorAlert = !!document.querySelector('[role="alert"]')
  return {
    hasTitle: bodyText.includes('通知中心'),
    hasStatCards: bodyText.includes('通知总数') && bodyText.includes('未读'),
    itemCount: items.length,
    priorityBadges: badges.map(b => b.textContent),
    unreadDotCount: unreadDots.length,
    errorAlert,
    bodySnippet: bodyText.slice(0, 300).replace(/\s+/g, ' '),
  }
})
console.log(JSON.stringify(result, null, 2))
await browser.close()
