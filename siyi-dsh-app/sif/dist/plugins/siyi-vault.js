// 思忆集 · 笔记数据接口（D1）
//
// 给客户端页面提供 vault 的读写 HTTP 接口。挂在 webServer 的
// /siyi-vault 前缀下，与引擎同端口（3180），前端同源调用不用另开端口。
//
// 为什么走 HTTP 而不是 cordis Remote：
//   Remote 那套是 TypeScript decorator 编译产物（__esDecorate），
//   在 /sdcard 的用户插件里照抄不现实。而 webServer.register 是
//   公开的普通对象方法，实测在用户插件里 ctx.get('webServer') 就能拿到。
//
// 数据格式沿用 vault 现有约定：
//   每篇一个 .md，frontmatter 存元数据，正文用 [[双链]] 与 #标签。
//   id 就是文件名（ULID），是主键，不能改。
//
// 安全边界：只允许访问 vault 目录内、且扩展名为 .md 的文件，
// 路径必须经过 resolve 后的前缀校验，防止 ../ 逃逸。

import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
// SIYI-INDEX-20261118：复用 sif 的增量索引，避免每次请求全量读盘
import { createIndexer } from '/storage/emulated/0/思忆集/app/sif/core/vault/indexer.mjs'

export const name = 'siyi-vault'
export const inject = ['tools']

const VAULT = '/sdcard/思忆集/vault'
const PREFIX = '/siyi-vault'
const ASSETS = path.join(VAULT, 'assets')

// ── 图片上传协议（客户端 client.js 与 selftest 脚本共用）─────────────
// 1) 原始字节（首选，客户端用）：
//    POST /siyi-vault/upload
//    Content-Type: image/png（或 application/octet-stream）
//    X-Siyi-Filename: encodeURIComponent(原始文件名)  ← 只用于取扩展名
//    body: 图片原始字节，<= 8MB
// 2) JSON 备选（curl / 自测方便）：
//    POST /siyi-vault/upload  Content-Type: application/json
//    {"name":"a.png","ext":"png","data":"<base64 或 data URL>"}
// 成功：{"ok":true,"url":"/siyi-vault/asset/<sha256前16位>.<ext>","name":...,"size":...,"dedup":bool}
// 失败：{"ok":false,"error":"..."}；400 参数/内容问题，413 超大，415 类型不支持
// 落盘名只用内容 sha256 前 16 位 + 嗅探出的扩展名，绝不使用用户文件名（防路径穿越）。
const MAX_UPLOAD = 8 * 1024 * 1024
const MIME_BY_EXT = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif' }

// ── 工具函数 ────────────────────────────────────────────────────────────

function log(msg) {
  try { process.stderr.write('[siyi-vault] ' + msg + '\n') } catch {}
}

/** 生成 ULID 风格 id：时间戳(48bit) + 随机(80bit)，与现有 note 一致。 */
function ulid() {
  const t = Date.now()
  const CH = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
  let ts = ''
  let x = t
  for (let i = 0; i < 10; i++) { ts = CH[x % 32] + ts; x = Math.floor(x / 32) }
  let rnd = ''
  const buf = new Uint8Array(16)
  globalThis.crypto.getRandomValues(buf)
  for (let i = 0; i < 16; i++) rnd += CH[buf[i] % 32]
  return ts + rnd
}

// ── 路径安全（SIYI-PATHFIX-20261118）─────────────────────────────────────────
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

/**
 * 断言 target 在 VAULT 内，返回规范化后的绝对路径。
 *
 * 为什么不能简单比较 realpath 前缀：Android 上 /data/user/0 是指向
 * /data/data 的符号链接。调用方传进来的路径是 /data/user/0/...（未规范化），
 * 而 realpath(VAULT) 返回 /data/data/...，直接比较必然误判为越界（实测踩到）。
 *
 * 正确做法：取「最近的已存在祖先」做 realpath，再把尚未存在的尾段拼回去。
 * 这样既处理了根路径符号链接，也能在文件尚未创建时判断归属。
 */
