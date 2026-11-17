// E2：用桩 ctx 验证 DSH 适配器能同步注册工具并正确转换 schema
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

let pass = 0, fail = 0
const lines = []
async function tt(name, fn) {
  try { await fn(); pass++; lines.push('PASS ' + name) }
  catch (e) { fail++; lines.push('FAIL ' + name + ' :: ' + (e && e.message)) }
}
function eq(a, b, label) {
  const x = JSON.stringify(a), y = JSON.stringify(b)
  if (x !== y) throw new Error(`${label}: expected ${y}, got ${x}`)
}

const PLUGIN = '/storage/emulated/0/思忆集/app/sif/plugins/siyi-vault-tools.js'

// 桩 defineTool：记录收到的定义，直接返回定义本身
function makeStub() {
  const tools = new Map()
  const defineTool = (def) => def
  const ctx = {
    tools: {
      register(def) {
        if (!def || typeof def.name !== 'string') throw new Error('tool needs name')
        if (!def.output || !def.output.schema || typeof def.output.render !== 'function') {
          throw new Error('tool must declare output { schema, render }')
        }
        if (tools.has(def.name)) throw new Error('duplicate tool ' + def.name)
        tools.set(def.name, def)
        return () => tools.delete(def.name)
      },
    },
  }
  return { ctx, tools, defineTool }
}

const mod = await import('file://' + PLUGIN + '?t=' + Date.now())

await tt('插件导出 name 与 inject', async () => {
  eq(mod.name, 'siyi-vault-tools', 'name')
  eq(mod.inject, ['tools'], 'inject')
})

await tt('apply 同步注册，返回后工具已就绪', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  eq(tools.size, 6, 'count')
})

await tt('工具名不含点号（DSH 要求）', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  for (const name of tools.keys()) {
    if (name.includes('.')) throw new Error('tool name has dot: ' + name)
  }
  eq(tools.has('vault_get'), true, 'vault_get')
  eq(tools.has('vault_save'), true, 'vault_save')
})

await tt('parameters 用 DSH 方言（required 在字段内）', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  const get = tools.get('vault_get')
  eq(Array.isArray(get.parameters.required), false, 'no-required-array')
  eq(get.parameters.id.required, true, 'field-required')
})

await tt('output.schema 与 render 齐备', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  for (const [name, def] of tools) {
    eq(typeof def.output.render, 'function', name + '.render')
    eq(typeof def.output.schema, 'object', name + '.schema')
  }
})

await tt('description 带风险标注', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  eq(tools.get('vault_save').description.includes('risk: write'), true, 'write-risk')
  eq(tools.get('vault_get').description.includes('risk: read'), true, 'read-risk')
})

await tt('缺 defineTool 时安静跳过不抛错', async () => {
  const { ctx } = makeStub()
  mod.apply(ctx, {})
  // 没抛错即通过
})

await tt('render 是纯函数：search 输出可读列表', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  // 真实宿主 dsh-tools 的调用约定是 render(args, value)：首参是入参，
  // 第二参才是 execute 的返回值。这里必须按同样的签名调用，否则会漏掉
  // 「render 把入参当结果」这类 bug（2026-11-18 真实引擎上踩到）。
  const out = tools.get('vault_search').output.render({ q: '知识' }, {
    total: 2,
    items: [
      { title: '笔记A', kind: 'note', id: '01M7NQXA5C3T702MJ24X791C2T', folder: '知识', due: null },
      { title: '笔记B', kind: 'task', id: '01M7NQXA5C3T702MJ24X791C2D', folder: null, due: '2026-11-20' },
    ],
  })
  eq(Array.isArray(out), true, 'array')
  eq(out[0].type, 'text', 'type')
  eq(out[0].text.includes('笔记A'), true, 'contains')
  eq(out[0].text.includes('共 2 篇'), true, 'total')
})

await tt('render 处理空搜索结果', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  const out = tools.get('vault_search').output.render({ q: '不存在的词' }, { total: 0, items: [] })
  eq(out[0].text.includes('没有匹配'), true, 'empty')
})

await tt('真实调用 vault_get 读到正式 vault 笔记', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  // 先搜一篇
  const search = await tools.get('vault_search').execute({ limit: 1 })
  if (!search.items || search.items.length === 0) throw new Error('vault 为空，无法验证')
  const id = search.items[0].id
  const note = await tools.get('vault_get').execute({ id })
  eq(note.id, id, 'id-match')
  eq(typeof note.revision, 'string', 'revision')
  eq(note.revision.length, 64, 'revision-len')
})

await tt('真实调用 vault_stats 返回统计', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  const st = await tools.get('vault_stats').execute({})
  eq(typeof st.total, 'number', 'total')
  eq(st.total > 0, true, 'nonempty')
})

await tt('工具层拒绝非法 id（路径安全穿透到工具面）', async () => {
  const { ctx, tools, defineTool } = makeStub()
  mod.apply(ctx, { defineTool })
  let threw = false
  try { await tools.get('vault_get').execute({ id: '../../etc/passwd' }) }
  catch (e) { threw = true; eq(e.code, 'INVALID_ARGUMENT', 'code') }
  eq(threw, true, 'threw')
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
