// 给运行中的 siyi-vault.js 打路径安全补丁（幂等）
//
// 修复内容（对应复审 P0）：
//   1. 新建时不再接受外部 id，id 一律服务端生成
//   2. 更新/删除按 id 在已知笔记里定位，禁止拼接任意路径
//   3. 保存前做 realpath 归属校验，防 ../ 与符号链接
//   4. 保存改为原子写（临时文件 + rename），不留半文件
//   5. 加 expectedRevision 可选校验，防并发覆盖

import fs from 'node:fs'
import path from 'node:path'

const TARGET = process.argv[2] || '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const MARK = 'SIYI-PATHFIX-20261118'

let src = fs.readFileSync(TARGET, 'utf8')

if (src.includes(MARK)) {
  console.log('SKIP: 已打过补丁（标记存在）')
  process.exit(0)
}

// ── 1. 在 ulid() 之后插入安全辅助函数 ──────────────────────
const anchorUlid = "  return ts + rnd\n}\n"
const helpers = `  return ts + rnd
}

// ── 路径安全（${MARK}）─────────────────────────────────────────
// 为什么需要：handleSave 原先直接 path.join(dir, data.id + '.md')，
// 外部传入的 id 含 ../ 就能把文件写到 vault 外（已用隔离 PoC 证实）。
// 三道检查：ULID 白名单 → realpath 归属 → 父目录 realpath。
const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/

function assertUlid(id) {
  if (typeof id !== 'string' || !ULID_RE.test(id)) {
    const e = new Error('id 必须是 26 位 ULID')
    e.code = 'INVALID_ARGUMENT'
    throw e
  }
  return id
}

/** 断言 target 在 VAULT 内；目标不存在时校验最近已存在祖先。 */
function assertInsideVault(target) {
  const realRoot = fs.realpathSync(VAULT)
  const resolved = path.resolve(realRoot, target)
  if (resolved !== realRoot && !resolved.startsWith(realRoot + path.sep)) {
    const e = new Error('路径越界')
    e.code = 'INVALID_ARGUMENT'
    throw e
  }
  let exists = false
  try { exists = fs.existsSync(resolved) } catch {}
  if (exists) {
    const real = fs.realpathSync(resolved)
    if (real !== realRoot && !real.startsWith(realRoot + path.sep)) {
      const e = new Error('路径越界（符号链接）')
      e.code = 'INVALID_ARGUMENT'
      throw e
    }
    return real
  }
  let probe = path.dirname(resolved)
  while (probe !== realRoot && probe.startsWith(realRoot + path.sep)) {
    if (fs.existsSync(probe)) {
      const real = fs.realpathSync(probe)
      if (real !== realRoot && !real.startsWith(realRoot + path.sep)) {
        const e = new Error('路径越界（父目录符号链接）')
        e.code = 'INVALID_ARGUMENT'
        throw e
      }
      break
    }
    probe = path.dirname(probe)
  }
  return resolved
}

/** 原子写：同目录临时文件 + fsync + rename，失败不退化直写。 */
function writeFileAtomicSync(targetPath, text) {
  const dir = path.dirname(targetPath)
  const base = path.basename(targetPath)
  const tmp = path.join(dir, '.' + base + '.' + process.pid + '.' + Math.random().toString(36).slice(2) + '.tmp')
  const buf = Buffer.from(text, 'utf8')
  let fd = null
  try {
    fs.mkdirSync(dir, { recursive: true })
    fd = fs.openSync(tmp, 'wx', 0o600)
    let off = 0
    while (off < buf.length) {
      const n = fs.writeSync(fd, buf, off, buf.length - off)
      if (n <= 0) throw new Error('short write')
      off += n
    }
    try { fs.fsyncSync(fd) } catch {}
    fs.closeSync(fd); fd = null
    fs.renameSync(tmp, targetPath)
  } catch (e) {
    if (fd !== null) { try { fs.closeSync(fd) } catch {} }
    try { fs.unlinkSync(tmp) } catch {}
    throw e
  }
}

function contentHashOf(text) {
  return crypto.createHash('sha256').update(Buffer.from(text, 'utf8')).digest('hex')
}
`
if (!src.includes(anchorUlid)) { console.error('ANCHOR ulid not found'); process.exit(1) }
src = src.replace(anchorUlid, helpers)

