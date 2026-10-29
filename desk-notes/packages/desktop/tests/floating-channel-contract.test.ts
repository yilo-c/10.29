import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  BODY_FRAME_TYPES,
  EXECUTION_FRAME_TYPES,
  applyFrameToMessages,
  isBodyFrame,
  isExecutionFrame,
} from '../src/FloatingBubble/frameRouting'
import {
  parsePersistedActiveConversationId,
  resolveSharedSessionId,
  sharedAppendStart,
} from '../src/FloatingBubble/sessionShare'
import { resolveWaitingStage } from '../src/components/chat/useWaitingStage'

/**
 * 悬浮助手通道契约（F-P0，2026-09-17）——防静默失效的机器门禁。
 *
 * 由来（三处截断，用户 09-17 拍板「悬浮助手 = chat 的另一形态 = 全功能管理面」）：
 *   T-1 执行全盲：frameRouting 只放行 chunk/error，tool_progress/tool_result 被丢弃
 *        → 内核真跑了工具，用户看不见。
 *   T-2 管理链死锁：悬浮窗无 R4 审批 UI → 从悬浮窗发起的高危操作永远 pending。
 *   T-3 记忆割裂：chat.send 不带 session_id → 悬浮窗对话与主窗不共享。
 *
 * 为什么用「静态源码扫描 + 纯函数行为断言」而不是渲染测试：
 * 沿用 tests/module-route-coverage.test.ts 的判据（17ecc90：隐藏导航 ≠ 删路由）。
 * FloatingChatApp/ChatPanel 挂了一堆 Electron IPC、WS、zustand persist，渲染测试要 mock
 * 一整个运行时；而这三类缺陷的**共同形态是「白名单/接线被悄悄摘掉，没有任何测试会红」**
 * ——静态扫描零 mock，且白名单加一项忘加一行接线立刻红。
 *
 * 核心不变式：**白名单加一项，本文件必须加一项断言**；**声明了白名单但没人消费 = 静默失效**。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const DESKTOP_SRC = path.join(PKG, 'src')
const SHARED_SRC = path.resolve(PKG, '..', 'shared', 'src')
const FLOAT_DIR = path.join(DESKTOP_SRC, 'FloatingBubble')
const FRAME_ROUTING = path.join(FLOAT_DIR, 'frameRouting.ts')
const FLOATING_APP = path.join(FLOAT_DIR, 'FloatingChatApp.tsx')
const CHAT_PANEL = path.join(FLOAT_DIR, 'ChatPanel.tsx')
const STREAM_TYPES = path.join(SHARED_SRC, 'ws-client', 'types.ts')

const read = (p: string) => fs.readFileSync(p, 'utf8')

/**
 * 去掉注释后的源码（保留代码）。
 * 必要性：本批次在 FloatingChatApp/ChatPanel 里写了大量解释性注释，其中会**提到**
 * `confirmation.resolve`、`session_id` 等字样；不剥注释的话，「必须带 session_id」
 * 这类断言会被注释里的同名字符串假绿，「禁止调用 confirmation.resolve」会被注释假红。
 * 简单状态机（逐字符跟踪引号）足够，不引入解析器依赖。
 */
