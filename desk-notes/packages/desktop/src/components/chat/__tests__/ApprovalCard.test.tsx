/**
 * M2.2 审批双形态：ApprovalCard 渲染与按钮直连 + summarizeApproval 口径。
 *
 * 展示字段照方案裁定：工具名 / 目标路径 / 参数摘要 ≤200 字符（后端
 * mcp_server/desktop.py:50 approval_preview 同档）+ 参数指纹短串（只展示不判断，
 * 指纹绑定在后端 sandbox_manager.py:265 / desktop.py:66 _require_approved）。
 * 按钮直连 sandbox.approve / sandbox.reject（handlers/_sandbox.py:30-31），带 pending_id。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ApprovalCard } from '../ApprovalCard'
import { summarizeApproval, appendHistory, APPROVAL_SUMMARY_MAX, type ApprovalItem, type ApprovalHistoryEntry } from '../../../hooks/useSandboxApprovals'

const rpcItem: ApprovalItem = {
  pending_id: '8242339d84dc',
  action_id: 'notes.delete',
  status: 'pending',
  created_at: 1789227570.49,
  preview: {
    action: 'notes.delete',
    args: { note_id: 'mp30hc39junb' },
    summary: 'RPC 方法 notes.delete 需要用户审批',
    args_sha256: '3819f8a34e1e369a20c25308201c995e24091e14f1722bf0ccd1ea02945454dc',
  },
}

describe('summarizeApproval', () => {
  it('取 action_id 为工具名、args 里的标识为目标、指纹截 12 位', () => {
    const s = summarizeApproval(rpcItem)
    expect(s.tool).toBe('notes.delete')
    expect(s.target).toBe('mp30hc39junb')
    expect(s.summary).toContain('notes.delete')
    expect(s.fingerprint).toBe('3819f8a34e1e')
  })

  it('参数摘要硬截 ≤200 字符且单行化（防注入换行伪造多行）', () => {
    const s = summarizeApproval({
      pending_id: 'p',
      action_id: 'file.write',
      preview: { args: { path: 'C:\\tmp\\a.txt', content: `第一行\n第二行 ${'x'.repeat(400)}` } },
    })
    expect(s.summary.length).toBeLessThanOrEqual(APPROVAL_SUMMARY_MAX)
    expect(s.summary).not.toContain('\n')
    expect(s.target).toBe('C:\\tmp\\a.txt')
  })
})

describe('appendHistory', () => {
  const at = (n: number): ApprovalHistoryEntry => ({
    pending_id: `p${n}`, action_id: 'notes.delete', decision: 'approved', at: n, summary: '',
  })
  it('同 pending_id 去重、新的排前、超过上限丢弃最旧', () => {
    let list: ApprovalHistoryEntry[] = [at(2), at(1)]
    list = appendHistory(list, at(3))
    expect(list.map((e) => e.pending_id)).toEqual(['p3', 'p2', 'p1'])
    list = appendHistory(list, at(2))
    expect(list.map((e) => e.pending_id)).toEqual(['p2', 'p3', 'p1'])
    list = appendHistory(list, at(9), 2)
    expect(list.map((e) => e.pending_id)).toEqual(['p9', 'p2'])
  })
})

describe('ApprovalCard', () => {
  it('渲染工具名 / 目标 / 参数摘要 / 指纹，且不出档位徽章（M4a-F2 前留位）', () => {
    render(<ApprovalCard item={rpcItem} onApprove={vi.fn()} onReject={vi.fn()} />)
    const card = screen.getByTestId('approval-card')
    expect(card).toBeTruthy()
    expect(card.textContent).toContain('notes.delete')
    expect(card.textContent).toContain('mp30hc39junb')
    expect(card.textContent).toContain('RPC 方法 notes.delete 需要用户审批')
    expect(card.textContent).toContain('3819f8a34e1e')
    expect(screen.queryByText(/档位|risk_level/)).toBeNull()
  })

  it('批准按钮直连 onApprove(pending_id)，拒绝同理', async () => {
    const onApprove = vi.fn().mockResolvedValue({ ok: true })
    const onReject = vi.fn().mockResolvedValue({ ok: true })
    render(<ApprovalCard item={rpcItem} onApprove={onApprove} onReject={onReject} />)
    fireEvent.click(screen.getByTestId('approval-approve'))
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith('8242339d84dc'))
    fireEvent.click(screen.getByTestId('approval-reject'))
    await waitFor(() => expect(onReject).toHaveBeenCalledWith('8242339d84dc'))
  })

  it('后端拒绝同连接自批时如实显示错误，不伪装成功', async () => {
    const onApprove = vi.fn().mockResolvedValue({ ok: false, error: '审批不得由发起该操作的同一连接自我批准' })
    render(<ApprovalCard item={rpcItem} onApprove={onApprove} onReject={vi.fn()} />)
    fireEvent.click(screen.getByTestId('approval-approve'))
    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('同一连接自我批准')
    })
  })

  it('已定态（收件箱历史侧）不再渲染批准/拒绝按钮', () => {
    render(<ApprovalCard item={rpcItem} variant="row" resolvedAs="rejected" onApprove={vi.fn()} onReject={vi.fn()} />)
    expect(screen.queryByTestId('approval-approve')).toBeNull()
    expect(screen.queryByTestId('approval-reject')).toBeNull()
    expect(screen.getByTestId('approval-card').textContent).toContain('已拒绝')
  })
})