// ── 2. 替换 handleSave ─────────────────────────────────────
const oldSaveStart = "/** 保存：有 id 就更新，没有就新建。 */"
const oldSaveEnd = "  fs.writeFileSync(p, buildMd(meta, data.body || ''), 'utf8')\n  return { ok: true, id, created: true, updated: now }\n}\n"
const i = src.indexOf(oldSaveStart)
const j = src.indexOf(oldSaveEnd)
if (i < 0 || j < 0) { console.error('handleSave block not found', i, j); process.exit(1) }
const end = j + oldSaveEnd.length

const newSave = `/** 保存：有 id 就更新，没有就新建。${MARK} */
async function handleSave(req) {
  const raw = await readBody(req)
  let data
  try { data = JSON.parse(raw) } catch { return { ok: false, error: 'invalid json' } }

  const now = new Date().toISOString()

  // ── 更新分支：只认已存在的合法 ULID ──────────────────────
  if (data.id !== undefined && data.id !== null && data.id !== '') {
    try { assertUlid(data.id) } catch (e) { return { ok: false, error: e.message, code: 'INVALID_ARGUMENT' } }
    const p = findById(data.id)
    if (!p) return { ok: false, error: 'not found: ' + data.id, code: 'NOT_FOUND' }

    const txt = fs.readFileSync(p, 'utf8')
    // 可选并发校验：调用方带了 expectedRevision 就必须匹配
    if (typeof data.expectedRevision === 'string' && data.expectedRevision.length === 64) {
      const cur = contentHashOf(txt)
      if (cur !== data.expectedRevision) {
        return { ok: false, error: '内容已被其他写入修改', code: 'CONFLICT', actual: cur }
      }
    }
    const { meta, body } = parseMd(txt)
    if (typeof data.title === 'string') meta.title = data.title
    if (Array.isArray(data.tags)) meta.tags = data.tags
    if (typeof data.status === 'string') meta.status = data.status
    if (typeof data.folder === 'string') meta.folder = data.folder
    if (data.due !== undefined) meta.due = data.due || null
    if (typeof data.kind === 'string') meta.kind = data.kind
    meta.updated = now
    const newBody = typeof data.body === 'string' ? data.body : body
    const text = buildMd(meta, newBody)
    const target = assertInsideVault(p)
    writeFileAtomicSync(target, text)
    return { ok: true, id: data.id, created: false, updated: now, revision: contentHashOf(text) }
  }

  // ── 新建分支：id 一律服务端生成，不接受外部 id ────────────
  const id = ulid()
  assertUlid(id)
  const meta = {
    schema: 1,
    id,
    kind: data.kind || 'note',
    title: data.title || '未命名',
    aliases: [],
    tags: Array.isArray(data.tags) ? data.tags : [],
    sensitivity: data.sensitivity || 'personal',
    created: now,
    updated: now,
    device: 'phone',
    source: null,
    status: data.status || (data.kind === 'task' ? 'open' : 'active'),
    source_id: null,
    folder: data.folder || null,
    due: data.due || null,
  }
  const dir = path.join(VAULT, now.slice(0, 4), now.slice(0, 7))
  fs.mkdirSync(dir, { recursive: true })
  const text = buildMd(meta, data.body || '')
  const target = assertInsideVault(path.join(dir, id + '.md'))
  writeFileAtomicSync(target, text)
  return { ok: true, id, created: true, updated: now, revision: contentHashOf(text) }
}
`
src = src.slice(0, i) + newSave + src.slice(end)

// ── 3. handleDelete 也加 ULID 校验 ────────────────────────
const oldDel = `function handleDelete(url) {
  const id = url.searchParams.get('id') || ''
  const p = findById(id)
  if (!p) return { ok: false, error: 'not found' }`
const newDel = `function handleDelete(url) {
  const id = url.searchParams.get('id') || ''
  try { assertUlid(id) } catch (e) { return { ok: false, error: e.message, code: 'INVALID_ARGUMENT' } }
  const p = findById(id)
  if (!p) return { ok: false, error: 'not found', code: 'NOT_FOUND' }`
if (!src.includes(oldDel)) { console.error('handleDelete block not found'); process.exit(1) }
src = src.replace(oldDel, newDel)

fs.writeFileSync(TARGET, src, 'utf8')
console.log('PATCHED: ' + TARGET)