function assertInsideVault(target) {
  const realRoot = fs.realpathSync(VAULT)
  const abs = path.resolve(target)

  let cur = abs
  const tail = []
  while (!fs.existsSync(cur)) {
    const parent = path.dirname(cur)
    if (parent === cur) break
    tail.unshift(path.basename(cur))
    cur = parent
  }
  const realAncestor = fs.realpathSync(cur)
  const canonical = tail.length ? path.join(realAncestor, ...tail) : realAncestor

  if (canonical !== realRoot && !canonical.startsWith(realRoot + path.sep)) {
    const e = new Error('路径越界')
    e.code = 'INVALID_ARGUMENT'
    throw e
  }
  return canonical
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

/**
 * SIYI-TITLEFIX-20261118：语义上必须是字符串的 frontmatter 字段。
 * title: 1 在 YAML 里是数字，但笔记标题永远是文本。
 */
const STRING_META_FIELDS = new Set([
  'id', 'kind', 'title', 'sensitivity', 'device', 'source', 'status',
  'source_id', 'folder', 'due', 'created', 'updated',
])

/** 解析 frontmatter。返回 { meta, body }；没有 frontmatter 时 meta 为空对象。 */
function parseMd(text) {
  if (!text.startsWith('---')) return { meta: {}, body: text }
  const end = text.indexOf('\n---', 3)
  if (end < 0) return { meta: {}, body: text }
  const raw = text.slice(4, end)
  const body = text.slice(text.indexOf('\n', end + 1) + 1)
  const meta = {}
  for (const line of raw.split('\n')) {
    const m = line.match(/^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*)$/)
    if (!m) continue
    const k = m[1]
    let v = m[2].trim()
    if (v === '' || v === 'null' || v === '~') { meta[k] = null; continue }
    if (v.startsWith('[') && v.endsWith(']')) {
      const inner = v.slice(1, -1).trim()
      meta[k] = inner === '' ? [] : inner.split(',').map(s => s.trim().replace(/^["']|["']$/g, '')).filter(Boolean)
      continue
    }
    if (v === 'true') { meta[k] = true; continue }
    if (v === 'false') { meta[k] = false; continue }
    // SIYI-TITLEFIX-20261118：字符串语义字段不转数字。
    // 真实 vault 里有 title: 1 的笔记，转成数字会让下游 .toLowerCase() 崩溃。
    if (!STRING_META_FIELDS.has(k) && /^-?\d+(\.\d+)?$/.test(v)) { meta[k] = Number(v); continue }
    meta[k] = v.replace(/^["']|["']$/g, '')
  }
  return { meta, body }
}

/** 序列化回 frontmatter + 正文。字段顺序固定，避免每次写都产生 diff。 */
function buildMd(meta, body) {
  const order = ['schema', 'id', 'kind', 'title', 'aliases', 'tags', 'sensitivity',
    'created', 'updated', 'device', 'source', 'status', 'source_id', 'folder', 'due']
  const lines = ['---']
  const done = new Set()
  const push = (k) => {
    const v = meta[k]
    if (v === undefined) return
    done.add(k)
    if (Array.isArray(v)) lines.push(k + ': [' + v.join(', ') + ']')
    else if (v === null) lines.push(k + ': null')
    else lines.push(k + ': ' + String(v))
  }
  for (const k of order) push(k)
  for (const k of Object.keys(meta)) if (!done.has(k)) push(k)
  lines.push('---', '')
  return lines.join('\n') + (body || '')
}

/** 从正文抽双链 [[xxx]] 与标签 #xxx。 */
function extractLinks(body) {
  const links = new Set()
  const tags = new Set()
  for (const m of body.matchAll(/\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]/g)) links.add(m[1].trim())
  for (const m of body.matchAll(/(?:^|\s)#([\u4e00-\u9fa5A-Za-z0-9_\-]+)/g)) tags.add(m[1])
  return { links: [...links], tags: [...tags] }
}

/** 枚举所有笔记文件，返回 [{id, path, meta}]。 */
function allNotes() {
  const out = []
  const yearRoot = path.join(VAULT, '2026')
  let years = []
  try { years = fs.readdirSync(VAULT).filter(n => /^\d{4}$/.test(n)) } catch { return out }
  for (const y of years) {
    let months = []
    try { months = fs.readdirSync(path.join(VAULT, y)) } catch { continue }
    for (const mo of months) {
      const dir = path.join(VAULT, y, mo)
      let files = []
      try { files = fs.readdirSync(dir) } catch { continue }
      for (const f of files) {
        if (!f.endsWith('.md')) continue
        try {
          const p = path.join(dir, f)
          const txt = fs.readFileSync(p, 'utf8')
          const { meta } = parseMd(txt)
          out.push({ id: f.replace(/\.md$/, ''), path: p, meta })
        } catch {}
      }
    }
  }
  return out
}

// SIYI-INDEX-20261118：索引实例。allNotes 仍保留（写入路径与兼容用），
// 但列表/统计/搜索改走索引。
let _indexer = null
function getIndexer() {
  if (_indexer) return _indexer
  try {
    _indexer = createIndexer({
      root: VAULT,
      parseNote: (text) => {
        const { meta, body } = parseMd(text)
        // 与 parseMd 保持一致的字符串归一
        for (const k of STRING_META_FIELDS) {
          if (meta[k] !== undefined && meta[k] !== null && typeof meta[k] !== 'string') {
            meta[k] = String(meta[k])
          }
        }
        return { meta, body }
      },
      extractLinks,
    })
  } catch (e) {
    log('索引初始化失败，回退全量读盘: ' + (e && e.message))
    _indexer = null
  }
  return _indexer
}

/** 走索引拿全部笔记的轻量元数据；索引不可用时回退 allNotes。 */
function allNoteSummaries() {
  const idx = getIndexer()
  if (!idx) {
    return allNotes().map(n => ({
      id: n.id, meta: n.meta, path: n.path, revision: null,
      links: [], tags: [],
    }))
  }
  return idx.all().map(e => ({
    id: e.id, meta: e.meta, path: path.join(VAULT, e.logicalPath),
    revision: e.revision, links: e.links, tags: e.tags,
  }))
}

/** 写入后让索引失效，避免读到旧数据。 */
function invalidateIndex(filePath) {
  const idx = getIndexer()
  if (!idx) return
  try {
    if (filePath) idx.invalidate(filePath)
    idx.markDirty()
  } catch {}
}

/** 按 id 找文件路径，找不到返回 null。 */
function findById(id) {
  if (!/^[0-9A-Z]{10,40}$/.test(id)) return null
  const hit = allNotes().find(n => n.id === id)
  return hit ? hit.path : null
}

/** 安全解析：只允许 vault 内 .md 文件。 */
function safePath(rel) {
  const p = path.resolve(VAULT, rel)
  if (!p.startsWith(VAULT + path.sep) && p !== VAULT) return null
  if (!p.endsWith('.md')) return null
  return p
}

// ── HTTP 安全闸门（SIYI-HTTPHARDEN-20261118）──────────────────────────────────────
//
// 为什么必须有：改造前实测 curl 无认证就能读写全部笔记，
// 任意网页也能通过 CSRF 写入。这是本机回环服务最容易忽略的攻击面。
//
// 四道闸门，都不依赖引擎改动：
//   1. Host 必须是回环地址（挡 DNS rebinding）
//   2. Origin 存在时必须同源（挡浏览器跨站）
//   3. 写操作必须是 application/json（挡表单 CSRF）
//   4. 写操作限流（挡批量破坏）
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]', '::1'])
const WRITE_ACTIONS = new Set(['save', 'delete', 'upload', 'folder-create', 'chat'])
const RATE_LIMIT_PER_MIN = 60

const writeHits = new Map()

/** 提取 host（不含端口）并判断是否回环。 */
function isLoopbackHost(hostHeader) {
  if (!hostHeader) return false
  const h = String(hostHeader).trim().toLowerCase()
  // 去掉端口；IPv6 形如 [::1]:3180
  let host = h
  if (h.startsWith('[')) {
    const end = h.indexOf(']')
    if (end > 0) host = h.slice(0, end + 1)
  } else {
    const colon = h.lastIndexOf(':')
    if (colon > 0 && h.indexOf(':') === colon) host = h.slice(0, colon)
  }
  return LOOPBACK_HOSTS.has(host)
}

/** Origin 与 Host 同源校验。Origin 缺失时按非浏览器客户端处理（仍需过其他闸门）。 */
function originAllowed(req) {
  const origin = req.headers['origin']
  if (!origin) return true   // curl / node 客户端不带 Origin
  try {
    const o = new URL(String(origin))
    const hostHeader = String(req.headers['host'] || '')
    const oHost = o.host.toLowerCase()
    const hHost = hostHeader.toLowerCase()
    if (oHost === hHost) return true
    // 端口可能省略：比较 hostname
    const oName = o.hostname.toLowerCase()
    let hName = hHost
    if (hName.startsWith('[')) { const e = hName.indexOf(']'); if (e > 0) hName = hName.slice(0, e + 1) }
    else { const c = hName.lastIndexOf(':'); if (c > 0) hName = hName.slice(0, c) }
    return oName === hName
  } catch {
    return false
  }
}

/** 写操作限流：同一 host+action 每分钟上限。 */
function rateLimited(req, action) {
  const key = String(req.headers['host'] || '') + '|' + action
  const now = Date.now()
  const win = writeHits.get(key) || []
  const fresh = win.filter(t => now - t < 60000)
  if (fresh.length >= RATE_LIMIT_PER_MIN) { writeHits.set(key, fresh); return true }
  fresh.push(now)
  writeHits.set(key, fresh)
  return false
}

/**
 * 执行全部闸门。通过返回 null，拒绝返回 { code, body }。
 */
function checkRequest(req, action) {
  // 闸门 1：Host 必须是回环
  if (!isLoopbackHost(req.headers['host'])) {
    return { code: 403, body: { ok: false, error: 'Host 不是回环地址，拒绝访问', code: 'FORBIDDEN_HOST' } }
  }
  // 闸门 2：Origin 同源
  if (!originAllowed(req)) {
    return { code: 403, body: { ok: false, error: '跨站请求被拒绝', code: 'FORBIDDEN_ORIGIN' } }
  }
  // 闸门 3+4 只针对写操作
  if (WRITE_ACTIONS.has(action)) {
    const ct = String(req.headers['content-type'] || '').toLowerCase()
    // chat 允许 application/json；upload 允许图片 MIME
    if (action === 'upload') {
      const okCt = ct.includes('application/json') || ct.startsWith('image/') || ct.startsWith('application/octet-stream')
      if (!okCt) return { code: 415, body: { ok: false, error: 'upload 需要图片或 JSON 类型', code: 'UNSUPPORTED_MEDIA_TYPE' } }
    } else if (!ct.includes('application/json')) {
      // 表单提交（text/plain / x-www-form-urlencoded / multipart）是 CSRF 的典型载体
      return { code: 415, body: { ok: false, error: '写操作必须是 application/json', code: 'UNSUPPORTED_MEDIA_TYPE' } }
    }
    if (rateLimited(req, action)) {
      return { code: 429, body: { ok: false, error: '写入过于频繁，请稍后再试', code: 'RATE_LIMITED' } }
    }
  }
  return null
}

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
    // SIYI-HTTPHARDEN-20261118：敏感响应禁止被缓存、禁止被 iframe 嵌入、禁止 MIME 嗅探
    'X-Content-Type-Options': 'nosniff',
    'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer',
  })
  res.end(body)
}

/** 读原始字节；超过 maxBytes 抛 code=TOO_LARGE（并排空剩余 body，保证还能回响应）。 */
function readBuffer(req, maxBytes) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    let done = false
    req.on('data', c => {
      if (done) return
      size += c.length
      if (size > maxBytes) {
        done = true
        const err = new Error('body too large')
        err.code = 'TOO_LARGE'
        reject(err)
        try { req.resume() } catch {}
        return
      }
      chunks.push(c)
    })
    req.on('end', () => { if (!done) resolve(Buffer.concat(chunks)) })
    req.on('error', e => { if (!done) { done = true; reject(e) } })
  })
}

