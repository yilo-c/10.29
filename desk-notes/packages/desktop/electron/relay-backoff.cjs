/**
 * 后端中继重连退避纯函数（V2.1 批次4，b4-e4）。
 *
 * 从 backend-relay.cjs 提取，纯函数无 electron 依赖，便于 vitest 单测覆盖。
 * 连续失败后指数退避（1s → 2s → 4s → … → 封顶 15s），避免连接风暴。
 */
const MAX_CONNECT_BACKOFF_MS = 15_000

/** 给定连续失败次数返回本次退避时长（ms）。0/负 → 立即重试。 */
function computeBackoffDelayMs(consecutiveFailures) {
  if (!Number.isFinite(consecutiveFailures) || consecutiveFailures <= 0) return 0
  return Math.min(1000 * Math.pow(2, consecutiveFailures - 1), MAX_CONNECT_BACKOFF_MS)
}

/** now 时刻是否允许发起连接（受退避期约束）。 */
function shouldAttemptConnect(now, lastConnectAttempt, consecutiveFailures) {
  if (consecutiveFailures === 0) return true
  return now - lastConnectAttempt >= computeBackoffDelayMs(consecutiveFailures)
}

module.exports = { MAX_CONNECT_BACKOFF_MS, computeBackoffDelayMs, shouldAttemptConnect }
