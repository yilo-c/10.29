/**
 * 路径越界防护纯函数（V2.1 批次4，b4-e3）。
 *
 * 从 file-store.cjs 提取，纯函数无 electron 依赖，便于 vitest 单测覆盖。
 * 用 path.relative 判越界，杜绝旧实现 `resolved.startsWith(allowed)` 的
 * 前缀碰撞误放行（如 /data/user_evil 相对 /data/user 被误判为在目录内）。
 */
const path = require('path')

/**
 * 解码 URL 编码的路径分隔符/点号（%2f→/、%5c→\、%2e→.、%25→%），
 * 并做有界多轮解码防双编码绕过（%252f → %2f → /）。
 * path.resolve 不识别 %2f，攻击者可用 notes/..%2f..%2f 绕过 .. 校验。
 */
function _decodeEncodedTraversal(raw) {
  let s = String(raw)
  for (let i = 0; i < 3; i++) {
    const next = s
      .replace(/%25/gi, '%')
      .replace(/%2f/gi, '/')
      .replace(/%5c/gi, '\\')
      .replace(/%2e/gi, '.')
    if (next === s) break
    s = next
  }
  return s
}

function guardPath(allowedDir, userPath) {
  const allowed = path.resolve(allowedDir)
  const decoded = _decodeEncodedTraversal(userPath)
  const resolved = path.resolve(allowed, decoded)
  const rel = path.relative(allowed, resolved)
  if (rel === '..' || rel.startsWith('..' + path.sep) || path.isAbsolute(rel)) {
    throw new Error(`Path traversal denied: ${userPath}`)
  }
  return resolved
}

module.exports = { guardPath, _decodeEncodedTraversal }