async function readBody(req) {
  return (await readBuffer(req, 4 * 1024 * 1024)).toString('utf8')
}

/** 用文件头判断真实图片类型，防止改扩展名把别的文件混进 asset。 */
function sniffImage(buf) {
  if (!buf || buf.length < 12) return null
  if (buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return 'jpg'
  if (buf[0] === 0x89 && buf[1] === 0x50 && buf[2] === 0x4e && buf[3] === 0x47) return 'png'
  const head = buf.toString('latin1', 0, 6)
  if (head === 'GIF87a' || head === 'GIF89a') return 'gif'
  if (buf.toString('latin1', 0, 4) === 'RIFF' && buf.toString('latin1', 8, 12) === 'WEBP') return 'webp'
  return null
}

/** 取白名单扩展名（jpeg 归一成 jpg）；不在白名单返回 null。 */
function normExt(name) {
  const m = String(name || '').match(/\.([A-Za-z0-9]+)$/)
  if (!m) return null
  const e = m[1].toLowerCase()
  if (e === 'jpeg') return 'jpg'
  return MIME_BY_EXT[e] ? e : null
}

/** 白名单扩展名 -> MIME。 */
function mimeByExt(ext) {
  return MIME_BY_EXT[String(ext || '').toLowerCase()] || null
}

// ── 业务处理 ────────────────────────────────────────────────────────────

/** 列表：支持 kind / 标签 / 关键词过滤，按 updated 倒序。 */
/**
 * 列出所有文件夹（名字 + 笔记数）以及未归类计数。
 *
 * 文件夹不是一个独立的目录实体，而是笔记 frontmatter 里的 folder 字段。
 * 已有数据里 folder 取值形如「资讯」「知识」，也有 null（未归类）。
 * 计数从实际笔记里统计，所以不会出现「有文件夹但里面是空的」以外的偏差。
 */
function handleFolders() {
  const counts = new Map()
  let unfiled = 0
  for (const n of allNoteSummaries()) {
    const f = n.meta.folder
    if (typeof f === 'string' && f.trim()) {
      const key = f.trim()
      counts.set(key, (counts.get(key) || 0) + 1)
    } else unfiled++
  }
  const folders = [...counts.entries()]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name))
  return { ok: true, folders, unfiled, total: folders.reduce((s, f) => s + f.count, 0) + unfiled }
}

