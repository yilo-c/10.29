// E3：用真实 dsh-tools 的 defineTool 校验 schema，确保能被宿主接受
//
// 为什么必须用真实验证器：上一轮 6 个工具只注册成功 3 个，
// 就是因为 pattern 关键字被 DSL 拒绝，而桩测试发现不了这个问题。
import { createRequire } from 'node:module'
import fs from 'node:fs'

const require = createRequire('/data/data/com.siyi.dshapp/files/dsh-home/profiles/web/node_modules/')
let defineTool
try {
  defineTool = require('@deepseek-ai/dsh-tools').defineTool
} catch (e) {
  console.error('无法加载 dsh-tools: ' + e.message)
  process.exit(2)
}
console.log('defineTool 加载成功')

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
const mod = await import('file://' + PLUGIN + '?t=' + Date.now())

function makeStub() {
  const tools = new Map()
  const ctx = {
    tools: {
      register(def) {
        if (!def || typeof def.name !== 'string') throw new Error('tool needs name')
        if (!def.output || !def.output.schema || typeof def.output.render !== 'function') {
          throw new Error('tool must declare output { schema, render }')
        }
        tools.set(def.name, def)
        return () => tools.delete(def.name)
      },
    },
  }
  return { ctx, tools }
}

await tt('全部 6 个工具能被真实 defineTool 接受', async () => {
  const { ctx, tools } = makeStub()
  mod.apply(ctx, { defineTool })
  eq(tools.size, 6, 'registered-count')
})

await tt('每个工具都通过 defineTool 的 schema 编译', async () => {
  const { ctx, tools } = makeStub()
  mod.apply(ctx, { defineTool })
  for (const [name, def] of tools) {
    // defineTool 内部会编译 schema；能注册成功即通过
    if (!def.parameters || typeof def.parameters !== 'object') {
      throw new Error(name + ' parameters 不是对象')
    }
  }
})

await tt('剥离 pattern 后不再抛 DSL 错误', async () => {
  const { ctx, tools } = makeStub()
  mod.apply(ctx, { defineTool })
  const get = tools.get('vault_get')
  // defineTool 会把 parameters 重新编译成标准 JSON Schema：
  // 顶层变成 { type: object, properties, required: [...] }
  eq(get.parameters.type, 'object', 'top-type')
  eq(get.parameters.properties.id.type, 'string', 'id-type')
  eq(Object.hasOwn(get.parameters.properties.id, 'pattern'), false, 'pattern-stripped')
})

await tt('required 正确转成标准数组', async () => {
  const { ctx, tools } = makeStub()
  mod.apply(ctx, { defineTool })
  eq(tools.get('vault_get').parameters.required, ['id'], 'id-required')
  const searchReq = tools.get('vault_search').parameters.required || []
  eq(searchReq.includes('q'), false, 'q-optional')
})

await tt('数组字段结构正确', async () => {
  const { ctx, tools } = makeStub()
  mod.apply(ctx, { defineTool })
  const tags = tools.get('vault_save').parameters.properties.tags
  eq(tags.type, 'array', 'array-type')
  eq(tags.items.type, 'string', 'item-type')
})

await tt('工具描述含风险标注', async () => {
  const { ctx, tools } = makeStub()
  mod.apply(ctx, { defineTool })
  eq(tools.get('vault_save').description.includes('risk: write'), true, 'write')
  eq(tools.get('vault_get').description.includes('risk: read'), true, 'read')
})

await tt('真实调用仍可用（schema 裁剪不影响执行）', async () => {
  const { ctx, tools } = makeStub()
  mod.apply(ctx, { defineTool })
  const st = await tools.get('vault_stats').execute({})
  eq(typeof st.total, 'number', 'total')
  eq(st.total > 0, true, 'nonempty')
})

await tt('业务层仍拒绝非法 id（pattern 剥离后靠 execute 校验）', async () => {
  const { ctx, tools } = makeStub()
  mod.apply(ctx, { defineTool })
  let threw = false
  try { await tools.get('vault_get').execute({ id: '../../../etc/passwd' }) }
  catch (e) { threw = true; eq(e.code, 'INVALID_ARGUMENT', 'code') }
  eq(threw, true, 'threw')
})

console.log(lines.join('\n'))
console.log(`\nTOTAL ${pass + fail}  PASS ${pass}  FAIL ${fail}`)
process.exit(fail === 0 ? 0 : 1)
