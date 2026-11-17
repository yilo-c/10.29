// 修复：frontmatter 里 title 被解析成数字导致搜索崩溃
//
// 现象：vault_search(q="知识") 报 (i.title || "").toLowerCase is not a function
// 根因：真实 vault 里有一篇笔记 title 是 "1"，
//       parseNote 把纯数字标量一律转成 Number，
//       于是 meta.title 变成数字 1，查询层 .toLowerCase() 就崩。
//
// 修复分两层（缺一不可）：
//   1. 解析层：声明为字符串语义的字段不做数字转换，保持原始字符串
//   2. 查询层：所有面向用户展示的文本字段做 String() 归一，防止历史脏数据再触发
//
// 为什么不只修解析层：已有笔记可能还有别的类型不一致，
// 查询层归一化是最后一道防线，代价只有一次 String() 调用。

import fs from 'node:fs'

const p = 'core/vault/service.mjs'
let s = fs.readFileSync(p, 'utf8')

// ── 1. 解析层：这些字段永远是字符串 ─────────────────────────
const oldNum = `    if (/^-?\\d+$/.test(v)) { meta[k] = Number(v); continue }
    meta[k] = unquoteScalar(v)`

const newNum = `    // 语义上必须是字符串的字段，即使内容全是数字也不转 Number。
    // 真实 vault 里有 title: 1 的笔记，转成数字会让下游 .toLowerCase() 崩溃。
    if (!STRING_FIELDS.has(k) && /^-?\\d+$/.test(v)) { meta[k] = Number(v); continue }
    meta[k] = unquoteScalar(v)`

if (!s.includes(oldNum)) { console.error('PARSE NUM BLOCK NOT FOUND'); process.exit(1) }
s = s.replace(oldNum, newNum)

// 在 parseNote 之前插入常量
const anchor = '/**\n * 反转义一个 YAML 标量。'
const constBlock = `/**
 * 语义上必须是字符串的 frontmatter 字段。
 *
 * 为什么需要：YAML 里 title: 1 是数字标量，但笔记标题永远是文本。
 * 统一按字符串解析，避免下游 toLowerCase / localeCompare 因类型崩溃。
 */
const STRING_FIELDS = new Set([
  'id', 'kind', 'title', 'sensitivity', 'device', 'source', 'status',
  'source_id', 'folder', 'due', 'created', 'updated',
])

`
if (!s.includes(anchor)) { console.error('ANCHOR NOT FOUND'); process.exit(1) }
s = s.replace(anchor, constBlock + anchor)

// ── 2. 查询层：归一化所有文本字段 ───────────────────────────
const oldItem = `          items.push({
            id, title: meta.title || '(无标题)', kind: meta.kind || 'note',
            tags: Array.isArray(meta.tags) ? meta.tags : [],`
const newItem = `          items.push({
            // String() 归一：历史脏数据里 title 可能是数字，这里兜底
            id, title: String(meta.title ?? '(无标题)'), kind: String(meta.kind || 'note'),
            tags: Array.isArray(meta.tags) ? meta.tags.map(t => String(t)) : [],`

if (!s.includes(oldItem)) { console.error('ITEM BLOCK NOT FOUND'); process.exit(1) }
s = s.replace(oldItem, newItem)

// get() 返回值也归一
const oldGet = `    return {
      id, meta, body, links, tags,`
const newGet = `    // meta 里的字符串字段统一归一，防止数字标题流到 UI 或工具输出
    const safeMeta = { ...meta }
    for (const k of STRING_FIELDS) {
      if (safeMeta[k] !== undefined && safeMeta[k] !== null && typeof safeMeta[k] !== 'string') {
        safeMeta[k] = String(safeMeta[k])
      }
    }
    return {
      id, meta: safeMeta, body, links, tags,`

if (!s.includes(oldGet)) { console.error('GET BLOCK NOT FOUND'); process.exit(1) }
s = s.replace(oldGet, newGet)

// 搜索过滤也做防御
const oldFilter = `      out = out.filter(i => (i.title || '').toLowerCase().includes(k) || i.tags.some(t => String(t).toLowerCase().includes(k)))`
const newFilter = `      out = out.filter(i => String(i.title || '').toLowerCase().includes(k) || i.tags.some(t => String(t).toLowerCase().includes(k)))`

if (!s.includes(oldFilter)) { console.error('FILTER BLOCK NOT FOUND'); process.exit(1) }
s = s.replace(oldFilter, newFilter)

fs.writeFileSync(p, s, 'utf8')
console.log('patched: STRING_FIELDS + 查询层归一化')
