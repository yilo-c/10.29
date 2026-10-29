import Icon from './Icon'
/**
 * 云同步配置面板（Cloudflare Worker 降级通道）
 *
 * 桌面端默认通过 Tailscale 直连手机端（全功能 RPC）。
 * 当 Tailscale 不可达时，Cloudflare Worker 提供加密 push/pull 降级同步
 * （仅 notes/todos/settings 3 类数据）。
 *
 * 本面板配置 Worker URL / Token / register_secret，并提供
 * 启用/禁用/立即同步按钮 + 实时状态显示。
 * A2：顶部嵌入同步冲突裁决区（SyncConflictResolver）。
 */
import React, { useState, useEffect, useCallback } from 'react'
import { useTranslation, onBackendEvent } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'
import { errorStore } from '../../store/errorStore'
import SyncConflictResolver from './SyncConflictResolver'

interface SyncStatus {
  enabled: boolean
  configured: boolean
  running: boolean
  last_sync_at: number | null
  last_error?: string
  message?: string
  push_count?: number
  pull_count?: number
  cursor?: number
}

const CloudSyncPanel: React.FC = () => {
  const { t } = useTranslation()
  const [workerUrl, setWorkerUrl] = useState('')
  const [token, setToken] = useState('')
  const [registerSecret, setRegisterSecret] = useState('')
  const [intervalSec, setIntervalSec] = useState('300')
  const [status, setStatus] = useState<SyncStatus | null>(null)
  const [busy, setBusy] = useState(false)

  // 拉取当前同步状态
  const fetchStatus = useCallback(async () => {
    try {
      const client = getWsClient()
      if (client.status !== 'connected') return
      const result = await client.call<SyncStatus>('sync.status')
      setStatus(result)
    } catch (e) {
      // 静默失败 — 状态非关键
      swallowError('CloudSyncPanel:42', e)
    }
  }, [])

  useEffect(() => {
    fetchStatus()
    const id = setInterval(fetchStatus, 10000)
    return () => clearInterval(id)
  }, [fetchStatus])

  // 订阅 sync.completed / sync.failed 事件刷新状态（B3：统一分发注册表）
  useEffect(() => {
    const offCompleted = onBackendEvent('sync.completed', (payload) => {
      fetchStatus()
      errorStore.success(t('cloudSync.feedbackCompletedFmt', { push: String(payload.push_count ?? 0), pull: String(payload.pull_count ?? 0) }))
    })
    const offFailed = onBackendEvent('sync.failed', (payload) => {
      fetchStatus()
      if (payload.fatal) {
        errorStore.error(t('cloudSync.feedbackFatalFmt', { error: String(payload.error ?? t('cloudSync.unknownError')) }))
      } else if (payload.error) {
        errorStore.error(t('cloudSync.feedbackFailedFmt', { error: String(payload.error) }))
      } else {
        errorStore.error(t('cloudSync.feedbackPartialFmt', { push: payload.push_ok ? t('cloudSync.okMark') : t('cloudSync.failMark'), pull: payload.pull_ok ? t('cloudSync.okMark') : t('cloudSync.failMark') }))
      }
    })
    return () => {
      offCompleted()
      offFailed()
    }
  }, [fetchStatus, t])

  const handleConfigure = async () => {
    setBusy(true)
    try {
      const client = getWsClient()
      const params: Record<string, unknown> = {}
      if (workerUrl) params.worker_url = workerUrl
      if (token) params.token = token
      if (registerSecret) params.register_secret = registerSecret
      if (intervalSec) params.interval_seconds = parseInt(intervalSec, 10)
      await client.call('sync.configure', params)
      errorStore.success(t('cloudSync.feedbackConfigSaved'))
      await fetchStatus()
    } catch (err) {
      errorStore.error(t('cloudSync.feedbackConfigFailedFmt', { msg: (err as Error).message }))
    } finally { setBusy(false) }
  }

  const handleEnable = async () => {
    setBusy(true)
    try {
      const client = getWsClient()
      await client.call('sync.enable')
      errorStore.success(t('cloudSync.feedbackEnabled'))
      await fetchStatus()
    } catch (err) {
      errorStore.error(t('cloudSync.feedbackEnableFailedFmt', { msg: (err as Error).message }))
    } finally { setBusy(false) }
  }

  const handleDisable = async () => {
    setBusy(true)
    try {
      const client = getWsClient()
      await client.call('sync.disable')
      errorStore.info(t('cloudSync.feedbackDisabled'))
      await fetchStatus()
    } catch (err) {
      errorStore.error(t('cloudSync.feedbackDisableFailedFmt', { msg: (err as Error).message }))
    } finally { setBusy(false) }
  }

  const handleSyncNow = async () => {
    setBusy(true); errorStore.info(t('cloudSync.feedbackSyncing'))
    try {
      const client = getWsClient()
      await client.call('sync.sync_now')
      // 状态会通过 sync.completed/sync.failed 事件刷新
    } catch (err) {
      errorStore.error(t('cloudSync.feedbackSyncFailedFmt', { msg: (err as Error).message }))
      setBusy(false)
    }
  }

  const handleRegisterDevice = async () => {
    setBusy(true)
    try {
      const client = getWsClient()
      const ok = await client.call<boolean>('sync.register_device', { name: 'desktop' })
      if (ok) errorStore.success(t('cloudSync.feedbackRegisterOk'))
      else errorStore.error(t('cloudSync.feedbackRegisterFail'))
    } catch (err) {
      errorStore.error(t('cloudSync.feedbackRegisterFailedFmt', { msg: (err as Error).message }))
    } finally { setBusy(false) }
  }

  const formatTime = (ts: number | null): string => {
    if (!ts) return t('cloudSync.timeNever')
    const date = new Date(ts * 1000)  // 后端用秒
    const now = Date.now()
    const diff = Math.floor((now - date.getTime()) / 1000)
    if (diff < 60) return t('cloudSync.timeSecondsAgoFmt', { n: String(diff) })
    if (diff < 3600) return t('cloudSync.timeMinutesAgoFmt', { n: String(Math.floor(diff / 60)) })
    if (diff < 86400) return t('cloudSync.timeHoursAgoFmt', { n: String(Math.floor(diff / 3600)) })
    return date.toLocaleString()
  }

  const statusColor = status?.running ? 'text-fluent-green' : status?.enabled ? 'text-yellow-400' : 'text-text-muted'
  const statusText = status?.running ? t('cloudSync.statusSyncing') : status?.enabled ? t('cloudSync.statusEnabled') : status?.configured ? t('cloudSync.statusConfiguredNotEnabled') : t('cloudSync.statusUnconfigured')

  return (
    <div className="mb-6 p-4 rounded-xl border" style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg)' }}>
      {/* A2：同步冲突裁决区（无冲突时自动隐藏） */}
      <SyncConflictResolver />
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="text-sm font-medium text-text-primary flex items-center gap-2">
            <Icon icon="fa-solid fa-cloud-arrow-up text-fluent-blue" />
            {t('cloudSync.title')}
          </div>
          <div className="text-xs text-text-muted mt-0.5">
            {t('cloudSync.subtitle')}
          </div>
        </div>
        <div className={`text-xs font-medium ${statusColor}`}>
          <span className={`inline-block w-1.5 h-1.5 rounded-full mr-1.5 ${status?.running ? 'bg-fluent-green animate-pulse' : status?.enabled ? 'bg-yellow-400' : 'bg-white/30'}`} />
          {statusText}
        </div>
      </div>

      {/* 配置输入区 */}
      <div className="space-y-2 mb-3">
        <input
          value={workerUrl}
          onChange={e => setWorkerUrl(e.target.value)}
          placeholder={t('cloudSync.workerUrlPlaceholder')}
          className="w-full px-3 py-1.5 rounded-lg text-xs outline-none border"
          style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
        />
        <input
          value={token}
          onChange={e => setToken(e.target.value)}
          placeholder={t('cloudSync.tokenPlaceholder')}
          type="password"
          className="w-full px-3 py-1.5 rounded-lg text-xs outline-none border"
          style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
        />
        <input
          value={registerSecret}
          onChange={e => setRegisterSecret(e.target.value)}
          placeholder={t('cloudSync.registerSecretPlaceholder')}
          type="password"
          className="w-full px-3 py-1.5 rounded-lg text-xs outline-none border"
          style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
        />
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">{t('cloudSync.intervalLabel')}</span>
          <input
            value={intervalSec}
            onChange={e => setIntervalSec(e.target.value)}
            type="number"
            min="60"
            className="w-20 px-2 py-1 rounded-lg text-xs outline-none border"
            style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
          />
        </div>
      </div>

      {/* 操作按钮 */}
      <div className="flex flex-wrap gap-2 mb-3">
        <button
          onClick={handleConfigure}
          disabled={busy}
          className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary disabled:opacity-40"
        >
          <Icon icon="fa-solid fa-floppy-disk mr-1" />
          {t('cloudSync.saveConfig')}
        </button>
        <button
          onClick={handleRegisterDevice}
          disabled={busy || !registerSecret}
          className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-white/[0.04] text-text-secondary hover:bg-white/[0.08] hover:text-text-secondary disabled:opacity-40"
          title={t('cloudSync.registerDeviceTooltip')}
        >
          <Icon icon="fa-solid fa-link mr-1" />
          {t('cloudSync.registerDevice')}
        </button>
        {status?.enabled ? (
          <button
            onClick={handleDisable}
            disabled={busy}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-yellow-400/10 text-yellow-400 hover:bg-yellow-400/20 disabled:opacity-40"
          >
            <Icon icon="fa-solid fa-pause mr-1" />
            {t('cloudSync.disable')}
          </button>
        ) : (
          <button
            onClick={handleEnable}
            disabled={busy || !status?.configured}
            className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-40"
          >
            <Icon icon="fa-solid fa-play mr-1" />
            {t('cloudSync.enable')}
          </button>
        )}
        <button
          onClick={handleSyncNow}
          disabled={busy || !status?.enabled}
          className="px-3 py-1.5 rounded-lg text-xs font-medium transition-all bg-fluent-green/10 text-fluent-green hover:bg-fluent-green/20 disabled:opacity-40"
        >
          <Icon icon="fa-solid fa-rotate mr-1" />
          {t('cloudSync.syncNow')}
        </button>
      </div>

      {/* 状态详情 */}
      {status && (
        <div className="text-xs text-text-muted space-y-0.5">
          <div>{t('cloudSync.lastSyncFmt', { time: formatTime(status.last_sync_at ?? null) })}</div>
          {status.last_error && (
            <div className="text-red-400/70">{t('cloudSync.errorFmt', { msg: status.last_error })}</div>
          )}
        </div>
      )}

    </div>
  )
}

export default CloudSyncPanel
