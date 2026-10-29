import { useState, useCallback, useEffect, useRef } from 'react'
import { getWsClient } from '../utils/ai'
import { onBackendEvent } from '@desk-notes/shared'
import type { SandboxPreviewData } from './SandboxPreview'
import { swallowError } from '../utils/swallowError'

/** Result of a sandbox.preview call */
export interface SandboxPreviewResult extends SandboxPreviewData {
  pending_id: string
  dry_run: boolean
}

/** State container for the sandbox system */
export interface SandboxState {
  pendingList: SandboxPreviewResult[]
  /** Preview a tool execution without side effects */
  preview: (actionId: string, args: Record<string, unknown>) => Promise<SandboxPreviewResult>
  /** Approve a pending operation */
  approve: (pendingId: string) => Promise<boolean>
  /** A4: Batch approve pending operations; returns approved count */
  batchApprove: (pendingIds: string[]) => Promise<number>
  /** Reject a pending operation */
  reject: (pendingId: string) => Promise<boolean>
  /** Refresh the pending list from backend */
  refreshPending: () => Promise<void>
  /** Clear all pending operations locally */
  clearLocal: () => void
}

/**
 * Hook for interacting with the backend sandbox preview system.
 *
 * ```ts
 * const sandbox = useSandbox()
 * const preview = await sandbox.preview('file.write', { path: '/tmp/test.txt', content: 'hello' })
 * // → shows SandboxPreview component
 * ```
 */
export function useSandbox(): SandboxState {
  const [pendingList, setPendingList] = useState<SandboxPreviewResult[]>([])
  const pollRef = useRef<ReturnType<typeof setInterval>>()

  const call = useCallback(async (method: string, params: Record<string, unknown> = {}): Promise<Record<string, unknown>> => {
    const client = getWsClient()
    if (client.status !== 'connected') {
      throw new Error(`sandbox: WebSocket not connected (status: ${client.status})`)
    }
    try {
      const result = await client.call(method, params)
      return (result ?? {}) as Record<string, unknown>
    } catch (err) {
      console.error(`sandbox.${method} failed:`, err)
      throw err
    }
  }, [])

  const preview = useCallback(async (actionId: string, args: Record<string, unknown>): Promise<SandboxPreviewResult> => {
    const result = await call('sandbox.preview', { action_id: actionId, ...args })
    if (result?.error) throw new Error(String(result.error))
    return result as unknown as SandboxPreviewResult
  }, [call])

  const approve = useCallback(async (pendingId: string): Promise<boolean> => {
    const result = await call('sandbox.approve', { pending_id: pendingId })
    if (result?.error) return false
    setPendingList(prev => prev.filter(p => p.pending_id !== pendingId))
    return true
  }, [call])

  // A4：批量批准（sandbox.batch_approve）——并发任务审批不逐条点
  const batchApprove = useCallback(async (pendingIds: string[]): Promise<number> => {
    if (pendingIds.length === 0) return 0
    const result = await call('sandbox.batch_approve', { pending_ids: pendingIds })
    if (result?.error) return 0
    const approved = typeof result.approved === 'number' ? result.approved : 0
    setPendingList(prev => prev.filter(p => !pendingIds.includes(p.pending_id)))
    return approved
  }, [call])

  const reject = useCallback(async (pendingId: string): Promise<boolean> => {
    const result = await call('sandbox.reject', { pending_id: pendingId })
    if (result?.error) return false
    setPendingList(prev => prev.filter(p => p.pending_id !== pendingId))
    return true
  }, [call])

  const refreshPending = useCallback(async () => {
    try {
      const list = await call('sandbox.list_pending')
      if (Array.isArray(list)) {
        setPendingList(list as SandboxPreviewResult[])
      }
    } catch (e) {
      // Silently fail — backend might not be connected
      swallowError('useSandbox:76', e)
    }
  }, [call])

  const clearLocal = useCallback(() => {
    setPendingList([])
  }, [])

  // Poll for pending operations periodically (fallback for missed events)
  useEffect(() => {
    refreshPending()
    // A-3：sandbox.pending_added/approved/rejected 事件已实时推送（下方订阅），
    // 轮询仅作漏事件兜底；旧值 5s 常驻烧 12 msg/min 配额。
    pollRef.current = setInterval(refreshPending, 30_000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [refreshPending])

  // 订阅 WebSocket 事件：实时感知审批状态变化（无需等待轮询）
  // B3：改走统一分发注册表（原 client.on('event') 手写过滤已废弃）
  useEffect(() => {
    const offs = [
      onBackendEvent('sandbox.pending_added', () => refreshPending()),
      onBackendEvent('sandbox.approved', () => refreshPending()),
      onBackendEvent('sandbox.rejected', () => refreshPending()),
    ]
    return () => {
      for (const off of offs) off()
    }
  }, [refreshPending])

  return {
    pendingList,
    preview,
    approve,
    batchApprove,
    reject,
    refreshPending,
    clearLocal,
  }
}
