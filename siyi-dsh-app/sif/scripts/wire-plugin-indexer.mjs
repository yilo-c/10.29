// 给运行中的 siyi-vault.js 接入增量索引（幂等）
//
// 目标：笔记面板的 list/stats/search 也走索引，不再每次全量读盘。
// 插件没有独立索引实例，所以直接复用 sif 的 indexer 模块。

import fs from 'node:fs'
const TARGET = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const MARK = 'SIYI-INDEX-20261118'

let s = fs.readFileSync(TARGET, 'utf8')
if (s.includes(MARK)) { console.log('SKIP: 已接入'); process.exit(0) }

// 1. 顶部 import indexer
const oldImport = `import crypto from 'node:crypto'`
const newImport = `import crypto from 'node:crypto'
// ${MARK}：复用 sif 的增量索引，避免每次请求全量读盘
import { createIndexer } from '/storage/emulated/0/思忆集/app/sif/core/vault/indexer.mjs'`
if (!s.includes(oldImport)) { console.error('IMPORT NOT FOUND'); process.exit(1) }
s = s.replace(oldImport, newImport)

// 2. 在 allNotes 之后创建索引实例（需要 parseMd / extractLinks 已定义）
const anchor = '/** 按 id 找文件路径，找不到返回 null。 */'
const idxInit = `// ${MARK}：索引实例。allNotes 仍保留（写入路径与兼容用），
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

/** 按 id 找文件路径，找不到返回 null。 */`
if (!s.includes(anchor)) { console.error('ANCHOR NOT FOUND'); process.exit(1) }
s = s.replace(anchor, idxInit)

// 3. handleList 改走索引
const oldList = `  let items = allNotes().map(n => ({
    id: n.id,
    title: String(n.meta.title ?? '(无标题)'),
    kind: n.meta.kind || 'note',
    tags: Array.isArray(n.meta.tags) ? n.meta.tags : [],
    status: n.meta.status || null,
    due: n.meta.due || null,
    created: n.meta.created || null,
    updated: n.meta.updated || null,
    folder: n.meta.folder || null,
  }))`
const newList = `  // ${MARK}：走索引，不再每次全量读盘
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
  }))`
if (!s.includes(oldList)) { console.error('LIST NOT FOUND'); process.exit(1) }
s = s.replace(oldList, newList)

// 4. handleStats 改走索引
const oldStats = `function handleStats() {
  const notes = allNotes()`
const newStats = `function handleStats() {
  // ${MARK}：走索引
  const notes = allNoteSummaries()`
if (!s.includes(oldStats)) { console.error('STATS NOT FOUND'); process.exit(1) }
s = s.replace(oldStats, newStats)

// 5. handleFolders 改走索引
const oldFolders = `  for (const n of allNotes()) {
    const f = n.meta.folder`
const newFolders = `  for (const n of allNoteSummaries()) {
    const f = n.meta.folder`
if (!s.includes(oldFolders)) { console.error('FOLDERS NOT FOUND'); process.exit(1) }
s = s.replace(oldFolders, newFolders)

// 6. handleTasks 改走索引
const oldTasks = `  const items = allNotes()
    .filter(n => n.meta.kind === 'task')`
const newTasks = `  const items = allNoteSummaries()
    .filter(n => n.meta.kind === 'task')`
if (!s.includes(oldTasks)) { console.error('TASKS NOT FOUND'); process.exit(1) }
s = s.replace(oldTasks, newTasks)

// 7. handleSave 更新后失效索引
const oldSaveRet = `    writeFileAtomicSync(target, text)
    return { ok: true, id: data.id, created: false, updated: now, revision: contentHashOf(text) }`
const newSaveRet = `    writeFileAtomicSync(target, text)
    invalidateIndex(target)
    return { ok: true, id: data.id, created: false, updated: now, revision: contentHashOf(text) }`
if (!s.includes(oldSaveRet)) { console.error('SAVE UPDATE RET NOT FOUND'); process.exit(1) }
s = s.replace(oldSaveRet, newSaveRet)

const oldCreateRet = `  writeFileAtomicSync(target, text)
  return { ok: true, id, created: true, updated: now, revision: contentHashOf(text) }`
const newCreateRet = `  writeFileAtomicSync(target, text)
  invalidateIndex(target)
  return { ok: true, id, created: true, updated: now, revision: contentHashOf(text) }`
if (!s.includes(oldCreateRet)) { console.error('SAVE CREATE RET NOT FOUND'); process.exit(1) }
s = s.replace(oldCreateRet, newCreateRet)

// 8. handleDelete 失效索引
const oldDel = `  fs.renameSync(p, dst)
  return { ok: true, id, trashed: path.basename(dst) }`
const newDel = `  fs.renameSync(p, dst)
  invalidateIndex(p)
  return { ok: true, id, trashed: path.basename(dst) }`
if (!s.includes(oldDel)) { console.error('DELETE NOT FOUND'); process.exit(1) }
s = s.replace(oldDel, newDel)

fs.writeFileSync(TARGET, s, 'utf8')
console.log('PATCHED: ' + TARGET)
