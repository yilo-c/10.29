/** 提供商配置（前端安全版本，无明文 api_key） */
export interface ProviderConfig {
  name: string
  api_base: string
  model: string
  models: string[]
  is_local: boolean
  is_fallback: boolean
  enabled: boolean
  description: string
  has_api_key: boolean
  /** 用途：general | primary | fallback | local | translation | summarization | coding */
  purpose?: string
  /** 健康检查结果（仅前端使用） */
  health?: ProviderHealth
}

/** 健康检查结果 */
export interface ProviderHealth {
  name: string
  available: boolean
  message: string
  models: string[]
  last_checked?: number
}

/** RPC 操作结果 */
export interface RpcResult {
  success: boolean
  message: string
}

/** Kiro-gateway 状态 */
export interface KiroStatus {
  running: boolean
  accounts: number
  message: string
  models?: string[]
}
