import { defineConfig } from 'playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  retries: 1,
  // 2026-09-16：HTML reporter 原先输出到 e2e-report/，而 e2e-report/index.html 是**已跟踪**
  // 文件 → 每跑一次 E2E 工作树就脏一次。改为 Playwright 惯例目录 playwright-report/
  // （.gitignore 覆盖），e2e-report/ 只留人工评审过的 notification 视觉基线。
  reporter: [['html', { outputFolder: 'playwright-report' }], ['list']],
  // C-12：真后端 harness——globalSetup 用 venv python 起隔离 headless 后端
  // （SIYI_DATA_DIR 临时目录 + 禁 desknotes 桥），就绪后经 SIYI_E2E_WS_URL 等
  // 环境变量传给 specs；teardown 杀进程树并删临时目录。
  // CI/无 Python 环境设 SIYI_E2E_SKIP_BACKEND=1 退回 browser-only 模式。
  globalSetup: './e2e/globalSetup.ts',
  use: {
    baseURL: 'http://localhost:5190',
    headless: true,
    viewport: { width: 900, height: 700 },
    actionTimeout: 10000,
    screenshot: 'only-on-failure',
    // 使用系统 Chrome/Edge，避免下载 Playwright 自带浏览器
    channel: 'chrome',
  },
  webServer: {
    command: 'npx vite --port 5190',
    url: 'http://localhost:5190',
    reuseExistingServer: false,
    // 2026-10-16：本机 vite 冷启动实测 >30s（10-14 起 E2E 就是慢因子），
    // 30s 预算必等超时 → 120s。
    timeout: 120000,
    cwd: '.',
  },
})
