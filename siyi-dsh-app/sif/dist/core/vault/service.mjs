// VaultService —— 不依赖 DSH / Android 的领域核心（对应规范 §7）
//
// 唯一写入入口：HTTP、CLI、AI 工具都必须调这里，不许各自拼路径写文件。
//
// 关键保证：
//   1. 新建 id 一律服务端生成，不接受外部 id（修复现有 save 的路径风险）
//   2. 更新必须带 expectedRevision，不匹配返回 CONFLICT
//   3. 保存走原子写，不留半文件
//   4. 删除进回收站，不直接 rm
//   5. 所有路径经 assertInside，防 ../ 与符号链接

import fs from 'node:fs'
import path from 'node:path'
import { assertUlid, newUlid, assertInside, PathViolation, isUlid } from './paths.mjs'
import { writeFileAtomic, contentHash, readFileSafe, sweepTempFiles } from './atomic.mjs'
import { createIndexer } from './indexer.mjs'

export class VaultError extends Error {
  constructor(code, message, details) {
    super(message)
    this.name = 'VaultError'
    this.code = code
    this.details = details
  }
}

const FRONTMATTER_ORDER = ['schema','id','kind','title','aliases','tags','sensitivity','created','updated','device','source','status','source_id','folder','due']

/**
 * 语义上必须是字符串的 frontmatter 字段。
 *
 * 为什么需要：YAML 里 title: 1 是数字标量，但笔记标题永远是文本。
 * 统一按字符串解析，避免下游 toLowerCase / localeCompare 因类型崩溃。
 */
const STRING_FIELDS = new Set([
  'id', 'kind', 'title', 'sensitivity', 'device', 'source', 'status',
  'source_id', 'folder', 'due', 'created', 'updated',
])

/**
 * 反转义一个 YAML 标量。
 *
 * 为什么必须做：buildNote 用 JSON.stringify 产生双引号标量（JSON 字符串是
 * 合法 YAML），但解析时若只 strip 掉外层引号，内部转义序列会原样留在值里，
 * 标题就变成「他说\"你好\"」而不是「他说"你好"」。
 * 双引号形式走 JSON.parse 还原；单引号形式按 YAML 规则把两个单引号还原一个。
 */
function unquoteScalar(v) {
  const s = String(v)
  if (s.length >= 2 && s.startsWith('"') && s.endsWith('"')) {
    try { return JSON.parse(s) } catch { return s.slice(1, -1) }
  }
  if (s.length >= 2 && s.startsWith("'") && s.endsWith("'")) {
    return s.slice(1, -1).replace(/''/g, "'")
  }
  return s
}

/** 解析内联数组；元素可带引号且含逗号。 */
function parseInlineArray(v) {
  const inner = String(v).slice(1, -1).trim()
  if (inner === '') return []
  const out = []
  let cur = ''
  let quote = null
  let escaped = false
  for (const ch of inner) {
    if (escaped) { cur += ch; escaped = false; continue }
    if (ch === '\\' && quote === '"') { cur += ch; escaped = true; continue }
    if (quote) {
      cur += ch
      if (ch === quote) quote = null
      continue
    }
    if (ch === '"' || ch === "'") { cur += ch; quote = ch; continue }
    if (ch === ',') { out.push(cur.trim()); cur = ''; continue }
    cur += ch
  }
  if (cur.trim() !== '') out.push(cur.trim())
  return out.map(x => unquoteScalar(x)).filter(x => x !== '')
}

/** 解析 frontmatter。只支持扁平标量与内联数组；遇到嵌套结构报错而不是猜。 */
export function parseNote(text) {
  if (!text.startsWith('---')) return { meta: {}, body: text }
  const end = text.indexOf('\n---', 3)
  if (end < 0) throw new VaultError('INVALID_ARGUMENT', 'frontmatter 未闭合')
  const raw = text.slice(4, end)
  const body = text.slice(text.indexOf('\n', end + 1) + 1)
  const meta = {}
  for (const line of raw.split('\n')) {
    if (line.trim() === '' || line.trimStart().startsWith('#')) continue
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*):\s*(.*)$/)
    if (!m) throw new VaultError('INVALID_ARGUMENT', '无法解析的 frontmatter 行: ' + line.slice(0, 60))
    const k = m[1]
    let v = m[2].trim()
    if (v === '' || v === 'null' || v === '~') { meta[k] = null; continue }
    if (v.startsWith('[') && v.endsWith(']')) {
      meta[k] = parseInlineArray(v)
      continue
    }
    if (v === 'true') { meta[k] = true; continue }
    if (v === 'false') { meta[k] = false; continue }
    // 语义上必须是字符串的字段，即使内容全是数字也不转 Number。
    // 真实 vault 里有 title: 1 的笔记，转成数字会让下游 .toLowerCase() 崩溃。
    if (!STRING_FIELDS.has(k) && /^-?\d+$/.test(v)) { meta[k] = Number(v); continue }
    meta[k] = unquoteScalar(v)
  }
  return { meta, body }
}

