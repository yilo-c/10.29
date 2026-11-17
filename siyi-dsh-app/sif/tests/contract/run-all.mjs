// 统一测试入口：跑全部合同测试并输出汇总
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const suites = ['codec.test.mjs', 'vault.test.mjs', 'bridge.test.mjs', 'dsh-adapter.test.mjs', 'dsh-schema-dsl.test.mjs', 'title-type.test.mjs', 'vault-js-title.test.mjs', 'http-hardening.test.mjs', 'indexer.test.mjs', 'plugin-indexer.test.mjs', 'dist-restore.test.mjs', 'dist-restore-drill.mjs', 'paths.test.mjs']

let failed = 0
const summary = []
for (const suite of suites) {
  const r = spawnSync(process.execPath, [path.join(here, suite)], { encoding: 'utf8' })
  const out = (r.stdout || '') + (r.stderr || '')
  const m = out.match(/TOTAL (\d+)\s+PASS (\d+)\s+FAIL (\d+)/)
  if (m) {
    summary.push(`${suite.padEnd(20)} total=${m[1]} pass=${m[2]} fail=${m[3]}`)
    if (Number(m[3]) > 0 || r.status !== 0) failed++
  } else {
    summary.push(`${suite.padEnd(20)} NO SUMMARY (exit ${r.status})`)
    failed++
  }
}
console.log(summary.join('\n'))
console.log('\n' + (failed === 0 ? 'ALL SUITES PASS' : `${failed} SUITE(S) FAILED`))
process.exit(failed === 0 ? 0 : 1)