/**
 * 创建一个文件夹。
 *
 * 文件夹本身用一个 kind=moc（索引）的笔记承载：这样不用引入新的文件类型，
 * 现有 frontmatter 解析、列表统计、图谱全都自动支持它。
 * 用户在文件夹里写点说明也顺带能存下来。
 */
async function handleFolderCreate(req) {
  let data
  try {
    const raw = await readBody(req)
    data = JSON.parse(raw)
  } catch (e) {
    return { ok: false, error: 'invalid json: ' + (e && e.message) }
  }
  const name = String((data && data.name) || '').trim()
  if (!name) return { ok: false, error: '文件夹名不能为空' }
  if (name.length > 40) return { ok: false, error: '文件夹名过长（最多 40 字）' }
  if (/[\/#\r\n]/.test(name)) return { ok: false, error: '文件夹名不能包含 / # 或换行' }
  // 已存在就复用，不重复创建
  const exists = allNotes().some(n => String(n.meta.folder || '').trim() === name)
  if (exists) return { ok: true, name, created: false, message: '文件夹已存在' }
  const id = ulid()
  const now = new Date().toISOString()
  const meta = {
    schema: 1,
    id,
    kind: 'moc',
    title: name,
    aliases: [],
    tags: [],
    sensitivity: 'personal',
    created: now,
    updated: now,
    device: 'phone',
    source: null,
    status: 'active',
    folder: name,
  }
  const dir = path.join(VAULT, now.slice(0, 4), now.slice(0, 7))
  fs.mkdirSync(dir, { recursive: true })
  fs.writeFileSync(path.join(dir, id + '.md'), buildMd(meta, ''), 'utf8')
  return { ok: true, name, id, created: true }
}

function handleList(url) {
  const q = url.searchParams.get('q') || ''
  const kind = url.searchParams.get('kind') || ''
  const tag = url.searchParams.get('tag') || ''
  const folder = url.searchParams.get('folder') || ''
  const status = url.searchParams.get('status') || ''
  const limit = Math.min(Number(url.searchParams.get('limit')) || 500, 2000)

  // SIYI-INDEX-20261118：走索引，不再每次全量读盘
  let items = allNoteSummaries().map(n => ({
    id: n.id,
    title: String(n.meta.title ?? '(无标题)'),
    kind: String(n.meta.kind || 'note'),
    tags: Array.isArray(n.meta.tags) ? n.meta.tags.map(t => String(t)) : [],
    status: n.meta.status || null,
    due: n.meta.due || null,
    created: n.meta.created || null,
    updated: n.meta.updated || null,
    folder: n.meta.folder || null,
  }))
  if (kind) items = items.filter(i => i.kind === kind)
  if (status) items = items.filter(i => i.status === status)
  if (tag) items = items.filter(i => i.tags.includes(tag))
  // folder 过滤：客户端的文件夹栏用它筛。
  // folder=__none__ 表示「没有文件夹的笔记」（未归类），
  // 不能用空串表达，因为空串就是「不过滤」。
  if (folder) items = items.filter(i => (i.folder || '') === (folder === '__none__' ? '' : folder))
  if (q) {
    const k = q.toLowerCase()
    items = items.filter(i =>
      String(i.title || '').toLowerCase().includes(k) ||
      i.tags.some(t => String(t).toLowerCase().includes(k)))
  }
  items.sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')))
  return { ok: true, total: items.length, items: items.slice(0, limit) }
}

/** 详情：返回 meta + 正文 + 抽出的双链标签。 */
function handleGet(url) {
  const id = url.searchParams.get('id') || ''
  const p = findById(id)
  if (!p) return { ok: false, error: 'not found' }
  const txt = fs.readFileSync(p, 'utf8')
  const { meta, body } = parseMd(txt)
  const { links, tags } = extractLinks(body)
  return { ok: true, id, meta, body, links, tags, path: p.replace(VAULT + '/', '') }
}

/** 保存：有 id 就更新，没有就新建。SIYI-PATHFIX-20261118 */
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
    invalidateIndex(target)
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
  invalidateIndex(target)
  return { ok: true, id, created: true, updated: now, revision: contentHashOf(text) }
}

/** 删除：移到 .siyi/trash，不真删。 */
function handleDelete(url) {
  const id = url.searchParams.get('id') || ''
  try { assertUlid(id) } catch (e) { return { ok: false, error: e.message, code: 'INVALID_ARGUMENT' } }
  const p = findById(id)
  if (!p) return { ok: false, error: 'not found', code: 'NOT_FOUND' }
  const trash = path.join(VAULT, '.siyi', 'trash')
  fs.mkdirSync(trash, { recursive: true })
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const dst = path.join(trash, stamp + '__' + path.basename(p))
  fs.renameSync(p, dst)
  invalidateIndex(p)
  return { ok: true, id, trashed: path.basename(dst) }
}

/** 知识图谱：节点=笔记，边=双链。 */
function handleGraph() {
  const notes = allNotes()
  const byTitle = new Map()
  for (const n of notes) {
    if (n.meta.title) byTitle.set(String(n.meta.title).trim(), n.id)
  }
  const nodes = []
  const edges = []
  const tagSet = new Set()
  for (const n of notes) {
    const txt = (() => { try { return fs.readFileSync(n.path, 'utf8') } catch { return '' } })()
    const { body } = parseMd(txt)
    const { links, tags } = extractLinks(body)
    nodes.push({
      id: n.id,
      title: n.meta.title || '(无标题)',
      kind: n.meta.kind || 'note',
      tags: Array.isArray(n.meta.tags) ? n.meta.tags : [],
      degree: links.length,
    })
    for (const l of links) {
      const target = byTitle.get(l)
      if (target) edges.push({ source: n.id, target, label: l })
      else edges.push({ source: n.id, target: null, label: l, missing: true })
    }
    for (const t of tags) tagSet.add(t)
    if (Array.isArray(n.meta.tags)) for (const t of n.meta.tags) tagSet.add(t)
  }
  return { ok: true, nodes, edges, tags: [...tagSet], total: nodes.length }
}

/** 待办：kind=task 的清单，按 due 排。 */
function handleTasks(url) {
  const includeDone = url.searchParams.get('done') === '1'
  const items = allNoteSummaries()
    .filter(n => n.meta.kind === 'task')
    .map(n => ({
      id: n.id,
      title: n.meta.title || '(无标题)',
      status: n.meta.status || 'open',
      due: n.meta.due || null,
      tags: Array.isArray(n.meta.tags) ? n.meta.tags : [],
      updated: n.meta.updated || null,
    }))
    .filter(i => includeDone || i.status !== 'done' && i.status !== 'archived')
  items.sort((a, b) => {
    if (!a.due && !b.due) return String(b.updated || '').localeCompare(String(a.updated || ''))
    if (!a.due) return 1
    if (!b.due) return -1
    return String(a.due).localeCompare(String(b.due))
  })
  return { ok: true, total: items.length, items }
}

/** 统计：给首页卡片用。 */
function handleStats() {
  // SIYI-INDEX-20261118：走索引
  const notes = allNoteSummaries()
  const byKind = {}
  const byStatus = {}
  for (const n of notes) {
    const k = n.meta.kind || 'note'
    byKind[k] = (byKind[k] || 0) + 1
    const s = n.meta.status || 'null'
    byStatus[s] = (byStatus[s] || 0) + 1
  }
  return { ok: true, total: notes.length, byKind, byStatus, vault: VAULT }
}

/** 上传图片：校验 -> sha256 去重 -> 写 vault/assets。返回 {code, body} 由分发层写响应。 */
async function handleUpload(req) {
  if (req.method !== 'POST') return { code: 405, body: { ok: false, error: 'POST only' } }
  const ct = String(req.headers['content-type'] || '').toLowerCase()
  // WebView 调系统选图器时文件名可能为空/无扩展名，用 MIME 兜底
  const ctExt = /image\/jpe?g/.test(ct) ? 'jpg' : /image\/png/.test(ct) ? 'png' :
    /image\/webp/.test(ct) ? 'webp' : /image\/gif/.test(ct) ? 'gif' : ''
  let buf = null
  let claimedName = ''
  let claimedExt = ''
  try {
    if (ct.includes('application/json')) {
      const raw = await readBuffer(req, 12 * 1024 * 1024)
      let data
      try { data = JSON.parse(raw.toString('utf8')) } catch {
        return { code: 400, body: { ok: false, error: 'invalid json' } }
      }
      if (!data || typeof data.data !== 'string' || !data.data) {
        return { code: 400, body: { ok: false, error: '缺少 data(base64)' } }
      }
      let b64 = data.data
      if (b64.startsWith('data:')) b64 = b64.slice(b64.indexOf(',') + 1)
      buf = Buffer.from(b64, 'base64')
      claimedName = String(data.name || '')
      claimedExt = String(data.ext || '')
    } else {
      const fn = String(req.headers['x-siyi-filename'] || '')
      try { claimedName = decodeURIComponent(fn) } catch { claimedName = fn }
      buf = await readBuffer(req, MAX_UPLOAD)
    }
  } catch (e) {
    if (e && e.code === 'TOO_LARGE') return { code: 413, body: { ok: false, error: '图片超过 8MB 上限' } }
    return { code: 400, body: { ok: false, error: '读取失败: ' + (e && e.message) } }
  }
  if (!buf || buf.length === 0) return { code: 400, body: { ok: false, error: '空文件' } }
  if (buf.length > MAX_UPLOAD) return { code: 413, body: { ok: false, error: '图片超过 8MB 上限' } }

  // 扩展名白名单：调用方声明的扩展名 + 文件头嗅探必须一致
  const declared = claimedExt ? normExt('.' + claimedExt)
    : (normExt(claimedName) || (ctExt ? normExt('.' + ctExt) : null))
  if (!declared) return { code: 415, body: { ok: false, error: '只支持 jpg/jpeg/png/webp/gif' } }
  const sniffed = sniffImage(buf)
  if (!sniffed) return { code: 415, body: { ok: false, error: '文件内容不是有效图片' } }
  if (sniffed !== declared) {
    return { code: 415, body: { ok: false, error: '扩展名与内容不符：' + declared + ' / ' + sniffed } }
  }

  const hash = crypto.createHash('sha256').update(buf).digest('hex').slice(0, 16)
  const fname = hash + '.' + sniffed
  fs.mkdirSync(ASSETS, { recursive: true })
  const fp = path.join(ASSETS, fname)
  let dedup = false
  if (fs.existsSync(fp)) {
    dedup = true
  } else {
    // 同一目录内临时名再 rename，避免写一半被读到
    const tmp = fp + '.tmp-' + process.pid
    fs.writeFileSync(tmp, buf)
    fs.renameSync(tmp, fp)
  }
  return { code: 200, body: { ok: true, url: PREFIX + '/asset/' + fname, name: fname, size: buf.length, dedup } }
}

/** 返回 asset 图片字节。name 只允许 sha256 前16位 + 白名单扩展名，双保险防 ../。 */
function handleAsset(req, res, name) {
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    return sendJson(res, 405, { ok: false, error: 'GET only' })
  }
  let n = String(name || '')
  try { n = decodeURIComponent(n) } catch {
    return sendJson(res, 400, { ok: false, error: 'bad name' })
  }
  if (!/^[0-9a-f]{16}\.(?:jpe?g|png|webp|gif)$/.test(n)) {
    return sendJson(res, 400, { ok: false, error: 'bad asset name' })
  }
  const fp = path.resolve(ASSETS, n)
  if (!fp.startsWith(ASSETS + path.sep)) {
    return sendJson(res, 400, { ok: false, error: 'bad path' })
  }
  const mime = mimeByExt(n.slice(n.lastIndexOf('.') + 1))
  if (!mime) return sendJson(res, 415, { ok: false, error: 'bad ext' })
  let buf
  try { buf = fs.readFileSync(fp) } catch {
    return sendJson(res, 404, { ok: false, error: 'asset not found' })
  }
  res.writeHead(200, {
    'Content-Type': mime,
    'Content-Length': buf.length,
    'Cache-Control': 'public, max-age=31536000, immutable',
    'X-Content-Type-Options': 'nosniff',
  })
  res.end(req.method === 'HEAD' ? undefined : buf)
}