function stripComments(src: string): string {
  let out = ''
  let quote: string | null = null
  for (let i = 0; i < src.length; i++) {
    const ch = src[i]
    const next = src[i + 1]
    if (quote) {
      out += ch
      if (ch === '\\') { out += next ?? ''; i++ } else if (ch === quote) quote = null
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { quote = ch; out += ch; continue }
    if (ch === '/' && next === '*') {
      const end = src.indexOf('*/', i + 2)
      i = end === -1 ? src.length : end + 1
      continue
    }
    if (ch === '/' && next === '/') {
      const end = src.indexOf('\n', i)
      i = end === -1 ? src.length : end - 1
      continue
    }
    out += ch
  }
  return out
}

/** 取 needle 之后 offset 个字符（用于「某调用点的参数块」这类原子断言）。 */
function after(src: string, needle: string, len: number): string {
  const at = src.indexOf(needle)
  expect(at, `源码里找不到调用点：${needle}`).toBeGreaterThanOrEqual(0)
  return src.slice(at, at + len)
}

/** `shared/ws-client/types.ts` 里 StreamChunkType 的真实联合成员（唯一真源）。 */
function streamChunkTypes(): Set<string> {
  const src = read(STREAM_TYPES)
  const body = src.slice(src.indexOf('export type StreamChunkType'))
  const block = body.slice(0, body.indexOf('\n\n'))
  return new Set([...block.matchAll(/'([^']+)'/g)].map((m) => m[1]))
}

/** 递归收集目录下非测试源码。 */
function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (['node_modules', '__tests__', 'dist', 'backup_20260518_083052', 'experimental'].includes(entry.name)) continue
      walk(full, out)
    } else if (/\.(ts|tsx)$/.test(entry.name) && !/\.test\.(ts|tsx)$/.test(entry.name)) {
      out.push(full)
    }
  }
  return out
}

// ─────────────────────────────────────────────────────────────────────────────
describe('T-1 帧放行：内核工具帧必须进执行流，且不得污染正文', () => {
  it('白名单本身就是契约：tool_progress / tool_result 必须被放行', () => {
    expect([...EXECUTION_FRAME_TYPES]).toEqual(['tool_progress', 'tool_result'])
  })

  it('正文白名单固定为 chunk/error，两个白名单不相交', () => {
    expect([...BODY_FRAME_TYPES]).toEqual(['chunk', 'error'])
    const overlap = BODY_FRAME_TYPES.filter((t) => (EXECUTION_FRAME_TYPES as readonly string[]).includes(t))
    expect(overlap, '正文白名单与执行流白名单相交 → 同一帧会既进正文又进执行流').toEqual([])
  })

  it('白名单里的帧类型都是 shared StreamChunkType 的真实成员（防枚举名凭猜）', () => {
    const real = streamChunkTypes()
    expect(real.size, 'streamChunkTypes() 解析失败').toBeGreaterThan(5)
    const unknown = [...BODY_FRAME_TYPES, ...EXECUTION_FRAME_TYPES].filter((t) => !real.has(t))
    expect(unknown, `以下帧类型不在 shared/ws-client/types.ts 的 StreamChunkType 里：${unknown.join(', ')}`).toEqual([])
  })

  it('行为：tool 帧不写正文但被判为执行流帧（放行 = 去状态区，不是去正文）', () => {
    for (const type of EXECUTION_FRAME_TYPES) {
      const msgs = [{ role: 'assistant', content: '已有正文' }]
      expect(applyFrameToMessages(msgs, { type, data: '{"tool":"siyi_note_delete"}' })).toBe(msgs)
      expect(isExecutionFrame({ type, data: '{"tool":"x"}' })).toBe(true)
      expect(isBodyFrame({ type, data: '{"tool":"x"}' })).toBe(false)
    }
  })

  it('行为：缺 data 的 tool 帧不算执行流帧（与 applyFrameToMessages 的入参口径一致）', () => {
    expect(isExecutionFrame({ type: 'tool_progress', data: '' })).toBe(false)
    expect(isExecutionFrame(null)).toBe(false)
  })

  it('接线：FloatingChatApp 真的把执行流帧喂给了状态区（不是只声明白名单）', () => {
    const src = stripComments(read(FLOATING_APP))
    expect(src, 'FloatingChatApp 未消费 isExecutionFrame —— 白名单声明了但没人用 = 执行仍然全盲')
      .toContain('isExecutionFrame(')
    expect(after(src, 'isExecutionFrame(frame)', 160), '执行流帧未进 streamEvents 状态')
      .toContain('setStreamEvents')
  })

  it('接线：ChatPanel 复用主窗 U1 的等待状态（不得自造一套文案表）', () => {
    const src = stripComments(read(CHAT_PANEL))
    expect(src).toContain("from '../components/chat/WaitingStatus'")
    expect(src).toContain('useWaitingStage(')
    expect(src).toContain('<WaitingStatus')
    // 自造一套 = 出现「正在分析…」这类硬编码阶段文案（主窗真源在 WAITING_STAGE_TEXT）
    expect(src, 'ChatPanel 自造了等待文案表，应复用 WAITING_STAGE_TEXT').not.toMatch(/正在分析|正在检索笔记/)
  })

  it('等待阶段：tool_progress 帧 → tooling（内核跑工具时不得回落「正在分析」）', () => {
    const r = resolveWaitingStage([{ type: 'tool_progress', data: '{"tool":"siyi_note_delete"}' }], 120)
    expect(r.stage).toBe('tooling')
  })
})

