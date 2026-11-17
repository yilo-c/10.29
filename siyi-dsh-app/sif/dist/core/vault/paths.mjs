// Vault 路径约束（对应规范 §7.1 / §7.3）
//
// 为什么单独一个模块：这是 P0 安全边界。现有 siyi-vault.js 的
// handleSave() 接受外部 id 后直接 path.join(dir, id + '.md')，
// 没有复用 findById() 的 ULID 校验，也没有走 safePath()。
// 这里把「id 合法性 + 路径归属」做成唯一实现，HTTP 和 AI 工具都调它。

import path from 'node:path'
import fs from 'node:fs'

/** ULID：26 位 Crockford Base32（不含 I L O U）。 */
const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/

export class PathViolation extends Error {
  constructor(message, code = 'INVALID_ARGUMENT') {
    super(message)
    this.name = 'PathViolation'
    this.code = code
  }
}

/** 校验 id 必须是 ULID。新建时禁止外部传入，更新时只接受已存在 id。 */
export function assertUlid(id) {
  if (typeof id !== 'string') throw new PathViolation('id 必须是字符串')
  if (!ULID_RE.test(id)) throw new PathViolation('id 必须是 26 位 ULID: ' + JSON.stringify(String(id).slice(0, 40)))
  return id
}

export function isUlid(id) {
  return typeof id === 'string' && ULID_RE.test(id)
}

/**
 * 生成 ULID。时间戳 48bit + 随机 80bit，与现有 vault 文件名一致。
 */
export function newUlid(now = Date.now()) {
  const CH = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
  let ts = ''
  let x = now
  for (let i = 0; i < 10; i++) { ts = CH[x % 32] + ts; x = Math.floor(x / 32) }
  const buf = new Uint8Array(16)
  globalThis.crypto.getRandomValues(buf)
  let rnd = ''
  for (let i = 0; i < 16; i++) rnd += CH[buf[i] % 32]
  return ts + rnd
}

/**
 * 断言 target 在 root 内。
 *
 * 三道检查缺一不可：
 *   1. 词法 resolve 后前缀校验（挡 ../ 与绝对路径）
 *   2. realpath 校验（挡符号链接逃逸）——目标存在时
 *   3. realpath 父目录校验（挡「新建文件」时父目录是符号链接）
 */
export function assertInside(root, target) {
  const realRoot = fs.realpathSync(root)
  const resolved = path.resolve(realRoot, target)

  // 1. 词法层
  if (resolved !== realRoot && !resolved.startsWith(realRoot + path.sep)) {
    throw new PathViolation('路径越界（词法层）: ' + target)
  }

  // 2. 目标已存在 → realpath 校验
  let exists = false
  try { exists = fs.existsSync(resolved) } catch {}
  if (exists) {
    const realTarget = fs.realpathSync(resolved)
    if (realTarget !== realRoot && !realTarget.startsWith(realRoot + path.sep)) {
      throw new PathViolation('路径越界（符号链接层）: ' + target)
    }
    return realTarget
  }

  // 3. 目标不存在 → 校验最近的已存在祖先
  let probe = path.dirname(resolved)
  while (probe !== realRoot && probe.startsWith(realRoot + path.sep)) {
    if (fs.existsSync(probe)) {
      const realProbe = fs.realpathSync(probe)
      if (realProbe !== realRoot && !realProbe.startsWith(realRoot + path.sep)) {
        throw new PathViolation('路径越界（父目录符号链接）: ' + target)
      }
      break
    }
    probe = path.dirname(probe)
  }
  return resolved
}

/** 从 note 的绝对路径推出 vault 相对逻辑路径（POSIX 风格，用于跨平台）。 */
export function toLogicalPath(root, absolute) {
  const realRoot = fs.realpathSync(root)
  const rel = path.relative(realRoot, absolute)
  if (rel.startsWith('..')) throw new PathViolation('无法推导相对路径: ' + absolute)
  return rel.split(path.sep).join('/')
}
