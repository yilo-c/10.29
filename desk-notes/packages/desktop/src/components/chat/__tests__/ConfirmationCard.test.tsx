/**
 * F-P1-1 队列2 审批卡（ConfirmationCard）—— 契约对齐主窗 ChatView 内联卡：
 * reason 必填闸（空理由禁用批准键）、允许执行/始终允许/拒绝三键、
 * expired 定案态撤按钮。resolve 链行为归 hooks（useConfirmApprovals.test 由
 * tests/floating-confirm-contract.test.ts 承担），本文件只锁渲染契约。
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { ConfirmationCard } from '../ConfirmationCard'
import type { ConfirmItem } from '../../../hooks/useConfirmApprovals'

const pending: ConfirmItem = {
  confirmation_id: 'c-1',
  action_id: 'shell.run',
  args: { command: 'echo hi' },
  status: 'pending',
  source: 'butler',
}

describe('ConfirmationCard（队列2）', () => {
  it('pending 态渲染 action_id、参数折叠入口与三键（允许执行/始终允许/拒绝）', () => {
    render(<ConfirmationCard item={pending} onApprove={vi.fn()} onReject={vi.fn()} />)
    expect(screen.getByTestId('confirmation-card').textContent).toContain('shell.run')
    expect(screen.getByTestId('confirmation-toggle-args')).toBeTruthy()
    expect(screen.getByTestId('confirmation-approve')).toBeTruthy()
    expect(screen.getByTestId('confirmation-always')).toBeTruthy()
    expect(screen.getByTestId('confirmation-reject')).toBeTruthy()
  })

  it('理由必填：未填理由时批准/始终允许禁用、拒绝可用（主窗同款）', () => {
    render(<ConfirmationCard item={pending} onApprove={vi.fn()} onReject={vi.fn()} />)
    expect((screen.getByTestId('confirmation-approve') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByTestId('confirmation-always') as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByTestId('confirmation-reject') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.change(screen.getByTestId('confirmation-reason'), { target: { value: '  ' } })
    expect((screen.getByTestId('confirmation-approve') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByTestId('confirmation-reason'), { target: { value: '要跑命令' } })
    expect((screen.getByTestId('confirmation-approve') as HTMLButtonElement).disabled).toBe(false)
  })

  it('批准 → onApprove(confirmation_id, reason, alwaysAllow)；始终允许 → true', async () => {
    const onApprove = vi.fn().mockResolvedValue({ ok: true })
    render(<ConfirmationCard item={pending} onApprove={onApprove} onReject={vi.fn()} />)
    fireEvent.change(screen.getByTestId('confirmation-reason'), { target: { value: '理由A' } })
    fireEvent.click(screen.getByTestId('confirmation-approve'))
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith('c-1', '理由A', false))
    fireEvent.click(screen.getByTestId('confirmation-always'))
    await waitFor(() => expect(onApprove).toHaveBeenCalledWith('c-1', '理由A', true))
  })

  it('拒绝 → onReject(confirmation_id, 已填理由如实传)', async () => {
    const onReject = vi.fn().mockResolvedValue({ ok: true })
    render(<ConfirmationCard item={pending} onApprove={vi.fn()} onReject={onReject} />)
    fireEvent.change(screen.getByTestId('confirmation-reason'), { target: { value: '不想跑' } })
    fireEvent.click(screen.getByTestId('confirmation-reject'))
    await waitFor(() => expect(onReject).toHaveBeenCalledWith('c-1', '不想跑'))
  })

  it('resolve 失败 → role=alert 如实反馈；expired 显示已过期文案', async () => {
    const onApprove = vi.fn().mockResolvedValue({ ok: false, expired: true })
    render(<ConfirmationCard item={pending} onApprove={onApprove} onReject={vi.fn()} />)
    fireEvent.change(screen.getByTestId('confirmation-reason'), { target: { value: 'x' } })
    fireEvent.click(screen.getByTestId('confirmation-approve'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('过期'))
  })

  it('已定案/过期态收为静态行，不再渲染按钮（TTL 过期不得死循环弹回）', () => {
    const { rerender } = render(
      <ConfirmationCard item={{ ...pending, status: 'expired' }} onApprove={vi.fn()} onReject={vi.fn()} />,
    )
    expect(screen.queryByTestId('confirmation-approve')).toBeNull()
    expect(screen.getByTestId('confirmation-card').textContent).toContain('已过期')
    rerender(<ConfirmationCard item={{ ...pending, status: 'approved' }} onApprove={vi.fn()} onReject={vi.fn()} />)
    expect(screen.queryByTestId('confirmation-reject')).toBeNull()
    expect(screen.getByTestId('confirmation-card').textContent).toContain('已批准并执行')
  })
})