// ─────────────────────────────────────────────────────────────────────────────
describe('T-2 R4 审批：悬浮窗必须接 sandbox 审批队列，且不得混用 confirmation 队列', () => {
  it('接线：FloatingChatApp 挂载 useSandboxApprovals 并把 approve/reject 传下去', () => {
    const src = stripComments(read(FLOATING_APP))
    expect(src, '悬浮窗未接审批数据层 → 从悬浮窗发起的高危操作永远 pending（管理链死锁）')
      .toContain('useSandboxApprovals(')
    expect(src).toContain('pendingApprovals={approvals.pending}')
    expect(src).toContain('onApproveApproval={approvals.approve}')
    expect(src).toContain('onRejectApproval={approvals.reject}')
  })

  it('接线：ChatPanel 内嵌 ApprovalCard（就地审批条，复用主窗同一张卡）', () => {
    const src = stripComments(read(CHAT_PANEL))
    expect(src).toContain("from '../components/chat/ApprovalCard'")
    expect(src).toContain('<ApprovalCard')
    expect(src).toContain('data-testid="floating-approval-bar"')
  })

  it('契约：R4 走 sandbox.approve {pending_id}（真源在 useSandboxApprovals）', () => {
    const hook = stripComments(read(path.join(DESKTOP_SRC, 'hooks', 'useSandboxApprovals.ts')))
    expect(hook).toContain("'sandbox.approve'")
    expect(hook).toContain('pending_id: pendingId')
  })

  // 双队列隔离（F-P1-1 更新）：FloatingBubble/ 目录里承载的是**队列1（R4 sandbox）**的
  // 就地审批链（ChatPanel 的 floating-approval-bar），它必须只走 sandbox.approve；
  // 队列2（butler CONFIRM，F-P0-2 → 已由 F-P1-1 兑现）走 confirmation.resolve，
  // 其调用点收敛在 hooks/useConfirmApprovals + components/chat/ConfirmationCard
  // （不在 FLOAT_DIR 内，由 floating-confirm-contract.test.ts 锁死）。
  // 本断言 = 队列1 那一侧不得被混入队列2 的 resolve（跨队列 → expired_or_not_found 假象）。
  it('隔离：队列1（FloatingBubble sandbox 链）不得混入 confirmation.resolve', () => {
    const offenders = walk(FLOAT_DIR)
      .filter((f) => stripComments(read(f)).includes('confirmation.resolve'))
      .map((f) => path.relative(PKG, f).replace(/\\/g, '/'))
    expect(
      offenders,
      'FloatingBubble/ 是队列1（sandbox.approve {pending_id}）的地盘，不得混入 confirmation.resolve\n' +
        '（TTL 300s + 必带 reason 的另一套队列，跨用 → expired_or_not_found 假象）：\n' +
        `${offenders.join('\n')}\n` +
        '队列2 的 confirmation.resolve 链在 hooks/useConfirmApprovals + components/chat/ConfirmationCard，见 floating-confirm-contract.test.ts。',
    ).toEqual([])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
describe('T-3 会话共享：chat.* 必须携带与主窗同一 session_id', () => {
  it('接线：chat.send 带 session_id（否则悬浮窗对话与主窗不共享）', () => {
    const src = stripComments(read(FLOATING_APP))
    expect(
      after(src, "streamTyped('chat.send'", 240),
      'chat.send 未带 session_id —— 记忆割裂（T-3）复发',
    ).toContain('session_id')
  })

  it('接线：chat.save_messages / chat.get_conversation 也带 session_id', () => {
    const src = stripComments(read(FLOATING_APP))
    expect(after(src, "'chat.save_messages'", 200)).toContain('session_id')
    expect(after(src, "'chat.get_conversation'", 200)).toContain('session_id')
  })

  it('回归：持久化路径不得再硬编码悬浮窗专属会话 id（隔离源）', () => {
    const src = stripComments(read(FLOATING_APP))
    for (const call of ["streamTyped('chat.send'", "'chat.save_messages'", "'chat.get_conversation'"]) {
      expect(after(src, call, 200), `${call} 又用回了 FLOATING_CONV_ID`).not.toContain('FLOATING_CONV_ID')
    }
  })

  it('共享面读取：sessionShare 从持久化 blob 取主窗活动会话 id', () => {
    expect(readSharedActiveConversationIdSource()).toContain("readFile('store.json')")
  })

  it('接线：回写共享会话前必须过前缀闸（防跨会话串写）', () => {
    const src = stripComments(read(FLOATING_APP))
    expect(src, '回写共享会话未过 sharedAppendStart 闸 → 切会话当帧会跨会话串写').toContain('sharedAppendStart(')
  })

  it('纯函数：resolveSharedSessionId 跟随主窗、无活动会话才回落', () => {
    expect(resolveSharedSessionId('conv-a', '__floating_bubble__')).toBe('conv-a')
    expect(resolveSharedSessionId(null, '__floating_bubble__')).toBe('__floating_bubble__')
    expect(resolveSharedSessionId('   ', '__floating_bubble__')).toBe('__floating_bubble__')
  })

  it('纯函数：parsePersistedActiveConversationId 解 zustand persist 形态且绝不抛', () => {
    expect(parsePersistedActiveConversationId(JSON.stringify({ state: { activeConversationId: 'c1' }, version: 9 }))).toBe('c1')
    // 裸 state 兼容
    expect(parsePersistedActiveConversationId(JSON.stringify({ activeConversationId: 'c2' }))).toBe('c2')
    // 异常输入一律 null，不抛
    expect(parsePersistedActiveConversationId(null)).toBeNull()
    expect(parsePersistedActiveConversationId('')).toBeNull()
    expect(parsePersistedActiveConversationId('{ 这不是 JSON')).toBeNull()
    expect(parsePersistedActiveConversationId(JSON.stringify({ state: { activeConversationId: 42 } }))).toBeNull()
    expect(parsePersistedActiveConversationId(JSON.stringify({ state: {} }))).toBeNull()
  })

  it('回写闸：切会话当帧不得把旧会话消息串写进新会话', () => {
    const convA = [{ role: 'user', content: 'A1' }, { role: 'assistant', content: 'A2' }]
    const convB = [{ role: 'user', content: 'B1' }]
    // 正常续写：本地是共享会话的超集 → 返回续写起点
    expect(sharedAppendStart(convA, [...convA, { role: 'user', content: 'A3' }])).toBe(2)
    // 一致：无新消息 → 起点等于长度（空循环，不写）
    expect(sharedAppendStart(convA, convA)).toBe(2)
    // 切会话当帧：本地还是 A 的消息、目标会话是 B → 必须拦下（-1）
    expect(sharedAppendStart(convB, convA)).toBe(-1)
    // 本地落后于另一窗的写入 → 拦下
    expect(sharedAppendStart(convA, convB)).toBe(-1)
    // 同长度但内容不同（分叉）→ 拦下
    expect(sharedAppendStart([{ role: 'user', content: 'X' }], [{ role: 'user', content: 'Y' }])).toBe(-1)
    // 空共享会话：任何本地消息都算新增
    expect(sharedAppendStart([], convA)).toBe(0)
  })
})

function readSharedActiveConversationIdSource(): string {
  return read(path.join(FLOAT_DIR, 'sessionShare.ts'))
}
