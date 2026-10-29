import React, { useMemo, useState, useEffect, useCallback } from 'react'
import { useStore } from '../../store/useStore'
import { getWsClient } from '../../utils/ai'
import { onBackendEvent, subscribeSyncQueue } from '@desk-notes/shared'
import { swallowError } from '../../utils/swallowError'
import { useTranslation } from '../../i18n'
import StatusPill from '../Common/StatusPill'
import Icon from '../Common/Icon'

interface SystemStatus {
  mode: string
  services: Record<string, boolean>
  methods: string[]
}

interface SyncStatus {
  enabled: boolean
  configured: boolean
  running: boolean
  last_sync_at: number | null
  last_error?: string
}

/** kernel.status（内核路由/降级观测）返回结构 */
interface KernelStatus {
  route: string
  effective_engine: string
  degraded: boolean
  gateway_present: boolean
  gateway_name: string
  kernel_type: string
  message: string
}

const StatusBar: React.FC = () => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const floatingNotes = useStore(s => s.floatingNotes)
  const statusBarCollapsed = useStore(s => s.statusBarCollapsed)
  const toggleStatusBarCollapsed = useStore(s => s.toggleStatusBarCollapsed)
  const [backendConnected, setBackendConnected] = useState(false)
  const [sysStatus, setSysStatus] = useState<SystemStatus | null>(null)
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null)
  const [kernelStatus, setKernelStatus] = useState<KernelStatus | null>(null)
  // 1.1 收尾：离线写队列待同步数量（可见化，不再静默）
  const [pendingSync, setPendingSync] = useState(0)

  const ei = window.electronAPI as ElectronAPI | undefined

  // Track backend connection status
  useEffect(() => {
    const client = getWsClient()
    setBackendConnected(client.status === 'connected')

    const unsub = client.onStatusChange((s) => {
      setBackendConnected(s === 'connected')
      ei?.updateBackendStatus?.(s === 'connected')
    })

    ei?.updateBackendStatus?.(client.status === 'connected')

    return unsub
  }, [ei])

  // 订阅待同步写操作队列数量变化
  useEffect(() => subscribeSyncQueue(setPendingSync), [])

  // Fetch system status when connected
  const fetchStatus = useCallback(async () => {
    try {
      const client = getWsClient()
      if (client.status !== 'connected') return
      const result = await client.call<SystemStatus>('system.status')
      setSysStatus(result)
      try {
        const ks = await client.call<KernelStatus>('kernel.status')
        setKernelStatus(ks)
      } catch (ke) {
        // 内核状态非关键
        swallowError('StatusBar:kernel', ke)
      }
    } catch (e) {
      // Silently ignore — status is non-critical
      swallowError('StatusBar:50', e)
    }
  }, [])

  useEffect(() => {
    if (backendConnected) {
      fetchStatus()
      const interval = setInterval(fetchStatus, 30000)
      return () => clearInterval(interval)
    } else {
      setSysStatus(null)
      setKernelStatus(null)
    }
  }, [backendConnected, fetchStatus])

  // 拉取云同步状态 + 订阅 sync.* 事件
  useEffect(() => {
    if (!backendConnected) {
      setSyncStatus(null)
      return
    }
    const client = getWsClient()
    const fetchSync = async () => {
      try {
        const result = await client.call<SyncStatus>('sync.status')
        setSyncStatus(result)
      } catch (e) { /* 静默 */ swallowError('StatusBar:76', e) }
    }
    fetchSync()
    const interval = setInterval(fetchSync, 15000)
    // B3：统一分发注册表（替代 client.on('event') 手写过滤）
    const offCompleted = onBackendEvent('sync.completed', () => fetchSync())
    const offFailed = onBackendEvent('sync.failed', () => fetchSync())
    return () => { clearInterval(interval); offCompleted(); offFailed() }
  }, [backendConnected])

  const serviceIcons = useMemo(() => {
    if (!sysStatus?.services) return null
    const icons: string[] = []
    if (sysStatus.services.ai) icons.push(t('statusBar.svcAI'))
    if (sysStatus.services.memory) icons.push(t('statusBar.svcMemory'))
    if (sysStatus.services.knowledge_graph) icons.push(t('statusBar.svcKnowledge'))
    return icons.length > 0 ? icons.join(' ') : null
  }, [sysStatus, t])

  const summary = useMemo(() => {
    const pending = todos.filter(td => !td.done).length
    const notes = floatingNotes.filter(n => !n.archived).length
    const parts: string[] = []
    if (pending > 0) parts.push(t('statusBar.todoSummary', { count: pending }))
    if (notes > 0) parts.push(t('statusBar.notesSummary', { count: notes }))
    return parts.join(' · ') || t('statusBar.ready')
  }, [todos, floatingNotes, t])

  // 折叠态：仅保留一条细条，点击即可重新展开（占位最小，避免常驻 28px 挤占内容空间）
  if (statusBarCollapsed) {
    return (
      <div
        onClick={toggleStatusBarCollapsed}
        title={t('statusBar.expand')}
        className="flex items-center justify-between flex-shrink-0 select-none px-2 border-t cursor-pointer"
        style={{ height: 22, borderColor: 'var(--panel-border)', background: 'var(--panel-bg)' }}
      >
        <span className="w-1.5 h-1.5 rounded-full" style={{ background: backendConnected ? 'var(--success)' : 'var(--danger)' }} />
        <Icon icon="fa-solid fa-chevron-up text-[9px]" style={{ color: 'var(--text-dim)' }} />
      </div>
    )
  }

  return (
    <div
      className="flex items-center justify-between flex-shrink-0 select-none px-4 border-t"
      style={{
        height: 28,
        borderColor: 'var(--panel-border)',
        background: 'var(--panel-bg)',
      }}
    >
      {/* 折叠控制（状态栏最左） */}
      <button
        onClick={toggleStatusBarCollapsed}
        title={t('statusBar.collapse')}
        className="flex-shrink-0 mr-2 w-5 h-5 flex items-center justify-center rounded text-text-dim hover:text-text-secondary hover:bg-hover-bg transition-colors"
      >
        <Icon icon="fa-solid fa-chevron-down text-[9px]" />
      </button>
      {/* Left: services（后端连接状态已收敛至聊天页展示，此处不再重复渲染） */}
      <div className="flex items-center gap-2">
        {pendingSync > 0 && (
          <StatusPill tone="warn" label={t('ux.pendingSyncBadge', { count: pendingSync })} />
        )}
        {backendConnected && serviceIcons && (
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
            {serviceIcons}
          </span>
        )}
        {backendConnected && kernelStatus && (
          <StatusPill
            tone={kernelStatus.degraded ? 'warn' : kernelStatus.effective_engine === 'gateway' ? 'ok' : 'idle'}
            label={t(
              kernelStatus.effective_engine === 'gateway'
                ? 'statusBar.kernelGateway'
                : kernelStatus.degraded
                  ? 'statusBar.kernelDegraded'
                  : kernelStatus.effective_engine === 'butler'
                    ? 'statusBar.kernelButler'
                    : 'statusBar.kernelUnknown'
            )}
            title={kernelStatus.message || kernelStatus.gateway_name}
          />
        )}
        {backendConnected && syncStatus?.enabled && (
          <StatusPill
            tone="running"
            pulse={syncStatus.running}
            label={syncStatus.running ? t('statusBar.syncing') : t('statusBar.cloudSync')}
            title={syncStatus.last_error ? t('statusBar.cloudSyncTitleWithError', { error: syncStatus.last_error }) : t('statusBar.cloudSyncTitle')}
          />
        )}
      </div>

      {/* Right: summary */}
      <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
        {summary}
      </span>
    </div>
  )
}

export default StatusBar
