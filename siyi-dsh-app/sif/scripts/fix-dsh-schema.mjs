// 修复：DSH value schema DSL 不支持 pattern/minimum/maximum/format 等关键字
//
// 根因：dsh-tools/lib/types/schema.js 的 assertAuthorKeys 有严格白名单：
//   string  → type/enum/const + description/title/default/examples
//   integer → 同上（无 minimum/maximum）
//   object  → type/properties/additionalProperties
// 出现白名单外的键就抛 "not supported by the value schema DSL"，
// 该工具注册失败（实测 6 个只注册成功 3 个）。
//
// 正确做法：中立契约保留 pattern 等约束（它是跨宿主的能力描述），
// 适配器在投影到 DSH 时剥离这些键，并把校验职责交给 execute 里的业务代码。
// 这样既不污染核心契约，也不放弃运行时校验。

import fs from 'node:fs'
const p = 'plugins/siyi-vault-tools.js'
let s = fs.readFileSync(p, 'utf8')

const oldFn = `function toDshParameters(schema) {
  if (!schema || schema.type !== 'object') return {}
  const required = Array.isArray(schema.required) ? new Set(schema.required) : new Set()
  const out = {}
  for (const [key, prop] of Object.entries(schema.properties || {})) {
    const field = { ...prop }
    if (field.type === 'array' && field.items) {
      field.items = toDshParameters({ type: 'object', properties: field.items.properties || {}, required: field.items.required })
    }
    if (required.has(key)) field.required = true
    out[key] = field
  }
  return out
}`

const newFn = `// DSH value schema DSL 的允许键（见 dsh-tools/lib/types/schema.js assertAuthorKeys）。
// 不在这些集合里的键会让整个工具注册失败，必须剥离。
const DSL_ANNOTATION = ['description', 'title', 'default', 'examples']
const DSL_SCALAR = ['type', 'enum', 'const']
const DSL_OBJECT = ['type', 'properties', 'additionalProperties']
const DSL_ARRAY = ['type', 'items']

/**
 * 把单个字段的 schema 裁剪成 DSH DSL 可接受的形式。
 *
 * 注意：pattern / minimum / maximum / format 会被丢弃。
 * 这不是放弃校验——约束仍在中立契约里，并且 execute 的业务代码
 * （VaultService 的 ULID 检查、revision 长度检查）会真正执行校验。
 * 适配器只负责让 schema 能被宿主接受，不假装它是校验器。
 */
function sanitizeField(prop) {
  if (!prop || typeof prop !== 'object') return { type: 'string' }
  const type = prop.type
  let allowed
  if (type === 'object') allowed = DSL_OBJECT
  else if (type === 'array') allowed = DSL_ARRAY
  else if (type === undefined) allowed = DSL_ANNOTATION
  else allowed = DSL_SCALAR
  const keys = [...allowed, ...DSL_ANNOTATION]

  const out = {}
  for (const k of keys) {
    if (Object.hasOwn(prop, k)) out[k] = prop[k]
  }

  if (out.type === 'object') {
    // DSL 要求 object 必须显式声明 additionalProperties 布尔值
    out.additionalProperties = prop.additionalProperties === false ? false : true
    out.properties = {}
    for (const [k, v] of Object.entries(prop.properties || {})) {
      out.properties[k] = sanitizeField(v)
    }
  }
  if (out.type === 'array' && prop.items) {
    out.items = sanitizeField(prop.items)
  }
  return out
}

function toDshParameters(schema) {
  if (!schema || schema.type !== 'object') return {}
  const required = Array.isArray(schema.required) ? new Set(schema.required) : new Set()
  const out = {}
  for (const [key, prop] of Object.entries(schema.properties || {})) {
    const field = sanitizeField(prop)
    if (required.has(key)) field.required = true
    out[key] = field
  }
  return out
}`

if (!s.includes(oldFn)) { console.error('toDshParameters NOT FOUND'); process.exit(1) }
s = s.replace(oldFn, newFn)

const oldOut = `function toDshOutputSchema(schema) {
  if (!schema || schema.type !== 'object') {
    return { type: 'object', additionalProperties: true, properties: {} }
  }
  const required = Array.isArray(schema.required) ? new Set(schema.required) : new Set()
  const properties = {}
  for (const [key, prop] of Object.entries(schema.properties || {})) {
    const field = { ...prop }
    if (required.has(key)) field.required = true
    properties[key] = field
  }
  return {
    type: 'object',
    additionalProperties: schema.additionalProperties === false ? false : true,
    properties,
  }
}`

const newOut = `function toDshOutputSchema(schema) {
  if (!schema || schema.type !== 'object') {
    return { type: 'object', additionalProperties: true, properties: {} }
  }
  const required = Array.isArray(schema.required) ? new Set(schema.required) : new Set()
  const properties = {}
  for (const [key, prop] of Object.entries(schema.properties || {})) {
    const field = sanitizeField(prop)
    if (required.has(key)) field.required = true
    properties[key] = field
  }
  return {
    type: 'object',
    additionalProperties: schema.additionalProperties === false ? false : true,
    properties,
  }
}`

if (!s.includes(oldOut)) { console.error('toDshOutputSchema NOT FOUND'); process.exit(1) }
s = s.replace(oldOut, newOut)

fs.writeFileSync(p, s, 'utf8')
console.log('patched siyi-vault-tools.js schema sanitizer')