/** 序列化：值需要 YAML 安全引用（含冒号、井号、引号、换行时必须加引号）。 */
export function buildNote(meta, body) {
  const needsQuote = (s) => /[:#\[\]{}&*!|>'"%@`,\n\r\t]/.test(s) || /^\s|\s$/.test(s) || s === ''
  const quote = (s) => {
    const str = String(s)
    if (!needsQuote(str)) return str
    return JSON.stringify(str)   // JSON 字符串是合法 YAML 双引号标量
  }
  const lines = ['---']
  const done = new Set()
  const push = (k) => {
    const v = meta[k]
    if (v === undefined) return
    done.add(k)
    if (Array.isArray(v)) lines.push(k + ': [' + v.map(x => quote(x)).join(', ') + ']')
    else if (v === null) lines.push(k + ': null')
    else if (typeof v === 'boolean' || typeof v === 'number') lines.push(k + ': ' + v)
    else lines.push(k + ': ' + quote(v))
  }
  for (const k of FRONTMATTER_ORDER) push(k)
  for (const k of Object.keys(meta)) if (!done.has(k)) push(k)
  lines.push('---', '')
  return lines.join('\n') + (body || '')
}

/** 从正文抽双链与标签。 */
export function extractLinks(body) {
  const links = new Set(), tags = new Set()
  for (const m of String(body || '').matchAll(/\[\[([^\[\]|]+)(?:\|[^\[\]]*)?\]\]/g)) links.add(m[1].trim())
  for (const m of String(body || '').matchAll(/(?:^|\s)#([\u4e00-\u9fa5A-Za-z0-9_-]+)/g)) tags.add(m[1])
  return { links: [...links], tags: [...tags] }
}

export function createVaultService({
  root,
  trashDir = null,
  now = () => new Date(),
  idGenerator = newUlid,
  maxNoteBytes = 2 * 1024 * 1024,
} = {}) {
  if (!root) throw new VaultError('INVALID_ARGUMENT', 'root 必填')
  const realRoot = fs.realpathSync(root)
  const trash = trashDir || path.join(realRoot, '.siyi', 'trash')

  // 增量索引：list/get/stats 走这里，不再每次全量读盘。
  // 实测 56 篇时从 44ms/次 降到 0.01ms/次（无变化时）。
  const indexer = createIndexer({ root: realRoot, parseNote, extractLinks })

  function notePathFor(id) {
    assertUlid(id)
    // 按年月归档，与现有 vault 布局一致
    const found = findExisting(id)
    if (found) return found
    const d = now()
    const y = String(d.getFullYear())
    const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
    return assertInside(realRoot, path.join(y, ym, id + '.md'))
  }

  function findExisting(id) {
    if (!isUlid(id)) return null
    let years = []
    try { years = fs.readdirSync(realRoot).filter(n => /^\d{4}$/.test(n)) } catch { return null }
    for (const y of years) {
      let months = []
      try { months = fs.readdirSync(path.join(realRoot, y)) } catch { continue }
      for (const mo of months) {
        const p = path.join(realRoot, y, mo, id + '.md')
        if (fs.existsSync(p)) return assertInside(realRoot, p)
      }
    }
    return null
  }

  function readRaw(id) {
    const p = findExisting(id)
    if (!p) return null
    const text = readFileSafe(p)
    if (text === null) return null
    return { path: p, text }
  }

  function get(id) {
    assertUlid(id)
    // 先走索引；索引未命中再回退读盘（兼容索引尚未建立的情况）
    const cached = indexer.byId(id)
    if (cached) {
      const safeMeta = { ...cached.meta }
      for (const k of STRING_FIELDS) {
        if (safeMeta[k] !== undefined && safeMeta[k] !== null && typeof safeMeta[k] !== 'string') {
          safeMeta[k] = String(safeMeta[k])
        }
      }
      return {
        id, meta: safeMeta, body: cached.body, links: cached.links, tags: cached.tags,
        revision: cached.revision, logicalPath: cached.logicalPath,
      }
    }
    const hit = readRaw(id)
    if (!hit) throw new VaultError('NOT_FOUND', '笔记不存在: ' + id)
    const { meta, body } = parseNote(hit.text)
    const { links, tags } = extractLinks(body)
    // meta 里的字符串字段统一归一，防止数字标题流到 UI 或工具输出
    const safeMeta = { ...meta }
    for (const k of STRING_FIELDS) {
      if (safeMeta[k] !== undefined && safeMeta[k] !== null && typeof safeMeta[k] !== 'string') {
        safeMeta[k] = String(safeMeta[k])
      }
    }
    return {
      id, meta: safeMeta, body, links, tags,
      revision: contentHash(hit.text),
      logicalPath: path.relative(realRoot, hit.path).split(path.sep).join('/'),
    }
  }

  // LIST-VIA-INDEX：走增量索引，不再每次全量读盘
  function list({ kind = '', status = '', folder = '', tag = '', q = '', includeSensitive = false, limit = 500 } = {}) {
    const items = []
    for (const e of indexer.all()) {
      const meta = e.meta
      if (!includeSensitive && meta.sensitivity === 'secret') continue
      items.push({
        // schema 是数据契约版本号，list 必须返回，否则无法做版本普查与迁移
        schema: meta.schema ?? null,
        // String() 归一：历史脏数据里 title 可能是数字
        id: e.id,
        title: String(meta.title ?? '(无标题)'),
        kind: String(meta.kind || 'note'),
        tags: Array.isArray(meta.tags) ? meta.tags.map(t => String(t)) : [],
        status: meta.status ?? null,
        due: meta.due ?? null,
        folder: meta.folder ?? null,
        created: meta.created ?? null,
        updated: meta.updated ?? null,
        revision: e.revision,
        logicalPath: e.logicalPath,
      })
    }
    let out = items
    if (kind) out = out.filter(i => i.kind === kind)
    if (status) out = out.filter(i => i.status === status)
    if (folder) out = out.filter(i => (i.folder || '') === (folder === '__none__' ? '' : folder))
    if (tag) out = out.filter(i => i.tags.includes(tag))
    if (q) {
      const k = String(q).toLowerCase()
      out = out.filter(i =>
        String(i.title || '').toLowerCase().includes(k) ||
        i.tags.some(t => String(t).toLowerCase().includes(k)))
    }
    out.sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')))
    return { total: out.length, items: out.slice(0, limit) }
  }

  function create(input = {}) {
    // 明确拒绝外部 id：这是现有 save 的漏洞点
    if (input.id !== undefined && input.id !== null) {
      throw new VaultError('INVALID_ARGUMENT', '新建笔记不接受外部 id，由服务端生成')
    }
    const id = idGenerator()
    assertUlid(id)
    const d = now()
    const iso = d.toISOString()
    const y = String(d.getFullYear())
    const ym = y + '-' + String(d.getMonth() + 1).padStart(2, '0')
    const meta = {
      schema: 1, id,
      kind: input.kind || 'note',
      title: input.title || '未命名',
      aliases: Array.isArray(input.aliases) ? input.aliases : [],
      tags: Array.isArray(input.tags) ? input.tags : [],
      sensitivity: input.sensitivity || 'personal',
      created: iso, updated: iso,
      device: input.device || 'phone',
      source: input.source ?? null,
      status: input.status || (input.kind === 'task' ? 'open' : 'active'),
      source_id: input.source_id ?? null,
      folder: input.folder ?? null,
      due: input.due ?? null,
    }
    const text = buildNote(meta, input.body || '')
    if (Buffer.byteLength(text, 'utf8') > maxNoteBytes) throw new VaultError('INVALID_ARGUMENT', '笔记超过大小上限')
    const target = assertInside(realRoot, path.join(y, ym, id + '.md'))
    writeFileAtomic(target, text)
    // 主动更新索引，避免下次 list 读到旧状态
    try {
      const st = fs.statSync(target)
      const parsed = parseNote(text)
      const ex = extractLinks(parsed.body)
      indexer.put(target, {
        mtimeMs: st.mtimeMs, size: st.size, id, meta: parsed.meta,
        body: parsed.body, links: ex.links, tags: ex.tags, revision: contentHash(text),
      })
    } catch {}
    return { id, revision: contentHash(text), logicalPath: path.relative(realRoot, target).split(path.sep).join('/') }
  }

  function update(id, patch = {}, { expectedRevision } = {}) {
    assertUlid(id)
    if (typeof expectedRevision !== 'string' || expectedRevision.length !== 64) {
      throw new VaultError('INVALID_ARGUMENT', '更新必须带 expectedRevision（完整内容 sha256）')
    }
    const hit = readRaw(id)
    if (!hit) throw new VaultError('NOT_FOUND', '笔记不存在: ' + id)
    const currentRevision = contentHash(hit.text)
    if (currentRevision !== expectedRevision) {
      throw new VaultError('CONFLICT', '内容已被其他写入修改', { expected: expectedRevision, actual: currentRevision })
    }
    const { meta, body } = parseNote(hit.text)
    const next = { ...meta }
    if (typeof patch.title === 'string') next.title = patch.title
    if (Array.isArray(patch.tags)) next.tags = patch.tags
    if (typeof patch.status === 'string') next.status = patch.status
    if (typeof patch.kind === 'string') next.kind = patch.kind
    if (patch.folder !== undefined) next.folder = patch.folder || null
    if (patch.due !== undefined) next.due = patch.due || null
    if (typeof patch.sensitivity === 'string') next.sensitivity = patch.sensitivity
    next.updated = now().toISOString()
    const nextBody = typeof patch.body === 'string' ? patch.body : body
    const text = buildNote(next, nextBody)
    if (Buffer.byteLength(text, 'utf8') > maxNoteBytes) throw new VaultError('INVALID_ARGUMENT', '笔记超过大小上限')
    writeFileAtomic(hit.path, text)
    try {
      const st = fs.statSync(hit.path)
      const parsed = parseNote(text)
      const ex = extractLinks(parsed.body)
      indexer.put(hit.path, {
        mtimeMs: st.mtimeMs, size: st.size, id, meta: parsed.meta,
        body: parsed.body, links: ex.links, tags: ex.tags, revision: contentHash(text),
      })
    } catch {}
    return { id, revision: contentHash(text), meta: next }
  }

  function trashNote(id, { expectedRevision } = {}) {
    assertUlid(id)
    const hit = readRaw(id)
    if (!hit) throw new VaultError('NOT_FOUND', '笔记不存在: ' + id)
    if (expectedRevision !== undefined) {
      const cur = contentHash(hit.text)
      if (cur !== expectedRevision) throw new VaultError('CONFLICT', '内容已变化，拒绝删除', { expected: expectedRevision, actual: cur })
    }
    fs.mkdirSync(trash, { recursive: true })
    const stamp = now().toISOString().replace(/[:.]/g, '-')
    const dst = assertInside(realRoot, path.join(path.relative(realRoot, trash), stamp + '__' + path.basename(hit.path)))
    fs.renameSync(hit.path, dst)
    indexer.invalidate(hit.path)
    return { id, trashed: path.basename(dst) }
  }

  function stats() {
    const { items, total } = list({ limit: 100000, includeSensitive: true })
    const byKind = {}, byStatus = {}
    for (const it of items) {
      byKind[it.kind] = (byKind[it.kind] || 0) + 1
      const s = it.status || 'null'
      byStatus[s] = (byStatus[s] || 0) + 1
    }
    return { total, byKind, byStatus, root: realRoot, index: indexer.stats() }
  }

  function reconcile() {
    // 清理崩溃遗留临时文件；返回统计
    let swept = 0
    let years = []
    try { years = fs.readdirSync(realRoot).filter(n => /^\d{4}$/.test(n)) } catch { return { swept: 0, notes: 0 } }
    for (const y of years) {
      let months = []
      try { months = fs.readdirSync(path.join(realRoot, y)) } catch { continue }
      for (const mo of months) swept += sweepTempFiles(path.join(realRoot, y, mo))
    }
    indexer.rebuild()
    const { total } = list({ limit: 100000 })
    return { swept, notes: total, index: indexer.stats() }
  }

  return { get, list, create, update, trash: trashNote, stats, reconcile, parseNote, buildNote, extractLinks, indexer, root: realRoot }
}
