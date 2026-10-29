/**
 * 显式吞掉错误的占位符。
 *
 * 用法：
 *   try { ... } catch (e) { swallowError('module:reason', e) }
 *
 * 见 思忆集手机端app/src/lib/swallowError.ts —— 桌面与手机端共用同一约定。
 */
export function swallowError(context: string, err?: unknown): void {
  const isDev = typeof import.meta !== 'undefined' && (import.meta as { env?: { DEV?: boolean } }).env?.DEV
  if (isDev) {
     
    console.debug('[swallow] ' + context, err)
  }
}
