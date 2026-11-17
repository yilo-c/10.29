// SIF × DSH 适配器：把 vault 能力注册成 DSH 工具
//
// 为什么用静态 import 而不是在 apply 里 await：
//   用户插件加载器用动态 import() 且不 await apply()。
//   如果 apply 里异步注册，会出现「日志显示已加载，但工具还没注册完」的竞态。
//   静态 import 在模块求值阶段就完成解析，apply 时可以直接同步注册。
//
// 为什么 import 绝对路径：
//   /sdcard/思忆集/plugins 不在 Node 模块解析路径上，无法用裸包名。
//   实测绝对路径静态 import 在 Android Node 24 可用。
//
// 为什么复用 sif 的 VaultService 而不是重写：
//   HTTP（siyi-vault.js）和 AI 工具必须是同一个写入实现，
//   否则两条路径会产生不同的 frontmatter、不同的路径校验、不同的原子写行为。

import { createVaultService } from '/storage/emulated/0/思忆集/app/sif/core/vault/service.mjs'
import { vaultCapabilities } from '/storage/emulated/0/思忆集/app/sif/core/vault/capabilities.mjs'

export const name = 'siyi-vault-tools'
export const inject = ['tools']

const VAULT_ROOT = '/storage/emulated/0/思忆集/vault'

function log(msg) {
  try { process.stderr.write('[siyi-vault-tools] ' + msg + '\n') } catch {}
}

/**
 * 把中立的 inputSchema（标准 JSON Schema，required 是数组）
 * 转成 DSH 方言（required 写在字段内部）。
 *
 * 这一步是适配器存在的意义：中立契约不迁就任何宿主方言。
 */
// DSH value schema DSL 的允许键（见 dsh-tools/lib/types/schema.js assertAuthorKeys）。
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
}

/** 中立 outputSchema → DSH output.schema（同样把 required 数组改成字段级）。 */
function toDshOutputSchema(schema) {
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
}

/** 把能力返回值渲染成模型可读文本（纯函数，禁 I/O）。 */
function renderValue(toolName, value) {
  try {
    if (toolName === 'vault.search') {
      const items = Array.isArray(value.items) ? value.items : []
      if (items.length === 0) return [{ type: 'text', text: '没有匹配的笔记（共 0 篇）' }]
      const lines = items.slice(0, 50).map(it =>
        `- ${it.title} [${it.kind}] id=${it.id}` +
        (it.folder ? ` folder=${it.folder}` : '') +
        (it.due ? ` due=${it.due}` : ''))
      const more = items.length > 50 ? `\n…还有 ${items.length - 50} 篇，请缩小查询范围或调大 limit` : ''
      return [{ type: 'text', text: `共 ${value.total} 篇，显示前 ${Math.min(items.length, 50)} 篇：\n` + lines.join('\n') + more }]
    }
    if (toolName === 'vault.get') {
      const head = `# ${value.title || '(无标题)'}\nid: ${value.id}\nrevision: ${value.revision}`
      const tags = Array.isArray(value.tags) && value.tags.length ? `\ntags: ${value.tags.join(', ')}` : ''
      const links = Array.isArray(value.links) && value.links.length ? `\n双链: ${value.links.join(', ')}` : ''
      return [{ type: 'text', text: head + tags + links + '\n\n' + (value.body || '') }]
    }
    if (toolName === 'vault.stats') {
      const kinds = Object.entries(value.byKind || {}).map(([k, n]) => `${k}=${n}`).join(' ')
      const status = Object.entries(value.byStatus || {}).map(([k, n]) => `${k}=${n}`).join(' ')
      return [{ type: 'text', text: `共 ${value.total} 篇\n类型: ${kinds}\n状态: ${status}` }]
    }
    return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
  } catch (e) {
    return [{ type: 'text', text: '[render failed] ' + (e && e.message) }]
  }
}

export function apply(ctx, config) {
  const defineTool = config && config.defineTool
  if (typeof defineTool !== 'function') {
    log('warn: config.defineTool 不可用，vault 工具未注册')
    return
  }

  let vault, caps
  try {
    vault = createVaultService({ root: VAULT_ROOT })
    caps = vaultCapabilities(vault)
  } catch (e) {
    log('warn: VaultService 初始化失败，工具未注册: ' + (e && e.message))
    return
  }

  const names = Object.keys(caps)
  let registered = 0

  for (const toolName of names) {
    const impl = caps[toolName]
    const d = impl.descriptor
    try {
      ctx.tools.register(defineTool({
        name: toolName.replace(/\./g, '_'),   // DSH 工具名不允许点号
        description: (d.description || d.title || toolName) +
          `\n[risk: ${d.risk}; idempotent: ${d.idempotent === true}]`,
        parameters: toDshParameters(d.inputSchema),
        output: {
          schema: toDshOutputSchema(d.outputSchema),
          // DSH 宿主的 render 签名是 (args, value)，不是 (value)。
          // 首参是本次调用的入参，第二参才是 execute 的返回值。
          // 2026-11-18 修复：原先写成 (value) => ...，模型看到的永远是
          // 入参当结果的渲染（搜索恒 0 篇、读取标题为空、revision undefined）。
          render: (_args, value) => renderValue(toolName, value),
        },
        execute: async (args) => impl.execute(args || {}, {}),
      }))
      registered++
    } catch (e) {
      log('warn: 注册 ' + toolName + ' 失败: ' + (e && e.message))
    }
  }

  log('已注册 ' + registered + '/' + names.length + ' 个 vault 工具: ' + names.join(', '))
}
