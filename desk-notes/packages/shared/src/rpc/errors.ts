/* ── 通用 RPC 错误类型 — 统一前端错误处理 ── */

/** RPC 调用结果信封 */
export interface RpcResult<T = unknown> {
  success: boolean
  data?: T
  error?: string
}

/** 前端分类后的错误类型 */
export type RpcErrorKind =
  | 'network'      // WebSocket 断连 / 网络不可用
  | 'timeout'      // 请求超时
  | 'auth'         // 认证失败（token 无效）
  | 'not_found'    // 资源不存在
  | 'validation'   // 参数校验失败
  | 'rate_limit'   // 频率限制
  | 'file_too_large' // 文件超过大小限制
  | 'unsupported_type' // 不支持的文件类型
  | 'server_error' // 后端内部错误
  | 'unknown'      // 未分类错误

/** 分类后的 RPC 错误 */
export interface RpcError {
  kind: RpcErrorKind
  message: string
  original?: unknown
}

/**
 * 将 RPC 响应或异常分类为统一错误类型。
 */
export function classifyRpcError(
  result: RpcResult | null,
  error?: unknown,
): RpcError {
  if (error instanceof TypeError && (error as Error).message === 'Failed to fetch') {
    return { kind: 'network', message: '网络连接失败' }
  }

  if (error && typeof error === 'object' && 'message' in error) {
    const msg = (error as Error).message
    if (msg.includes('timeout') || msg.includes('Timeout')) {
      return { kind: 'timeout', message: '请求超时', original: error }
    }
  }

  if (!result) {
    return { kind: 'unknown', message: '无响应', original: error }
  }

  if (result.success) {
    return { kind: 'unknown', message: '', original: result }
  }

  const errMsg = result.error || ''

  if (errMsg.includes('auth')) {
    return { kind: 'auth', message: errMsg, original: result }
  }
  if (errMsg.includes('not found') || errMsg.includes('not_found')) {
    return { kind: 'not_found', message: errMsg, original: result }
  }
  if (errMsg.includes('too large')) {
    return { kind: 'file_too_large', message: '文件过大', original: result }
  }
  if (errMsg.includes('rate') || errMsg.includes('limit')) {
    return { kind: 'rate_limit', message: errMsg, original: result }
  }
  if (errMsg.includes('invalid') || errMsg.includes('required')) {
    return { kind: 'validation', message: errMsg, original: result }
  }

  return { kind: 'server_error', message: errMsg || '服务端错误', original: result }
}

/**
 * 给用户展示友好的错误信息。
 */
export function formatRpcError(error: RpcError): string {
  const fallback: Record<RpcErrorKind, string> = {
    network: '网络连接失败，请检查连接',
    timeout: '请求超时，请重试',
    auth: '认证失败',
    not_found: '资源不存在',
    validation: '参数错误',
    rate_limit: '请求过于频繁',
    file_too_large: '文件大小超过限制',
    unsupported_type: '不支持的文件类型',
    server_error: '服务端错误',
    unknown: '未知错误',
  }
  return error.message || fallback[error.kind]
}
