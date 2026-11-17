// 修复运行中 siyi-vault.js 的同类 title 类型 bug（幂等）
//
// 现象：笔记面板搜索会崩 (i.title || '').toLowerCase is not a function
// 根因与 core 相同：parseMd 把 title: 1 解析成数字。
//
// 修两处：
//   1. parseMd 对字符串语义字段不做 Number 转换
//   2. 搜索过滤用 String() 兜底

import fs from 'node:fs'
const TARGET = '/storage/emulated/0/思忆集/plugins/siyi-vault.js'
const MARK = 'SIYI-TITLEFIX-20261118'

let s = fs.readFileSync(TARGET, 'utf8')
if (s.includes(MARK)) { console.log('SKIP: 已打过补丁'); process.exit(0) }

// ── 1. parseMd 里加字符串字段白名单 ────────────────────────
const oldParse = `    if (/^-?\\d+(\\.\\d+)?$/.test(v)) { meta[k] = Number(v); continue }
    meta[k] = v.replace(/^["']|["']$/g, '')`

const newParse = `    // ${MARK}：字符串语义字段不转数字。
    // 真实 vault 里有 title: 1 的笔记，转成数字会让下游 .toLowerCase() 崩溃。
    if (!STRING_META_FIELDS.has(k) && /^-?\\d+(\\.\\d+)?$/.test(v)) { meta[k] = Number(v); continue }
    meta[k] = v.replace(/^["']|["']$/g, '')`

if (!s.includes(oldParse)) { console.error('PARSE BLOCK NOT FOUND'); process.exit(1) }
s = s.replace(oldParse, newParse)

// 常量插在 parseMd 之前
const anchor = '/** 解析 frontmatter。返回 { meta, body }；没有 frontmatter 时 meta 为空对象。 */'
const constBlock = `/**
 * ${MARK}：语义上必须是字符串的 frontmatter 字段。
 * title: 1 在 YAML 里是数字，但笔记标题永远是文本。
 */
const STRING_META_FIELDS = new Set([
  'id', 'kind', 'title', 'sensitivity', 'device', 'source', 'status',
  'source_id', 'folder', 'due', 'created', 'updated',
])

`
if (!s.includes(anchor)) { console.error('ANCHOR NOT FOUND'); process.exit(1) }
s = s.replace(anchor, constBlock + anchor)

// ── 2. 搜索过滤加 String() 兜底 ───────────────────────────
const oldFilter = `      (i.title || '').toLowerCase().includes(k) ||
      i.tags.some(t => t.toLowerCase().includes(k)))`
const newFilter = `      String(i.title || '').toLowerCase().includes(k) ||
      i.tags.some(t => String(t).toLowerCase().includes(k)))`

if (!s.includes(oldFilter)) { console.error('FILTER BLOCK NOT FOUND'); process.exit(1) }
s = s.replace(oldFilter, newFilter)

// ── 3. handleList 返回的 title 归一 ───────────────────────
const oldList = `    title: n.meta.title || '(无标题)',`
const newList = `    title: String(n.meta.title ?? '(无标题)'),`

if (!s.includes(oldList)) { console.error('LIST TITLE NOT FOUND'); process.exit(1) }
s = s.replace(oldList, newList)

fs.writeFileSync(TARGET, s, 'utf8')
console.log('PATCHED: ' + TARGET)
