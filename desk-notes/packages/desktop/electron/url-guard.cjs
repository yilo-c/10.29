/**
 * URL 安全判定纯函数（V2.1 批次4，b4-e2）。
 *
 * 从 main.cjs 的 window-open / will-navigate 处理器中提取，纯函数无副作用，
 * 便于 vitest 单测覆盖：
 * - isSafeExternalUrl：仅 http/https 放行交系统浏览器，file: 等一律拒绝
 * - isAllowedLocalNavigation：仅允许导航到本机后端/开发服务器
 *   （hostname 精确匹配 127.0.0.1 / localhost，杜绝 startswith 前缀绕过，
 *   如 http://127.0.0.1.evil.com 被拒）。
 */
function isSafeExternalUrl(raw) {
  try {
    const u = new URL(raw)
    return (u.protocol === 'http:' || u.protocol === 'https:') ? raw : ''
  } catch {
    return ''
  }
}

function isAllowedLocalNavigation(url) {
  try {
    const u = new URL(url)
    const host = u.hostname.toLowerCase()
    return (u.protocol === 'http:' || u.protocol === 'https:') &&
      (host === '127.0.0.1' || host === 'localhost')
  } catch {
    return false
  }
}

module.exports = { isSafeExternalUrl, isAllowedLocalNavigation }
