import Icon from './Icon'
/**
 * A2（前后端对齐规划）：同步冲突裁决区。
 *
 * 数据来源：
 * - 实时：sync.conflict 事件经 backendEventsStore 落点（A1 接入层）
 * - 补拉：挂载时调用 sync.conflicts.list 覆盖离线期间产生的冲突
 *
 * 裁决：sync.resolve_conflict（choice=remote 保留远端 / local 恢复本地快照）。
 * 后端引用：backend/handlers/_sync.py、sync/cloud_sync_client.py。
 *
 * i18n 说明：按用户确认不做英文界面，本组件直接使用中文文案
 * （与前端报告"i18n 暂不实施"的既定决策一致）。
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useBackendEventsStore, type SyncConflictInfo } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'

interface BackendConflict {
  resource: string
  id: string
  title?: string
  updated_at?: string
  has_local_version?: boolean
}

interface ResolveResult {
  success: boolean
  error?: string
}

const SyncConflictResolver: React.FC = () => {
  const storeConflicts = useBackendEventsStore((s) => s.syncConflicts)
  const removeSyncConflict = useBackendEventsStore((s) => s.removeSyncConflict)
  const [backendConflicts, setBackendConflicts] = useState<BackendConflict[]>([])
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  // 挂载时补拉后端冲突列表（覆盖离线/重启期间产生的冲突）
  const fetchConflicts = useCallback(async () => {
    try {
      const client = getWsClient()
      if (client.status !== 'connected') return
      const result = await client.call<{ success?: boolean; conflicts?: BackendConflict[] }>('sync.conflicts.list')
      if (result?.success && Array.isArray(result.conflicts)) {
        setBackendConflicts(result.conflicts)
      }
    } catch (e) {
      swallowError('SyncConflictResolver:fetch', e)
    }
  }, [])

  useEffect(() => {
    fetchConflicts()
  }, [fetchConflicts])

  // 合并：store 实时事件优先（含标题/时间戳），后端列表补漏，按 resource+id 去重
  const merged: SyncConflictInfo[] = (() => {
    const map = new Map<string, SyncConflictInfo>()
    for (const c of backendConflicts) {
      if (!c.id) continue
      map.set(`${c.resource}:${c.id}`, {
        resource: c.resource,
        id: String(c.id),
        title: c.title ?? '',
        local_time: 0,
        remote_time: 0,
        receivedAt: 0,
      })
    }
    for (const c of storeConflicts) {
      map.set(`${c.resource}:${c.id}`, c)
    }
    return [...map.values()]
  })()

  const resolve = async (resource: string, id: string, choice: 'remote' | 'local') => {
    const key = `${resource}:${id}`
    setBusyKey(key)
    setMessage(null)
    try {
      const client = getWsClient()
      const result = await client.call<ResolveResult>('sync.resolve_conflict', { resource, id, choice })
      if (result?.success) {
        removeSyncConflict(resource, id)
        setBackendConflicts((list) => list.filter((c) => !(c.resource === resource && String(c.id) === id)))
        setMessage({ kind: 'ok', text: choice === 'remote' ? '已保留远端版本' : '已恢复本地版本（将在下次同步推回远端）' })
      } else {
        setMessage({ kind: 'err', text: result?.error ?? '裁决失败' })
      }
      setTimeout(() => setMessage(null), 4000)
    } catch (err) {
      setMessage({ kind: 'err', text: (err as Error).message })
      setTimeout(() => setMessage(null), 4000)
    } finally {
      setBusyKey(null)
    }
  }

  if (merged.length === 0 && !message) return null

  return (
    <div className="mb-4 p-3 rounded-xl border" style={{ borderColor: 'rgba(250,204,21,0.35)', background: 'rgba(250,204,21,0.06)' }}>
      {merged.length > 0 && (
        <>
          <div className="text-xs font-medium text-yellow-400 flex items-center gap-2 mb-2">
            <Icon icon="fa-solid fa-triangle-exclamation" />
            同步冲突待裁决（{merged.length}）
          </div>
          <div className="text-xs text-text-muted mb-2">
            同一内容在多端被同时修改。请选择保留版本；本地版本来自冲突发生前的自动快照。
          </div>
          <div className="space-y-2">
            {merged.map((c) => {
              const key = `${c.resource}:${c.id}`
              const busy = busyKey === key
              return (
                <div key={key} className="flex items-center justify-between gap-2 p-2 rounded-lg" style={{ background: 'var(--panel-bg)', border: '1px solid var(--panel-border)' }}>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-text-primary truncate">
                      <span className="mr-1.5 px-1 py-0.5 rounded text-2xs" style={{ background: 'var(--input-bg)', color: 'var(--text-muted)' }}>
                        {c.resource === 'note' ? '笔记' : '任务'}
                      </span>
                      {c.title || c.id}
                    </div>
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => resolve(c.resource, c.id, 'local')}
                      disabled={busy}
                      className="px-2 py-1 rounded-lg text-xs font-medium transition-all bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-40"
                      title="恢复冲突前的本地快照，下次同步推回远端"
                    >
                      恢复本地
                    </button>
                    <button
                      onClick={() => resolve(c.resource, c.id, 'remote')}
                      disabled={busy}
                      className="px-2 py-1 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] disabled:opacity-40"
                      title="接受远端内容，放弃本地修改"
                    >
                      保留远端
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}
      {message && (
        <div className={`text-xs ${merged.length > 0 ? 'mt-2' : ''} ${message.kind === 'ok' ? 'text-fluent-green' : 'text-red-400'}`}>
          {message.text}
        </div>
      )}
    </div>
  )
}

export default SyncConflictResolver
