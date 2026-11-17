import fs from 'node:fs'
const p = 'core/vault/service.mjs'
let s = fs.readFileSync(p, 'utf8')

const startMark = "    if (v.startsWith('[') && v.endsWith(']')) {"
const endMark = "    meta[k] = v.replace(/^[\"']|[\"']$/g, '')\n"
const i = s.indexOf(startMark)
const j = s.indexOf(endMark)
if (i < 0 || j < 0) { console.error('MARK NOT FOUND', i, j); process.exit(1) }
const blockEnd = j + endMark.length

const newBlock = [
  "    if (v.startsWith('[') && v.endsWith(']')) {",
  "      meta[k] = parseInlineArray(v)",
  "      continue",
  "    }",
  "    if (v === 'true') { meta[k] = true; continue }",
  "    if (v === 'false') { meta[k] = false; continue }",
  "    if (/^-?\\d+$/.test(v)) { meta[k] = Number(v); continue }",
  "    meta[k] = unquoteScalar(v)",
  ""
].join('\n')

s = s.slice(0, i) + newBlock + s.slice(blockEnd)

const anchor = '/** 解析 frontmatter。只支持扁平标量与内联数组；遇到嵌套结构报错而不是猜。 */'
const helpers = `/**
 * 反转义一个 YAML 标量。
 *
 * 为什么必须做：buildNote 用 JSON.stringify 产生双引号标量（JSON 字符串是
 * 合法 YAML），但解析时若只 strip 掉外层引号，内部转义序列会原样留在值里，
 * 标题就变成「他说\\"你好\\"」而不是「他说"你好"」。
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
    if (ch === '\\\\' && quote === '"') { cur += ch; escaped = true; continue }
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

`
if (!s.includes(anchor)) { console.error('ANCHOR NOT FOUND'); process.exit(1) }
s = s.replace(anchor, helpers + anchor)
fs.writeFileSync(p, s, 'utf8')
console.log('patched OK')
