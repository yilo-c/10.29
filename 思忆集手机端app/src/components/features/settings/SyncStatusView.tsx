import { useState, useCallback, useEffect } from 'react'
import { tokenManager } from '../../../lib/tokenManager'
import { haptic } from '../../../lib/haptic'
import { fullContentSync, getPendingOpsCount } from '../../../lib/contentSync'
import { getSyncStatus, type SyncStatusRemote } from '../../../lib/bridges/syncBridge'

type StatusDot = 'synced' | 'syncing' | 'error' | 'offline'

interface ResourceRow {
  key: string
  label: string
  status: StatusDot
}

export default function SyncStatusView() {
  const [rows, setRows] = useState<ResourceRow[]>([
    { key: 'notes', label: '笔记', status: 'offline' },
    { key: 'tasks', label: '任务', status: 'offline' },
    { key: 'translations', label: '翻译', status: 'offline' },
    { key: 'wordbooks', label: '词本', status: 'offline' },
  ])
  const [cloudStatus, setCloudStatus] = useState<SyncStatusRemote | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [lastSync, setLastSync] = useState<string | null>(tokenManager.isPaired ? localStorage.getItem('siyi_last_sync_time') : null)
  const [statusMessage, setStatusMessage] = useState<string | null>(null)
  // b4-m2：待处理操作数接 contentSync 的 PendingOpManager（同步后刷新）
  const [pendingOps, setPendingOps] = useState(getPendingOpsCount)

  useEffect(() => {
    if (!tokenManager.isPaired) return
    getSyncStatus().then(setCloudStatus).catch(() => {})
  }, [])

  const updateRow = (key: string, status: StatusDot) => {
    setRows(prev => prev.map(r => r.key === key ? { ...r, status } : r))
  }

  const handleSync = useCallback(async () => {
    if (!tokenManager.isPaired || syncing) return

    haptic.medium()
    setSyncing(true)
    setStatusMessage(null)

    rows.forEach(r => updateRow(r.key, 'syncing'))

    try {
      const result = await fullContentSync()

      updateRow('notes', result.notes >= 0 ? 'synced' : 'error')
      updateRow('tasks', result.tasks >= 0 ? 'synced' : 'error')
      updateRow('translations', result.translations >= 0 ? 'synced' : 'error')
      updateRow('wordbooks', result.wordbooks >= 0 ? 'synced' : 'error')

      const pulled = result.notes + result.tasks + result.translations + result.wordbooks
      setStatusMessage(`同步完成 · 新增 ${pulled} 条`)
      haptic.success()

      setLastSync(new Date().toISOString())
      localStorage.setItem('siyi_last_sync_time', new Date().toISOString())
      // b4-m2：同步后刷新待处理操作数（重放成功的 op 已出队）
      setPendingOps(getPendingOpsCount())

      // 同时刷新桌面端云同步状态
      getSyncStatus().then(setCloudStatus).catch(() => {})
    } catch (err) {
      setStatusMessage('同步失败: ' + (err instanceof Error ? err.message : '未知错误'))
      rows.forEach(r => updateRow(r.key, 'error'))
      haptic.error()
    } finally {
      setSyncing(false)
    }
  }, [syncing, rows])

  const dotColor: Record<StatusDot, string> = {
    synced: '#34C759',
    syncing: '#FF9F0A',
    error: '#FF3B30',
    offline: 'var(--text-disabled)',
  }

  const dotLabel: Record<StatusDot, string> = {
    synced: '已同步',
    syncing: '同步中',
    error: '错误',
    offline: '未连接',
  }

  return (
    <div style={{ marginTop: 28 }}>
      <div style={sectionTitleStyle}>同步状态</div>

      <div style={cardStyle}>
        {/* Resource rows */}
        {rows.map((row, i) => (
          <div
            key={row.key}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '12px 16px',
              borderBottom: i < rows.length - 1 ? '0.5px solid var(--border-subtle)' : 'none',
            }}
          >
            <div style={{
              width: 8, height: 8, borderRadius: '50%',
              background: dotColor[row.status],
              flexShrink: 0,
              transition: 'background 0.3s ease',
            }} />
            <span style={{ fontSize: 'var(--text-footnote)', flex: 1 }}>{row.label}</span>
            <span style={{
              fontSize: 'var(--text-caption-2)',
              color: 'var(--text-tertiary)',
            }}>
              {dotLabel[row.status]}
            </span>
          </div>
        ))}

        {/* Cloud sync status */}
        {cloudStatus && (
          <div style={{
            padding: '10px 16px',
            borderTop: '0.5px solid var(--border-subtle)',
            fontSize: 'var(--text-caption-2)',
            color: 'var(--text-tertiary)',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>桌面端云同步</span>
              <span style={{ color: cloudStatus.enabled ? 'var(--tone-smooth)' : 'var(--text-tertiary)' }}>
                {cloudStatus.enabled ? '已启用' : '未启用'}
              </span>
            </div>
            {cloudStatus.last_sync_at && (
              <div style={{ marginTop: 2 }}>
                上次: {new Date(cloudStatus.last_sync_at).toLocaleString('zh-CN')}
              </div>
            )}
          </div>
        )}

        {/* Info footer */}
        <div style={{
          padding: '10px 16px',
          borderTop: '0.5px solid var(--border-subtle)',
          fontSize: 'var(--text-caption-2)',
          color: 'var(--text-tertiary)',
        }}>
          <div style={{ marginBottom: 2 }}>
            上次同步: {lastSync ? new Date(lastSync).toLocaleString('zh-CN') : '从未'}
          </div>
          {pendingOps > 0 && (
            <div style={{ color: 'var(--accent)' }}>
              待处理操作: {pendingOps}
            </div>
          )}
        </div>
      </div>

      {/* Sync button */}
      <button
        onClick={handleSync}
        disabled={syncing || !tokenManager.isPaired}
        style={{
          marginTop: 10,
          width: '100%',
          padding: '10px 0',
          borderRadius: 'var(--radius-md)',
          border: 'none',
          background: syncing ? 'var(--bg-tertiary)' : tokenManager.isPaired ? 'var(--accent)' : 'var(--bg-tertiary)',
          color: syncing || !tokenManager.isPaired ? 'var(--text-disabled)' : '#fff',
          fontSize: 'var(--text-footnote)',
          fontWeight: 600,
          cursor: syncing || !tokenManager.isPaired ? 'default' : 'pointer',
        }}
      >
        {syncing ? '同步中...' : tokenManager.isPaired ? '全部刷新' : '未配对'}
      </button>

      {statusMessage && (
        <div style={{
          marginTop: 6,
          fontSize: 'var(--text-caption-2)',
          color: 'var(--text-tertiary)',
          textAlign: 'center',
        }}>
          {statusMessage}
        </div>
      )}
    </div>
  )
}

const sectionTitleStyle: React.CSSProperties = {
  fontSize: 'var(--text-caption-1)',
  fontWeight: 600,
  color: 'var(--text-tertiary)',
  marginBottom: 10,
  textTransform: 'uppercase',
  letterSpacing: 0.5,
}

const cardStyle: React.CSSProperties = {
  background: 'var(--bg-secondary)',
  borderRadius: 'var(--radius-lg)',
  overflow: 'hidden',
}
