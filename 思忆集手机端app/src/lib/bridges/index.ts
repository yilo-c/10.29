/**
 * Bridges barrel — 把手机端各个领域到桌面 RPC 的薄封装统一导出。
 *
 * 调用约定：所有桥都基于 `callRpc`（WS 优先 + HTTP 回落），失败静默回退（业务侧可拿到空数组或 null）。
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
export * from './cronBridge'
export * from './agentBridge'
export * from './auditBridge'
