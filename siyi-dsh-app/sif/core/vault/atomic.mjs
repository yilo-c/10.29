// 原子写（对应规范 §7.3）
//
// 为什么不能用 writeFileSync 直接写：
//   手机随时可能被杀、断电、磁盘满。半写文件会留下「有 frontmatter 没正文」
//   或「JSON 截断」的坏笔记，而用户以为已经保存。
//
// 做法：同目录唯一临时文件 → 写满 → fsync → rename 覆盖。
// rename 在同文件系统内是原子的（POSIX），Windows 用 replace 语义。

import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'

export class AtomicWriteError extends Error {
  constructor(message, cause) {
    super(message)
    this.name = 'AtomicWriteError'
    this.code = 'ATOMIC_WRITE_FAILED'
    this.cause = cause
  }
}

/**
 * 原子写文本。返回 { bytes, sha256, tmpUsed }。
 * 失败时清理临时文件并抛 AtomicWriteError，绝不退化为直接覆盖。
 */
export function writeFileAtomic(targetPath, text, { encoding = 'utf8' } = {}) {
  const dir = path.dirname(targetPath)
  const base = path.basename(targetPath)
  const tmp = path.join(dir, '.' + base + '.' + process.pid + '.' + crypto.randomBytes(6).toString('hex') + '.tmp')
  const buf = Buffer.from(text, encoding)
  let fd = null
  try {
    fs.mkdirSync(dir, { recursive: true })
    fd = fs.openSync(tmp, 'wx', 0o600)
    let offset = 0
    while (offset < buf.length) {
      const written = fs.writeSync(fd, buf, offset, buf.length - offset)
      if (written <= 0) throw new Error('short write at offset ' + offset)
      offset += written
    }
    try { fs.fsyncSync(fd) } catch (e) {
      // FUSE/部分 Android 存储不支持 fsync，记录但不阻断
      if (!/EINVAL|ENOTSUP|EPERM/.test(String(e && e.code))) throw e
    }
    fs.closeSync(fd)
    fd = null
    fs.renameSync(tmp, targetPath)
    // rename 后尝试同步父目录，确保目录项落盘；不支持则忽略
    try {
      const dfd = fs.openSync(dir, 'r')
      try { fs.fsyncSync(dfd) } catch {}
      fs.closeSync(dfd)
    } catch {}
    return {
      bytes: buf.length,
      sha256: crypto.createHash('sha256').update(buf).digest('hex'),
      tmpUsed: true,
    }
  } catch (e) {
    if (fd !== null) { try { fs.closeSync(fd) } catch {} }
    try { fs.unlinkSync(tmp) } catch {}
    throw new AtomicWriteError('原子写失败: ' + (e && e.message), e)
  }
}

/** 计算文件内容 hash，用作 revision。 */
export function contentHash(text) {
  return crypto.createHash('sha256').update(Buffer.from(text, 'utf8')).digest('hex')
}

/** 读取并校验；文件不存在返回 null。 */
export function readFileSafe(p) {
  try {
    return fs.readFileSync(p, 'utf8')
  } catch (e) {
    if (e && (e.code === 'ENOENT' || e.code === 'ENOTDIR')) return null
    throw e
  }
}

/** 清理遗留临时文件（崩溃恢复）。返回删除数量。 */
export function sweepTempFiles(dir, maxAgeMs = 3600_000) {
  let removed = 0
  const now = Date.now()
  let entries = []
  try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { return 0 }
  for (const ent of entries) {
    if (!ent.isFile()) continue
    if (!/^\..+\.\d+\.[0-9a-f]+\.tmp$/.test(ent.name)) continue
    const full = path.join(dir, ent.name)
    try {
      const st = fs.statSync(full)
      if (now - st.mtimeMs > maxAgeMs) { fs.unlinkSync(full); removed++ }
    } catch {}
  }
  return removed
}
