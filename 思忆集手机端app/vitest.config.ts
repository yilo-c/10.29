import { defineConfig } from 'vitest/config'

/**
 * vitest 单测配置（纯函数逻辑层）。
 *
 * ⚠️ 必须排除 e2e/：
 *   Playwright 的 *.spec.ts 与 vitest 的扫描规则会撞车，vitest 默认会把
 *   e2e/smoke.spec.ts 当成自己的测试收集，然后因为 test/expect 来自
 *   @playwright/test 而抛出 "Playwright Test did not expect test() to be called here"。
 *   两套测试的运行器必须隔离：vitest 只跑 src/**\/__tests__，Playwright 只跑 e2e/。
 */
export default defineConfig({
  test: {
    globals: true,
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['node_modules/**', 'dist/**', 'e2e/**'],
  },
})
