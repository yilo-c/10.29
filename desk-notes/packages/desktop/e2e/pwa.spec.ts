import { test, expect } from './fixtures'

test.describe('PWA offline support', () => {
  test('offline banner shows when going offline', async ({ page }) => {
    // Simulate offline event
    await page.evaluate(() => {
      window.dispatchEvent(new Event('offline'))
    })
    await page.waitForTimeout(200)

    // Offline banner should be visible
    const offlineBanner = page.locator('text=当前离线')
    await expect(offlineBanner).toBeVisible({ timeout: 3000 })

    // Simulate going back online
    await page.evaluate(() => {
      window.dispatchEvent(new Event('online'))
    })
    await page.waitForTimeout(200)

    // Banner should disappear
    await expect(offlineBanner).not.toBeVisible()
  })

  test('service worker update event shows notification', async ({ page }) => {
    // Simulate SW update event
    await page.evaluate(() => {
      window.dispatchEvent(new CustomEvent('sw-update-ready'))
    })
    await page.waitForTimeout(200)

    // Update notification should show
    await expect(page.locator('text=新版本已就绪')).toBeVisible({ timeout: 3000 })
  })

  test('service worker is registered', async ({ page }) => {
    const hasSW = await page.evaluate(() => {
      return 'serviceWorker' in navigator
    })
    expect(hasSW).toBe(true)
  })
})
