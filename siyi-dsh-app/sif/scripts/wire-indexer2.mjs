// 让 list/stats 走索引，并在写入后主动更新（幂等）
import fs from 'node:fs'
const p = 'core/vault/service.mjs'
let s = fs.readFileSync(p, 'utf8')

if (s.includes('// LIST-VIA-INDEX')) { console.log('SKIP: 已接入'); process.exit(0) }

// ── list() 改为走索引 ─────────────────────────────────────
const listStart = '  function list({ kind = \'\', status = \'\', folder = \'\', tag = \'\', q = \'\', includeSensitive = false, limit = 500 } = {}) {'
const i = s.indexOf(listStart)
if (i < 0) { console.error('LIST START NOT FOUND'); process.exit(1) }

// 找到函数结尾：下一个 "\n  }\n" 在 items.sort 之后
const marker = "    out.sort((a, b) => String(b.updated || '').localeCompare(String(a.updated || '')))\n    return { total: out.length, items: out.slice(0, limit) }\n  }"
const j = s.indexOf(marker, i)
if (j < 0) { console.error('LIST END NOT FOUND'); process.exit(1) }
const listEnd = j + marker.length

const newList = `  // LIST-VIA-INDEX：走增量索引，不再每次全量读盘
  function list({ kind = '', status = '', folder = '', tag = '', q = '', includeSensitive = false, limit = 500 } = {}) {
    const items = []
    for (const e of indexer.all()) {
      const meta = e.meta
      if (!includeSensitive && meta.sensitivity === 'secret') continue
      items.push({
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
  }`

s = s.slice(0, i) + newList + s.slice(listEnd)

// ── 写入后主动更新索引 ────────────────────────────────────
// create：写完塞入索引
const oldCreate = `    const target = assertInside(realRoot, path.join(y, ym, id + '.md'))
    writeFileAtomic(target, text)
    return { id, revision: contentHash(text), logicalPath: path.relative(realRoot, target).split(path.sep).join('/') }`
const newCreate = `    const target = assertInside(realRoot, path.join(y, ym, id + '.md'))
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
    return { id, revision: contentHash(text), logicalPath: path.relative(realRoot, target).split(path.sep).join('/') }`
if (!s.includes(oldCreate)) { console.error('CREATE NOT FOUND'); process.exit(1) }
s = s.replace(oldCreate, newCreate)

// update：写完更新索引
const oldUpdate = `    writeFileAtomic(hit.path, text)
    return { id, revision: contentHash(text), meta: next }`
const newUpdate = `    writeFileAtomic(hit.path, text)
    try {
      const st = fs.statSync(hit.path)
      const parsed = parseNote(text)
      const ex = extractLinks(parsed.body)
      indexer.put(hit.path, {
        mtimeMs: st.mtimeMs, size: st.size, id, meta: parsed.meta,
        body: parsed.body, links: ex.links, tags: ex.tags, revision: contentHash(text),
      })
    } catch {}
    return { id, revision: contentHash(text), meta: next }`
if (!s.includes(oldUpdate)) { console.error('UPDATE NOT FOUND'); process.exit(1) }
s = s.replace(oldUpdate, newUpdate)

// trash：删除后失效索引
const oldTrash = `    fs.renameSync(hit.path, dst)
    return { id, trashed: path.basename(dst) }`
const newTrash = `    fs.renameSync(hit.path, dst)
    indexer.invalidate(hit.path)
    return { id, trashed: path.basename(dst) }`
if (!s.includes(oldTrash)) { console.error('TRASH NOT FOUND'); process.exit(1) }
s = s.replace(oldTrash, newTrash)

// stats：附带索引健康状态
const oldStats = `    return { total, byKind, byStatus, root: realRoot }`
const newStats = `    return { total, byKind, byStatus, root: realRoot, index: indexer.stats() }`
if (!s.includes(oldStats)) { console.error('STATS NOT FOUND'); process.exit(1) }
s = s.replace(oldStats, newStats)

// reconcile：重建索引
const oldRecon = `    const { total } = list({ limit: 100000 })
    return { swept, notes: total }`
const newRecon = `    indexer.rebuild()
    const { total } = list({ limit: 100000 })
    return { swept, notes: total, index: indexer.stats() }`
if (!s.includes(oldRecon)) { console.error('RECONCILE NOT FOUND'); process.exit(1) }
s = s.replace(oldRecon, newRecon)

// 导出索引器供观测
const oldRet = `  return { get, list, create, update, trash: trashNote, stats, reconcile, parseNote, buildNote, extractLinks, root: realRoot }`
const newRet = `  return { get, list, create, update, trash: trashNote, stats, reconcile, parseNote, buildNote, extractLinks, indexer, root: realRoot }`
if (!s.includes(oldRet)) { console.error('RETURN NOT FOUND'); process.exit(1) }
s = s.replace(oldRet, newRet)

fs.writeFileSync(p, s, 'utf8')
console.log('PATCHED: list/stats via index, writes update index')