// ── AI 助手代理（笔记编辑器内嵌聊天）────────────────────────────────────
//
// 为什么在插件侧代理，而不是 client.js 直接打主 RPC：
//   1) 主会话的 prompt 只返回 {accepted:true}，真正的 AI 回复走 session/follow
//      的 Remote mux 流（WebSocket + 登录态签名 cookie）。笔记面板是裸 fetch 页面，
//      接不到那条流，也不该碰主对话的流（会串会话/打断用户当前对话）。
//   2) 插件在引擎内，ctx.get('sessionController') 就是已配好 provider/model 的主
//      agent 控制器，ctx.on('session/event') 能收到所有 assistant/message 事件。
//      这里复用它的「已配置模型和登录态」，绝不硬编码模型名或 API key。
//   3) 因此 client.js 只需要 POST /siyi-vault/chat 然后显示一次性返回的 reply。
//
// 实现：用一个专用的命名 session（siyi-notes-ai-chat）承载笔记问答，避免占用/污染
// 用户的主对话 session。prompt 受理后监听该 session 的 assistant/message，攒够本轮
// 新增的文本就返回；turn/end 或超时也算收尾，保证不会让 HTTP 请求挂死。

// 专用会话的稳定 id（ULID 形状仅用于满足 dsh-session 的会话 id 校验）。
// 固定 id + create 的 reusable 语义会复用同一个空白 session，不会每问一句新建一条。
const AI_CHAT_SESSION = '01JAI0N7E5SIYINOTES00000000000'
const AI_CHAT_TIMEOUT_MS = 90_000

