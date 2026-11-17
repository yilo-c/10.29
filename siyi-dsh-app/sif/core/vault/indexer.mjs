// 增量索引（T09）
//
// 为什么需要：
//   当前 list/stats/search 每次都全量 readdir + readFileSync + parseNote。
//   实测 56 篇 44ms；600 篇约 437ms；2000 篇会到秒级。
//   而绝大多数请求时文件根本没变。
//
// 设计原则（对应规范 §7.4）：
//   1. 索引是可删除、可重建的派生物，不是真源
//   2. 用 (mtimeMs, size) 判断是否需要重读，未变直接命中内存
//   3. 文件事件只作加速器，必须能全量对账（reconcile）
//   4. 解析失败的文件单独记录，不拖垮全库
//   5. 不引入 SQLite —— 56~2000 篇规模，内存 Map 足够且零依赖

import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'

const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/

export function createIndexer({ root, parseNote, extractLinks, now = () => Date.now() } = {}) {
  if (!root) throw new Error('root 必填')
  const realRoot = fs.realpathSync(root)

  /** filePath -> { mtimeMs, size, id, meta, body, links, tags, revision, error } */
  const entries = new Map()
  /** 解析失败的文件：filePath -> { error, at } */
  const failures = new Map()

  let lastFullScanAt = 0
  let lastFullScanMs = 0
  let scans = 0
  let incrementalHits = 0
  /** 脏标记：外部写入/失效后置位，下次 scan 强制穿透节流。 */
  let dirtyFlag = false

  /** 枚举所有候选 .md 文件路径。 */
  function enumerateFiles() {
    const out = []
    let years = []
    try { years = fs.readdirSync(realRoot).filter(n => /^\d{4}$/.test(n)) } catch { return out }
    for (const y of years) {
      let months = []
      try { months = fs.readdirSync(path.join(realRoot, y)) } catch { continue }
      for (const mo of months) {
        const dir = path.join(realRoot, y, mo)
        let files = []
        try { files = fs.readdirSync(dir) } catch { continue }
        for (const f of files) {
          if (!f.endsWith('.md')) continue
          const id = f.slice(0, -3)
          if (!ULID_RE.test(id)) continue
          out.push({ path: path.join(dir, f), id })
        }
      }
    }
    return out
  }

  /** 读取并解析一个文件，更新 entries。返回是否重读了。 */
  function refreshFile(filePath, id, st) {
    let text
    try {
      text = fs.readFileSync(filePath, 'utf8')
    } catch (e) {
      failures.set(filePath, { error: String(e && e.message || e), at: now() })
      entries.delete(filePath)
      return true
    }
    let parsed
    try {
      parsed = parseNote(text)
    } catch (e) {
      // 解析失败：记下来，不拖垮全库
      failures.set(filePath, { error: String(e && e.message || e), at: now() })
      entries.delete(filePath)
      return true
    }
    failures.delete(filePath)
    const { meta, body } = parsed
    const { links, tags } = extractLinks(body)
    entries.set(filePath, {
      mtimeMs: st.mtimeMs,
      size: st.size,
      id,
      meta,
      body,
      links,
      tags,
      revision: createHash('sha256').update(Buffer.from(text, 'utf8')).digest('hex'),
      logicalPath: path.relative(realRoot, filePath).split(path.sep).join('/'),
    })
    return true
  }

  /**
   * 增量扫描：只重读 mtime/size 变化的文件。
   * 这是所有查询的入口，内部做节流。
   */
  function scan({ force = false, minIntervalMs = 250 } = {}) {
    const t0 = now()
    // 脏标记优先于节流：外部刚写过，必须重新扫，否则读到旧数据
    if (!force && !dirtyFlag && t0 - lastFullScanAt < minIntervalMs) {
      incrementalHits++
      return { scanned: false, files: entries.size }
    }
    dirtyFlag = false

    const files = enumerateFiles()
    const seen = new Set()
    let reread = 0

    for (const { path: fp, id } of files) {
      seen.add(fp)
      let st
      try { st = fs.statSync(fp) } catch { continue }
      const prev = entries.get(fp)
      if (prev && prev.mtimeMs === st.mtimeMs && prev.size === st.size) continue
      refreshFile(fp, id, st)
      reread++
    }

    // 删除已消失的文件
    for (const fp of [...entries.keys()]) {
      if (!seen.has(fp)) entries.delete(fp)
    }
    for (const fp of [...failures.keys()]) {
      if (!seen.has(fp)) failures.delete(fp)
    }

    lastFullScanAt = now()
    lastFullScanMs = lastFullScanAt - t0
    scans++
    return { scanned: true, files: entries.size, reread, ms: lastFullScanMs }
  }

  /** 取全部索引条目（先增量扫描）。 */
  function all() {
    scan()
    return [...entries.values()]
  }

  /** 按 id 查找。 */
  function byId(id) {
    scan()
    for (const e of entries.values()) if (e.id === id) return e
    return null
  }

  /** 强制全量重建（reconcile 用）。 */
  function rebuild() {
    entries.clear()
    failures.clear()
    return scan({ force: true, minIntervalMs: 0 })
  }

  /** 索引健康状态，给 UI/工具观测用。 */
  function stats() {
    scan()
    return {
      files: entries.size,
      failures: failures.size,
      failureDetails: [...failures.entries()].slice(0, 5).map(([p, f]) => ({
        path: path.relative(realRoot, p), error: f.error,
      })),
      scans,
      incrementalHits,
      lastScanMs: lastFullScanMs,
      stale: failures.size > 0,
    }
  }

  /** 外部写入后主动失效某个文件（save/delete 时调用，避免等下次扫描）。 */
  function invalidate(filePath) {
    entries.delete(filePath)
    failures.delete(filePath)
    dirtyFlag = true
  }

  /** 外部写入后主动更新（save 后直接塞入，省一次读盘）。 */
  function put(filePath, { mtimeMs, size, id, meta, body, links, tags, revision }) {
    entries.set(filePath, {
      mtimeMs, size, id, meta, body, links, tags, revision,
      logicalPath: path.relative(realRoot, filePath).split(path.sep).join('/'),
    })
    failures.delete(filePath)
    // put 是写入后的主动更新，索引已是最新，不需要置脏；
    // 但要让下一次 scan 不因节流而漏掉其它文件的真实变化
    dirtyFlag = true
  }

  /** 显式标记需要重新扫描（外部修改文件后调用）。 */
  function markDirty() {
    dirtyFlag = true
  }

  return { scan, all, byId, rebuild, stats, invalidate, put, markDirty, root: realRoot, entries, failures }
}
