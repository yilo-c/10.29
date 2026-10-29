/**
 * 批5 单一真源（2026-09-18）：前端「内核能否续跑」一律读 kernel.status 的能力声明。
 *
 * 由来（F-P2 mcp:kernel 两真源矛盾）：后端 `gateway_path.run_gateway_resume` 的
 * docstring 声称"在既有会话上续跑"，前端 `ChatView` 却硬编码"DSH 无法续跑"。
 * 同一事实两处各算一遍且相反 → 本文件锁死"只读一处"：
 *   - 动态：`kernelSupportsResume` / `readKernelCapabilities` 的行为与保守缺省；
 *   - 静态：ChatView 的 mcp:kernel 分支必须经该读取器，且源码中不得再出现
 *     "无法续跑 / no_pending_task" 这类**本地推断能力**的硬编码结论。
 */
import { describe, it, expect, vi } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  KERNEL_STATUS_METHOD,
  kernelSupportsResume,
  readKernelCapabilities,
} from '../src/utils/kernelCapabilities'

const caller = (impl: (m: string, p?: Record<string, unknown>) => unknown) => ({ call: vi.fn(impl) as never })

describe('内核续跑能力读取器（唯一入口）', () => {
  it('resume=true → 支持续跑（数据源 = kernel.status.details.capabilities）', async () => {
    const c = caller(() => ({ details: { capabilities: { resume: true, stream: true } } }))
    await expect(kernelSupportsResume(c)).resolves.toBe(true)
    expect((c.call as unknown as { mock: { calls: unknown[][] } }).mock.calls[0][0]).toBe(KERNEL_STATUS_METHOD)
  })

  it('resume=false → 不支持（DSH / Codex 的默认声明）', async () => {
    await expect(kernelSupportsResume(caller(() => ({ details: { capabilities: { resume: false } } })))).resolves.toBe(false)
  })

  it('缺 capabilities / details → 保守判不支持（不赌一次 chat.resume）', async () => {
    await expect(kernelSupportsResume(caller(() => ({})))).resolves.toBe(false)
    await expect(kernelSupportsResume(caller(() => ({ details: {} })))).resolves.toBe(false)
    await expect(readKernelCapabilities(caller(() => ({})))).resolves.toBeNull()
  })

  it('RPC 抛错 / 无客户端 → 保守判不支持，不向上抛', async () => {
    await expect(kernelSupportsResume(caller(() => { throw new Error('backend down') }))).resolves.toBe(false)
    await expect(kernelSupportsResume(null)).resolves.toBe(false)
    await expect(kernelSupportsResume(undefined)).resolves.toBe(false)
  })
})

// ─────────────────────────────────────────────────────────────────────────────
const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const CHAT_VIEW = path.join(PKG, 'src', 'components', 'chat', 'ChatView.tsx')
const read = (p: string) => fs.readFileSync(p, 'utf8')

/** 去注释：静态判据只认代码，不认注释里的说明文字（沿用 floating-confirm-contract 判据）。 */
function stripComments(src: string): string {
  let out = ''
  let quote: string | null = null
  for (let i = 0; i < src.length; i++) {
    const ch = src[i]; const next = src[i + 1]
    if (quote) {
      out += ch
      if (ch === '\\') { out += next ?? ''; i++ } else if (ch === quote) quote = null
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { quote = ch; out += ch; continue }
    if (ch === '/' && next === '*') { const e = src.indexOf('*/', i + 2); i = e === -1 ? src.length : e + 1; continue }
    if (ch === '/' && next === '/') { const e = src.indexOf('\n', i); i = e === -1 ? src.length : e + 1; continue }
    out += ch
  }
  return out
}

describe('接线：ChatView 的 mcp:kernel 分支改读能力声明（不再本地推断）', () => {
  it('mcp:kernel 分支经 kernelSupportsResume 判定，而不是直接推定不可续跑', () => {
    const src = stripComments(read(CHAT_VIEW))
    expect(src, 'ChatView 未接入能力读取器 → 又变回本地推断').toContain('kernelSupportsResume(')
    const at = src.indexOf('kernelSupportsResume(')
    // 判定必须与 source==='mcp:kernel' 出现在同一处分支逻辑里
    const around = src.slice(Math.max(0, at - 400), at + 200)
    expect(around).toContain("'mcp:kernel'")
  })

  it('源码不得再出现"无法续跑"式硬编码结论（能力判据只此一处）', () => {
    // 注意：本判据刻意扫**原始源码含注释**（不用 stripComments）——本缺陷的
    // 载体正是注释/docstring（旧 ChatView:333 写着"…并返回 no_pending_task，
    // 无法续跑"）。若只扫代码，这类"第二真源"复活时门禁将完全无感。
    const raw = read(CHAT_VIEW)
    expect(raw, '旧硬编码结论复活：请改读 kernel.status.details.capabilities.resume').not.toContain('无法续跑')
    expect(raw).not.toContain('no_pending_task')
  })
})
