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

export const name = 'siyi-vault'
export const inject = ['tools']

const VAULT = '/sdcard/思忆集/vault'
const PREFIX = '/siyi-vault'

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
    if (/^-?\d+(\.\d+)?$/.test(v)) { meta[k] = Number(v); continue }
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

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj)
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  })
  res.end(body)
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    let size = 0
    req.on('data', c => {
      size += c.length
      if (size > 4 * 1024 * 1024) { reject(new Error('body too large')); req.destroy(); return }
      chunks.push(c)
    })
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')))
    req.on('error', reject)
  })
}

// ── 业务处理 ────────────────────────────────────────────────────────────

/** 列表：支持 kind / 标签 / 关键词过滤，按 updated 倒序。 */
function handleList(url) {
  const q = url.searchParams.get('q') || ''
  const kind = url.searchParams.get('kind') || ''
  const tag = url.searchParams.get('tag') || ''
  const status = url.searchParams.get('status') || ''
  const limit = Math.min(Number(url.searchParams.get('limit')) || 500, 2000)

  let items = allNotes().map(n => ({
    id: n.id,
    title: n.meta.title || '(无标题)',
    kind: n.meta.kind || 'note',
    tags: Array.isArray(n.meta.tags) ? n.meta.tags : [],
    status: n.meta.status || null,
    due: n.meta.due || null,
    created: n.meta.created || null,
    updated: n.meta.updated || null,
    folder: n.meta.folder || null,
  }))
  if (kind) items = items.filter(i => i.kind === kind)
  if (status) items = items.filter(i => i.status === status)
  if (tag) items = items.filter(i => i.tags.includes(tag))
  if (q) {
    const k = q.toLowerCase()
    items = items.filter(i =>
      (i.title || '').toLowerCase().includes(k) ||
      i.tags.some(t => t.toLowerCase().includes(k)))
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

/** 保存：有 id 就更新，没有就新建。 */
async function handleSave(req) {
  const raw = await readBody(req)
  let data
  try { data = JSON.parse(raw) } catch { return { ok: false, error: 'invalid json' } }

  const now = new Date().toISOString()
  const id = data.id || ulid()
  let p = data.id ? findById(data.id) : null

  if (p) {
    // 更新已有
    const txt = fs.readFileSync(p, 'utf8')
    const { meta, body } = parseMd(txt)
    if (typeof data.title === 'string') meta.title = data.title
    if (typeof data.body === 'string') { /* 正文单独处理 */ }
    if (Array.isArray(data.tags)) meta.tags = data.tags
    if (typeof data.status === 'string') meta.status = data.status
    if (typeof data.folder === 'string') meta.folder = data.folder
    if (data.due !== undefined) meta.due = data.due || null
    if (typeof data.kind === 'string') meta.kind = data.kind
    meta.updated = now
    const newBody = typeof data.body === 'string' ? data.body : body
    fs.writeFileSync(p, buildMd(meta, newBody), 'utf8')
    return { ok: true, id, created: false, updated: now }
  }

  // 新建
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
  const ym = now.slice(0, 7).replace('-', '-')
  const dir = path.join(VAULT, now.slice(0, 4), ym)
  fs.mkdirSync(dir, { recursive: true })
  p = path.join(dir, id + '.md')
  fs.writeFileSync(p, buildMd(meta, data.body || ''), 'utf8')
  return { ok: true, id, created: true, updated: now }
}

/** 删除：移到 .siyi/trash，不真删。 */
function handleDelete(url) {
  const id = url.searchParams.get('id') || ''
  const p = findById(id)
  if (!p) return { ok: false, error: 'not found' }
  const trash = path.join(VAULT, '.siyi', 'trash')
  fs.mkdirSync(trash, { recursive: true })
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const dst = path.join(trash, stamp + '__' + path.basename(p))
  fs.renameSync(p, dst)
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
  const items = allNotes()
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
  const notes = allNotes()
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
      try {
        if (action === 'list') return sendJson(res, 200, handleList(url))
        if (action === 'get') return sendJson(res, 200, handleGet(url))
        if (action === 'graph') return sendJson(res, 200, handleGraph())
        if (action === 'tasks') return sendJson(res, 200, handleTasks(url))
        if (action === 'stats') return sendJson(res, 200, handleStats())
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

  log('笔记接口已挂载 ' + PREFIX + '（list/get/save/delete/graph/tasks/stats）')
  ctx.effect(() => () => {
    try { disposer() } catch {}
  }, 'siyi-vault: dispose')
}
