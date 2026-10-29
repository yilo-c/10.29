/**
 * Bridges barrel — 把手机端各个领域到桌面 RPC 的薄封装统一导出。
 *
 * 调用约定：所有桥都基于 `callRpc`（WS 优先 + HTTP 回落），失败静默回退（业务侧可拿到空数组或 null）。
 *
 * ⚠️ 命名冲突处理：`cronBridge` / `agentBridge` / `auditBridge` 都有 `history`
 * 和 `runNow` 这类同义函数，用 `export *` 会互相覆盖（TS2308）。
 * 因此这三个桥改为**命名空间导出**，调用方写成 `cron.history()` / `agent.history()`，
 * 语义更清楚，也避免以后再加桥时继续撞名。
 * 其余桥保持 `export *`，不破坏既有调用点。
 */
export * from './reminderBridge'
export * from './tagsBridge'
export * from './inboxBridge'
export * from './notesBridge'
export * from './tasksBridge'
export * from './memoryBridge'
export * from './dailyBridge'
export * from './weatherBridge'
export * from './settingsBridge'
export * from './vocabBridge'
export * from './systemBridge'
export * from './providerBridge'
export * from './aiBridge'
export * from './learningBridge'
export * from './syncBridge'
export * from './infoBridge'

/* 命名空间导出：规避 history / runNow 等同名函数冲突 */
export * as cron from './cronBridge'
export * as agent from './agentBridge'
export * as audit from './auditBridge'
