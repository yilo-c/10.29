import { defineConfig, devices } from '@playwright/test'

/**
 * 手机端 E2E 配置（Playwright / Chromium）
 *
 * 为什么需要它：
 *   已有的 201 个单测全是「纯函数逻辑」测试，覆盖不到「App 打开后能不能用」。
 *   一次 8500 行的大改可以做到单测全绿、真机上却白屏或点不动。
 *   本套 E2E 只验证一件事：**用户能不能把主流程走通**。
 *
 * 与 CI 的关系（见 .github/workflows/build-apk.yml）：
 *   e2e 不绿 → 不出 APK。这样「改坏了」由机器判定，而不是靠人肉试。
 *
 * 关键约束（读代码得出，改测试前务必知悉）：
 *   1. 所有 Tab 常驻挂载（App.tsx P1-18），切换只切 display。
 *      ⇒ 断言必须用 toBeVisible()，不能用 toBeAttached()/count()，
 *        否则隐藏 Tab 的元素也会被算作「存在」，测试会假绿。
 *   2. 首启有 Onboarding 挡屏，靠 localStorage['siyi-onboarding-done'] 判定。
 *      本配置在 storageState 里预置该键，并在每个 spec 的 beforeEach 兜底。
 *   3. LockScreen 在 appLocked/appPin 皆空时返回 null，空环境不挡；无需处理。
 *   4. 视口取手机尺寸（390x844，接近 Redmi 常见逻辑分辨率），
 *      因为布局有大量移动端断点，桌面视口会走到不同分支，测了也不代表手机。
 */
export default defineConfig({
  testDir: './e2e',
  // 只跑「真浏览器」用例；vitest 的单测在 src/**/__tests__ 下，两者互不干扰
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  // CI 上禁止 .only，防止误提交导致只跑一个用例还显示全绿
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // 手机项目并发过高会互相抢 IndexedDB，且 debug 时日志更乱；1 个 worker 最稳
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: 45_000,
  expect: { timeout: 10_000 },

  use: {
    baseURL: 'http://127.0.0.1:4173',
    // 只保留失败现场：trace 用于回看点击序列，screenshot 用于肉眼确认界面
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    ...devices['Pixel 7'],
  },

  projects: [
    {
      name: 'chromium-mobile',
      use: { ...devices['Pixel 7'], browserName: 'chromium' },
    },
  ],

  /**
   * 用 vite preview 起产物服务，而不是 vite dev：
   *   - 测的是 build 出来的真实产物，与 CI 打包进 APK 的 dist 完全一致
   *   - dev 模式有 HMR/开发报错浮层，会干扰断言
   * ⚠️ 本地 proot 下 `vite preview --host 0.0.0.0` 会因 uv_interface_addresses
   *    报 err 13，故只绑 127.0.0.1。
   */
  webServer: {
    command: 'npx vite build && npx vite preview --host 127.0.0.1 --port 4173 --strictPort',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
