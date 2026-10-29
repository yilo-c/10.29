/**
 * 显式吞掉错误的占位符。
 *
 * 用法：
 *   try { ... } catch (e) { swallowError('module:reason', e) }
 *
 * 设计意图：
 * - 把原本零意识的 `catch {}` 转成可 grep、可埋点的显式动作；
 * - 默认开发环境 `console.debug`、生产静默，避免引入新噪音；
 * - **不可** 用于用户可感知的失败路径 —— 那里必须 toast/notify，
 *   `swallowError` 仅适用 best-effort 操作（native API 不可用 / 缓存清理失败 / 可重试的轮询等）。
 *
 * P1-20: contentSync / WordbookView / secureStore 等历史空 catch 统一改走此处。
 */
export function swallowError(context: string, err?: unknown): void {
  // 仅在 vitest / 浏览器 dev 输出，避免污染生产日志
  // eslint-disable-next-line no-undef
  const isDev = typeof import.meta !== 'undefined' && (import.meta as { env?: { DEV?: boolean } }).env?.DEV
  if (isDev) {
    // eslint-disable-next-line no-console
    console.debug('[swallow] ' + context, err)
  }
}