function assistantText(sessionId, events) {
  // 只收集本轮 record 里 assistant/message 的 text 块，按时间顺序拼接。
  let out = ''
  for (const ev of events) {
    const data = ev && ev.data
    const msg = data && data.message
    if (!msg || msg.role !== 'assistant' || !Array.isArray(msg.content)) continue
    const parts = msg.content
      .filter(b => b && b.type === 'text' && typeof b.text === 'string')
      .map(b => b.text)
    if (parts.length) out += (out && !out.endsWith('\n') ? '\n' : '') + parts.join('')
  }
  return out
}

async function handleChat(ctx, req) {
  if (req.method !== 'POST') return { code: 405, body: { ok: false, error: 'POST only' } }
  let input
  try {
    const raw = await readBuffer(req, 256 * 1024)
    input = JSON.parse(raw.toString('utf8'))
  } catch (e) {
    return { code: 400, body: { ok: false, error: 'invalid json: ' + (e && e.message) } }
  }
  const question = String((input && input.question) || '').trim()
  if (!question) return { code: 400, body: { ok: false, error: '问题不能为空' } }
  const noteText = input && typeof input.noteText === 'string' ? input.noteText : ''

  const controller = ctx.get('sessionController', false)
  if (!controller || typeof controller.prompt !== 'function') {
    return { code: 503, body: { ok: false, error: '主 agent 控制器不可用' } }
  }

  // 组 prompt：正文作为上下文块（用户勾了「引用当前笔记」才有），再跟用户问题。
  // 复用主 agent 的 system prompt / preset，不在这里注入任何角色扮演硬编码。
  const content = []
  if (noteText) {
    content.push({ type: 'text', text: '以下是我当前笔记的内容，请作为上下文参考：\n<笔记>\n' + noteText + '\n</笔记>' })
  }
  content.push({ type: 'text', text: question })

  const requestId = (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function')
    ? globalThis.crypto.randomUUID()
    : (String(Date.now()) + '-' + Math.random().toString(36).slice(2))

  // 收集本轮 assistant 事件：按 session id 过滤，只认本会话的 assistant/message。
  // 每次请求都是新的一次问答，事件监听在 prompt 之前挂上，收完本轮即摘除，
  // 不会混入下一轮残留。
  const collected = []

  let settled = false
  let finish
  const done = new Promise((resolve) => { finish = resolve })
  let timer = null
  // 用「收到第一条 assistant 文本后再静默一段时间」作为一轮回复的收尾信号，
  // 辅以 turn/end；两者都先到先算，并且有硬超时兜底。
  let quietTimer = null
  const armQuiet = () => {
    if (quietTimer) clearTimeout(quietTimer)
    quietTimer = setTimeout(() => { if (!settled) { settled = true; finish() } }, 1500)
  }

  const off = ctx.on('session/event', (session, event) => {
    if (settled || !session || session.id !== AI_CHAT_SESSION || !event) return
    const type = event.type
    if (type === 'assistant/message') {
      const msg = event.data && event.data.message
      if (msg && msg.role === 'assistant') {
        collected.push(event)
        armQuiet()
      }
    } else if (type === 'turn/end') {
      if (collected.length > 0) { settled = true; finish() }
    }
  })

  try {
    // 确保有会话：create 同名会复用空白 session（幂等）。
    try {
      const created = await controller.create({ sessionId: AI_CHAT_SESSION })
      log('AI 会话就绪 ' + ((created && created.sessionId) || AI_CHAT_SESSION))
    } catch (e) {
      log('AI 会话创建/复用失败（继续尝试直接 prompt）: ' + (e && e.message))
    }

    // 必须传 signal：SessionController.prompt 内部第一行就是 signal.throwIfAborted()。
    const abort = new AbortController()
    const admitted = await controller.prompt({
      requestId,
      sessionId: AI_CHAT_SESSION,
      mode: 'queue',
      content,
      clientTimeZone: 'Asia/Shanghai',
    }, abort.signal)
    // 宿主 controller.prompt 直接返回 {accepted:true}，失败会抛 RemoteError（进下面的 catch）。
    if (!admitted || admitted.accepted !== true) {
      return { code: 502, body: { ok: false, error: '主 agent 未受理本次提问' } }
    }

    timer = setTimeout(() => { if (!settled) { settled = true; finish() } }, AI_CHAT_TIMEOUT_MS)
    await done
    const reply = assistantText(AI_CHAT_SESSION, collected).trim()
    if (!reply) {
      return { code: 504, body: { ok: false, error: 'AI 未在限定时间内回复，请稍后重试' } }
    }
    return { code: 200, body: { ok: true, reply, sessionId: AI_CHAT_SESSION } }
  } catch (e) {
    return { code: 502, body: { ok: false, error: 'AI 助手出错: ' + (e && e.message ? e.message : String(e)) } }
  } finally {
    if (timer) clearTimeout(timer)
    if (quietTimer) clearTimeout(quietTimer)
    try { off() } catch {}
  }
}

// ── 路由分发 ────────────────────────────────────────────────────────────

export function apply(ctx, config) {
  const ws = ctx.get('webServer', false)
  if (!ws || typeof ws.register !== 'function') {
    log('未找到 webServer 服务，笔记接口未挂载')
    return
  }

  const disposer = ws.register({
    kind: 'prefix',
    path: PREFIX,
    handler: async (req, res) => {
      let url
      try {
        url = new URL(req.url, 'http://127.0.0.1')
      } catch {
        return sendJson(res, 400, { ok: false, error: 'bad url' })
      }
      // /siyi-vault/api/xxx -> xxx
      const rest = url.pathname.slice(PREFIX.length).replace(/^\//, '')
      const action = rest.replace(/^api\//, '') || 'stats'

      // SIYI-HTTPHARDEN-20261118：所有请求先过安全闸门
      const denied = checkRequest(req, action)
      if (denied) {
        log('安全闸门拒绝 ' + action + ': ' + denied.body.code)
        return sendJson(res, denied.code, denied.body)
      }

      try {
        // 图片字节流：/siyi-vault/asset/<name>
        if (action.startsWith('asset/')) return handleAsset(req, res, action.slice(6))
        if (action === 'upload') {
          const r = await handleUpload(req)
          return sendJson(res, r.code, r.body)
        }
        if (action === 'list') return sendJson(res, 200, handleList(url))
        if (action === 'get') return sendJson(res, 200, handleGet(url))
        if (action === 'chat') {
          const r = await handleChat(ctx, req)
          return sendJson(res, r.code, r.body)
        }
        if (action === 'graph') return sendJson(res, 200, handleGraph())
        if (action === 'tasks') return sendJson(res, 200, handleTasks(url))
        if (action === 'stats') return sendJson(res, 200, handleStats())
        if (action === 'folders') return sendJson(res, 200, handleFolders())
        if (action === 'folder-create') {
          if (req.method !== 'POST') return sendJson(res, 405, { ok: false, error: 'POST only' })
          return sendJson(res, 200, await handleFolderCreate(req))
        }
        if (action === 'save') {
          if (req.method !== 'POST') return sendJson(res, 405, { ok: false, error: 'POST only' })
          return sendJson(res, 200, await handleSave(req))
        }
        if (action === 'delete') {
          if (req.method !== 'POST') return sendJson(res, 405, { ok: false, error: 'POST only' })
          return sendJson(res, 200, handleDelete(url))
        }
        return sendJson(res, 404, { ok: false, error: 'unknown action: ' + action })
      } catch (e) {
        log('处理 ' + action + ' 失败: ' + (e && e.message))
        return sendJson(res, 500, { ok: false, error: String(e && e.message || e) })
      }
    },
  })

  log('笔记接口已挂载 ' + PREFIX + '（list/get/save/delete/graph/tasks/stats/upload/asset/chat）')
  ctx.effect(() => () => {
    try { disposer() } catch {}
  }, 'siyi-vault: dispose')
}
