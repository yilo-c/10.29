/**
 * F-P1-1（2026-09-17）悬浮端接通队列2（butler CONFIRM 审批）——契约门禁。
 *
 * 由来（P-C 探针 §3 实测）：后端挂起链完整（action.confirm_required +
 * confirmation.resolve + chat.resume 主窗闭环已证），悬浮端只差「订阅
 * action.confirm_required + 挂 ConfirmationCard + resolve 后 autoResume」
 * （即 ChatPanel 的 TODO(F-P0-2) 那半张工单）。本文件锁死这条接线，防止
 * 再被「悄悄摘掉」而零测试变红（同 floating-channel-contract 的教训 17ecc90）。
 *
 * 双形态（沿用 floating-channel-contract 判据）：
 * - hook 行为：renderHook + dispatchBackendEvent 注入 WS 事件序列，mock
 *   getWsClient 断言 RPC 调用参数 —— 覆盖「卡片出现 / 批准→resolve+resume /
 *   拒绝 / 过期 / mcp:kernel 分支」；
 * - 静态接线：源码扫描断言 FloatingChatApp→ChatPanel→ConfirmationCard 三段
 *   真的接上，且双队列 id 空间不互踩（sandbox 链不得出现 confirmation.resolve，
 *   反之 confirmation 链的 resolve 只经 useConfirmApprovals 一处）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { renderHook, waitFor, act } from '@testing-library/react'
import { clearBackendEventHandlers, dispatchBackendEvent } from '@desk-notes/shared'

import { useConfirmApprovals, isExpiredOutcome } from '../src/hooks/useConfirmApprovals'

const { mockGetWsClient, wsMock } = vi.hoisted(() => ({
  mockGetWsClient: vi.fn(),
  wsMock: {
    status: 'connected',
    call: vi.fn(),
    on: vi.fn(() => () => {}),
    onStatusChange: vi.fn(() => () => {}),
  },
}))
vi.mock('../src/utils/ai', () => ({ getWsClient: () => mockGetWsClient() }))

const ev = (event_type: string, payload: Record<string, unknown>) =>
  ({ type: 'event' as const, event_type, payload }) as never

beforeEach(() => {
  vi.clearAllMocks()
  clearBackendEventHandlers()
  mockGetWsClient.mockReturnValue(wsMock)
  wsMock.status = 'connected'
})
afterEach(() => clearBackendEventHandlers())

const CONFIRM_REQUIRED = {
  confirmation_id: 'c-1',
  action_id: 'shell.run',
  args: { command: 'echo hi' },
  source: 'butler',
}

// ─────────────────────────────────────────────────────────────────────────────
describe('队列2 数据层：action.confirm_required → 卡片可见', () => {
  it('confirm_required 事件进入 pending 队列，字段与后端 payload 对齐', async () => {
    const { result } = renderHook(() => useConfirmApprovals())
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    expect(result.current.pending[0]).toMatchObject({
      confirmation_id: 'c-1', action_id: 'shell.run', source: 'butler', status: 'pending',
    })
  })

  it('去重（主窗同款）：同 (action_id,args) 重复发起只保留最新一条 pending', async () => {
    const { result } = renderHook(() => useConfirmApprovals())
    act(() => {
      dispatchBackendEvent(ev('action.confirm_required', { ...CONFIRM_REQUIRED, confirmation_id: 'old' }) as never)
      dispatchBackendEvent(ev('action.confirm_required', { ...CONFIRM_REQUIRED, confirmation_id: 'new' }) as never)
    })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    expect(result.current.pending[0].confirmation_id).toBe('new')
  })

  it('confirm_resolved 事件把卡片定案为 approved/rejected', async () => {
    const { result } = renderHook(() => useConfirmApprovals())
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    act(() => { dispatchBackendEvent(ev('action.confirm_resolved', { confirmation_id: 'c-1', approved: true }) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(0))
    expect(result.current.items[0].status).toBe('approved')
  })
})

describe('队列2 批准：confirmation.resolve {approved,reason} → 成功后 resume 回调', () => {
  it('批准必带 reason 走 confirmation.resolve（带 confirmation_id/always_allow）', async () => {
    wsMock.call.mockResolvedValue({ success: true, approved: true })
    const onResume = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useConfirmApprovals({ onApprovedResume: onResume }))
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    const res = await result.current.approve('c-1', '需要跑这条命令', false)
    expect(res.ok).toBe(true)
    expect(wsMock.call).toHaveBeenCalledWith('confirmation.resolve', {
      confirmation_id: 'c-1', approved: true, reason: '需要跑这条命令', always_allow: false,
    })
  })

  it('批准成功后回调 onApprovedResume（宿主据此以共享 session_id 走 chat.resume）', async () => {
    wsMock.call.mockResolvedValue({ success: true })
    const onResume = vi.fn().mockResolvedValue(undefined)
    const { result } = renderHook(() => useConfirmApprovals({ onApprovedResume: onResume }))
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    await result.current.approve('c-1', 'ok', false)
    expect(onResume).toHaveBeenCalledWith('butler')
  })

  it('始终允许：always_allow=true 透传后端（B2 名单写入）', async () => {
    wsMock.call.mockResolvedValue({ success: true })
    const { result } = renderHook(() => useConfirmApprovals({ onApprovedResume: vi.fn() }))
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    await result.current.approve('c-1', '长期需要', true)
    expect(wsMock.call).toHaveBeenCalledWith('confirmation.resolve', {
      confirmation_id: 'c-1', approved: true, reason: '长期需要', always_allow: true,
    })
  })

  it('理由必填：空理由直接拦下，不发 RPC（前端闸 + 后端双保险的前端侧）', async () => {
    const { result } = renderHook(() => useConfirmApprovals())
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    const res = await result.current.approve('c-1', '   ', false)
    expect(res.ok).toBe(false)
    expect(wsMock.call).not.toHaveBeenCalled()
  })
})

describe('队列2 拒绝：resolve(approved:false) 且不 resume', () => {
  it('拒绝走 confirmation.resolve {approved:false}，理由可选、不触发续跑', async () => {
    wsMock.call.mockResolvedValue({ success: true, approved: false })
    const onResume = vi.fn()
    const { result } = renderHook(() => useConfirmApprovals({ onApprovedResume: onResume }))
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    const res = await result.current.reject('c-1')
    expect(res.ok).toBe(true)
    expect(wsMock.call).toHaveBeenCalledWith('confirmation.resolve', {
      confirmation_id: 'c-1', approved: false, reason: '', always_allow: false,
    })
    expect(onResume).not.toHaveBeenCalled()
  })
})

describe('队列2 过期：not_found/expired → 卡片转已过期并撤按钮，不死循环', () => {
  it('resolve 返回 confirmation_not_found_or_expired → 本地翻 expired、移出 pending', async () => {
    wsMock.call.mockResolvedValue({ success: false, reason: 'confirmation_not_found_or_expired' })
    const onResume = vi.fn()
    const { result } = renderHook(() => useConfirmApprovals({ onApprovedResume: onResume }))
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    const res = await result.current.approve('c-1', 'x', false)
    expect(res).toMatchObject({ ok: false, expired: true })
    await waitFor(() => expect(result.current.pending).toHaveLength(0))
    expect(result.current.items[0].status).toBe('expired')
    expect(onResume).not.toHaveBeenCalled()
  })

  it('RPC 抛 expired 类异常 → 同样收敛为 expired 态', async () => {
    wsMock.call.mockRejectedValue(new Error('pending 已过期'))
    const { result } = renderHook(() => useConfirmApprovals())
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    const res = await result.current.reject('c-1')
    expect(res.ok).toBe(false)
    await waitFor(() => expect(result.current.items[0].status).toBe('expired'))
  })

  it('过期后迟到的 confirm_resolved 不得复活卡片（本地过期优先）', async () => {
    wsMock.call.mockResolvedValue({ success: false, reason: 'confirmation_not_found_or_expired' })
    const { result } = renderHook(() => useConfirmApprovals())
    act(() => { dispatchBackendEvent(ev('action.confirm_required', CONFIRM_REQUIRED) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    await result.current.approve('c-1', 'x', false)
    await waitFor(() => expect(result.current.items[0].status).toBe('expired'))
    act(() => { dispatchBackendEvent(ev('action.confirm_resolved', { confirmation_id: 'c-1', approved: true }) as never) })
    await new Promise((r) => setTimeout(r, 10))
    expect(result.current.items[0].status).toBe('expired')
  })

  it('纯函数：isExpiredOutcome 覆盖后端英文 marker 与中文兜底', () => {
    expect(isExpiredOutcome('confirmation_not_found_or_expired')).toBe(true)
    expect(isExpiredOutcome('pending_id 不存在或已过期')).toBe(true)
    expect(isExpiredOutcome('approval_reason_required')).toBe(false)
  })
})

describe('队列2 mcp:kernel 分支：批准后不 chat.resume（走宿主重发）', () => {
  it('source=mcp:kernel 时回调透传该 source（宿主走 autoContinue 重发而非 resume）', async () => {
    wsMock.call.mockResolvedValue({ success: true })
    const onResume = vi.fn()
    const { result } = renderHook(() => useConfirmApprovals({ onApprovedResume: onResume }))
    act(() => { dispatchBackendEvent(ev('action.confirm_required', { ...CONFIRM_REQUIRED, source: 'mcp:kernel' }) as never) })
    await waitFor(() => expect(result.current.pending).toHaveLength(1))
    await result.current.approve('c-1', 'x', false)
    expect(onResume).toHaveBeenCalledWith('mcp:kernel')
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// 静态接线 + 双队列隔离（源码扫描，沿用 floating-channel-contract 判据）
const HERE = path.dirname(fileURLToPath(import.meta.url))
const PKG = path.resolve(HERE, '..')
const DESKTOP_SRC = path.join(PKG, 'src')
const FLOAT_DIR = path.join(DESKTOP_SRC, 'FloatingBubble')
const FLOATING_APP = path.join(FLOAT_DIR, 'FloatingChatApp.tsx')
const CHAT_PANEL = path.join(FLOAT_DIR, 'ChatPanel.tsx')
const read = (p: string) => fs.readFileSync(p, 'utf8')

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
    if (ch === '/' && next === '/') { const e = src.indexOf('\n', i); i = e === -1 ? src.length : e - 1; continue }
    out += ch
  }
  return out
}

describe('接线：FloatingChatApp 挂队列2 并把卡片数据/动作传给 ChatPanel', () => {
  it('FloatingChatApp 消费 useConfirmApprovals 且下传 pendingConfirmations', () => {
    const src = stripComments(read(FLOATING_APP))
    expect(src, '悬浮端未订阅 action.confirm_required → 队列2 死锁（看得见批不了）').toContain('useConfirmApprovals(')
    expect(src).toContain('pendingConfirmations={')
    expect(src).toContain('onApproveConfirmation={')
    expect(src).toContain('onRejectConfirmation={')
  })

  it('批准后以共享 session_id 续跑：onApprovedResume 里 sessionIdRef → resumeChatStream', () => {
    const src = stripComments(read(FLOATING_APP))
    expect(src, '队列2 批准后未调 chat.resume（宿主未接续跑）').toContain('resumeChatStream(')
    // 续跑回调体内必须同时出现「取宿主共享 session_id」与「resumeChatStream」，
    // 否则会话割裂（resume 落到错误 session 的 checkpoint）。
    const at = src.indexOf('onApprovedResume')
    expect(at).toBeGreaterThanOrEqual(0)
    const block = src.slice(at, at + 1200)
    expect(block).toContain('sessionIdRef.current')
    expect(block).toContain('resumeChatStream(')
  })

  it('ChatPanel 渲染 ConfirmationCard 并留独立容器 testid', () => {
    const src = stripComments(read(CHAT_PANEL))
    expect(src).toContain("from '../components/chat/ConfirmationCard'")
    expect(src).toContain('<ConfirmationCard')
    expect(src).toContain('data-testid="floating-confirmation-bar"')
  })
})

describe('双队列隔离：sandbox 链与 confirmation 链 id 空间不互踩', () => {
  it('useSandboxApprovals（队列1）源码不得出现 confirmation.resolve', () => {
    const src = stripComments(read(path.join(DESKTOP_SRC, 'hooks', 'useSandboxApprovals.ts')))
    expect(src, '队列1 混入 confirmation.resolve → 跨队列必得 expired_or_not_found').not.toContain('confirmation.resolve')
  })

  it('ConfirmationCard（队列2）源码不得出现 sandbox.approve', () => {
    const src = stripComments(read(path.join(DESKTOP_SRC, 'components', 'chat', 'ConfirmationCard.tsx')))
    expect(src, '队列2 混入 sandbox.approve → 跨队列必得 expired_or_not_found').not.toContain('sandbox.approve')
  })

  it('confirmation.resolve 的调用点收敛在 useConfirmApprovals（唯一真源）', () => {
    const hook = stripComments(read(path.join(DESKTOP_SRC, 'hooks', 'useConfirmApprovals.ts')))
    expect(hook).toContain("'confirmation.resolve'")
    expect(hook).toContain('confirmation_id: confirmationId')
  })
})
