import React, { useState, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'

import { getWsClient } from '../../utils/ai'
import { useTranslation } from '../../i18n'

interface SubsystemStatus {
  key: string
  name: string
  severity: 'healthy' | 'degraded' | 'down' | 'unknown'
  available: boolean
  message: string
  details: Record<string, unknown>
  latency_ms: number
  error: string
}

interface SystemHealthSnapshot {
  timestamp: number
  overall_severity: 'healthy' | 'degraded' | 'down' | 'unknown'
  summary: Record<string, number>
  counters?: Record<string, number>
  subsystems: SubsystemStatus[]
}

const SEVERITY_CONFIG: Record<string, { color: string; bg: string; icon: string }> = {
  healthy: { color: '#34d399', bg: 'rgba(52,211,153,0.08)', icon: 'fa-circle-check' },
  degraded: { color: '#fbbf24', bg: 'rgba(251,191,36,0.08)', icon: 'fa-triangle-exclamation' },
  down: { color: 'var(--danger)', bg: 'rgba(239,68,68,0.08)', icon: 'fa-circle-xmark' },
  unknown: { color: '#6b7280', bg: 'rgba(107,114,128,0.08)', icon: 'fa-circle-question' },
}

const SEVERITY_LABEL_KEY: Record<string, string> = {
  healthy: 'systemHealth.labelHealthy',
  degraded: 'systemHealth.labelDegraded',
  down: 'systemHealth.labelDown',
  unknown: 'systemHealth.labelUnknown',
}

const SUBSYSTEM_ICONS: Record<string, string> = {
  ai: 'fa-brain',
  cost: 'fa-coins',
  cron: 'fa-clock',
  memory: 'fa-database',
  knowledge_graph: 'fa-diagram-project',
  sync: 'fa-cloud-arrow-up',
  vocabulary: 'fa-book',
  inbox: 'fa-inbox',
  notes: 'fa-note-sticky',
  tasks: 'fa-list-check',
}

function formatTimestamp(ts: number): string {
  if (!ts) return '--'
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString(undefined, { hour12: false })
}

function formatDetails(details: Record<string, unknown>): string {
  if (!details || Object.keys(details).length === 0) return ''
  try {
    return JSON.stringify(details, null, 2)
  } catch {
    return String(details)
  }
}

interface ProviderStatus {
  name: string
  model: string
  models: string[]
  enabled: boolean
  is_local: boolean
  is_fallback: boolean
  has_api_key: boolean
  valid: boolean
  description?: string
}

function renderAIProviders(details: Record<string, unknown>, t: (key: string) => string) {
  const providers = details.providers as ProviderStatus[] | undefined
  if (!Array.isArray(providers) || providers.length === 0) return null
  return (
    <div className="mt-1.5 space-y-1">
      {providers.map(p => (
        <div
          key={p.name}
          className="flex items-center justify-between gap-1.5 px-1.5 py-1 rounded"
          style={{ background: 'var(--panel-bg)' }}
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <div
              className="w-1.5 h-1.5 rounded-full flex-shrink-0"
              style={{ background: p.enabled && p.valid ? '#34d399' : p.enabled ? '#fbbf24' : '#6b7280' }}
            />
            <span className="text-2xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>{p.name}</span>
            {p.is_local && <span className="text-[7px] px-1 rounded" style={{ background: 'rgba(52,211,153,0.12)', color: '#34d399' }}>{t('systemHealth.tagLocal')}</span>}
            {!p.enabled && <span className="text-[7px] px-1 rounded" style={{ background: 'rgba(107,114,128,0.12)', color: '#9ca3af' }}>{t('systemHealth.tagDisabled')}</span>}
            {p.enabled && !p.valid && <span className="text-[7px] px-1 rounded" style={{ background: 'rgba(251,191,36,0.12)', color: '#fbbf24' }}>{t('systemHealth.tagIncomplete')}</span>}
          </div>
          <span className="text-[8px] truncate max-w-[90px]" style={{ color: 'var(--text-muted)' }}>{p.model}</span>
        </div>
      ))}
    </div>
  )
}

const SystemHealthPanel: React.FC = () => {
  const { t } = useTranslation()
  const [snapshot, setSnapshot] = useState<SystemHealthSnapshot | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [expandedKey, setExpandedKey] = useState<string | null>(null)
  // P3b：渐进披露——健康/未知分组默认折叠
  const [showHealthy, setShowHealthy] = useState(false)
  const [showUnknown, setShowUnknown] = useState(false)

  const fetchHealth = useCallback(async (forceRefresh = false) => {
    const client = getWsClient()
    if (client.status !== 'connected') {
      setLoading(false)
      return
    }
    try {
      if (forceRefresh) setRefreshing(true)
      const result = await client.call<SystemHealthSnapshot>(
        'system.health',
        forceRefresh ? { force_refresh: true } : {},
      )
      setSnapshot(result)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    fetchHealth()
    const interval = setInterval(() => fetchHealth(), 15000)
    const client = getWsClient()
    const cleanup = client.onStatusChange(() => {
      if (client.status === 'connected') fetchHealth()
    })
    return () => {
      clearInterval(interval)
      cleanup?.()
    }
  }, [fetchHealth])

  const handleManualRefresh = () => fetchHealth(true)

  const toggleExpand = (key: string) => {
    setExpandedKey(prev => (prev === key ? null : key))
  }

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center py-8">
        <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-2 py-6 text-center">
        <div className="text-xl opacity-25"><Icon icon="fa-solid fa-circle-exclamation" /></div>
        <div className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('systemHealth.fetchFailed')}</div>
        <div className="text-2xs max-w-[200px] truncate" style={{ color: 'var(--text-dim)' }}>{error}</div>
        <button
          onClick={handleManualRefresh}
          className="mt-1 text-xs px-2 py-1 rounded border hover:opacity-80"
          style={{ borderColor: 'var(--panel-border)', color: 'var(--text-secondary)' }}
        >
          <Icon icon="fa-solid fa-rotate-right mr-1" />{t('systemHealth.retry')}
        </button>
      </div>
    )
  }

  if (!snapshot) {
    return (
      <div className="flex-1 flex items-center justify-center py-8 text-xs" style={{ color: 'var(--text-muted)' }}>
        {t('systemHealth.noData')}
      </div>
    )
  }

  const overallCfg = SEVERITY_CONFIG[snapshot.overall_severity] || SEVERITY_CONFIG.unknown
  const summary = snapshot.summary || {}

  // P3b：按严重度分组，注意力优先给需要关注的
  const attention = snapshot.subsystems.filter(ss => ss.severity === 'degraded' || ss.severity === 'down')
  const healthy = snapshot.subsystems.filter(ss => ss.severity === 'healthy')
  const unknown = snapshot.subsystems.filter(ss => ss.severity === 'unknown')

  const MetricCard: React.FC<{ icon: string; label: string; value: number }> = ({ icon, label, value }) => (
    <div
      className="rounded-xl border p-2.5 flex items-center gap-2.5"
      style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg)' }}
    >
      <div className="w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0" style={{ background: 'rgba(52,211,153,0.12)' }}>
        <Icon icon={`${icon} text-xs`} style={{ color: '#34d399' }} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-2xs truncate" style={{ color: 'var(--text-muted)' }}>{label}</div>
        <div className="text-sm font-semibold tabular-nums" style={{ color: 'var(--text-primary)' }}>{value}</div>
      </div>
    </div>
  )

  const renderSubsystemCard = (ss: SubsystemStatus) => {
    const cfg = SEVERITY_CONFIG[ss.severity] || SEVERITY_CONFIG.unknown
    const icon = SUBSYSTEM_ICONS[ss.key] || 'fa-gear'
    const isExpanded = expandedKey === ss.key
    const detailsStr = formatDetails(ss.details)
    return (
      <div
        key={ss.key}
        className="rounded-xl border p-2.5 cursor-pointer transition-colors hover:bg-white/[0.02]"
        style={{
          borderColor: isExpanded ? cfg.color : 'var(--panel-border)',
          background: isExpanded ? cfg.bg : undefined,
        }}
        onClick={() => toggleExpand(ss.key)}
      >
        <div className="flex items-center gap-2">
          <div className="w-6 h-6 rounded-md flex items-center justify-center flex-shrink-0" style={{ background: `${cfg.color}14` }}>
            <Icon icon={`fa-solid ${icon} text-xs`} style={{ color: cfg.color }} />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>{ss.name}</span>
            </div>
            <div className="text-2xs truncate" style={{ color: ss.error ? 'var(--danger)' : 'var(--text-muted)' }}>
              {ss.error ? ss.error : ss.message || t(SEVERITY_LABEL_KEY[ss.severity] || 'systemHealth.labelUnknown')}
            </div>
          </div>
          <div className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: cfg.color }} />
        </div>
        {isExpanded && (
          <div className="mt-2 pt-2 border-t space-y-1" style={{ borderColor: 'var(--panel-border)' }}>
            <div className="flex items-center justify-between text-2xs" style={{ color: 'var(--text-muted)' }}>
              <span>{t('systemHealth.statusLabel')}<span style={{ color: cfg.color }}>{t(SEVERITY_LABEL_KEY[ss.severity] || 'systemHealth.labelUnknown')}</span></span>
              <span>{t('systemHealth.latencyLabel')}{ss.latency_ms.toFixed(1)}ms</span>
            </div>
            <div className="flex items-center justify-between text-2xs" style={{ color: 'var(--text-muted)' }}>
              <span>{t('systemHealth.availableLabel')}<span style={{ color: ss.available ? '#34d399' : 'var(--danger)' }}>{ss.available ? t('systemHealth.yes') : t('systemHealth.no')}</span></span>
              <span>{t('systemHealth.keyLabel')}<code style={{ color: 'var(--text-dim)' }}>{ss.key}</code></span>
            </div>
            {ss.key === 'ai' ? renderAIProviders(ss.details, t) : (
              detailsStr && (
                <pre className="mt-1 p-1.5 rounded text-[9px] overflow-x-auto max-h-24" style={{ background: 'var(--panel-bg)', color: 'var(--text-dim)' }}>
                  {detailsStr}
                </pre>
              )
            )}
          </div>
        )}
      </div>
    )
  }

  const renderCollapsibleGroup = (
    items: SubsystemStatus[],
    label: string,
    open: boolean,
    onToggle: () => void,
  ) => {
    if (items.length === 0) return null
    return (
      <div>
        <button
          onClick={onToggle}
          className="flex items-center gap-2 text-xs font-medium transition-colors"
          style={{ color: 'var(--text-muted)' }}
        >
          <Icon icon={`fa-solid fa-chevron-${open ? 'down' : 'right'} text-[9px]`} />
          {label}
          <span className="px-1.5 rounded-full" style={{ background: 'var(--hover-bg)', color: 'var(--text-secondary)' }}>{items.length}</span>
        </button>
        {open && (
          <div className="grid grid-cols-2 gap-2 mt-2">
            {items.map(renderSubsystemCard)}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Overall severity banner */}
      <div
        className="rounded-xl border p-3 flex items-center gap-3"
        style={{ borderColor: `${overallCfg.color}33`, background: overallCfg.bg }}
      >
        <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: `${overallCfg.color}1a` }}>
          <Icon icon={`fa-solid ${overallCfg.icon} text-sm`} style={{ color: overallCfg.color }} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>{t('systemHealth.title')}</span>
            <span className="text-xs px-1.5 py-0.5 rounded" style={{ background: `${overallCfg.color}1a`, color: overallCfg.color }}>
              {t(SEVERITY_LABEL_KEY[snapshot.overall_severity] || 'systemHealth.labelUnknown')}
            </span>
          </div>
          <div className="flex items-center gap-2 mt-0.5 text-2xs" style={{ color: 'var(--text-muted)' }}>
            <span>{t('systemHealth.healthyFmt', { count: summary.healthy || 0 })}</span>
            <span>·</span>
            <span>{t('systemHealth.degradedFmt', { count: summary.degraded || 0 })}</span>
            <span>·</span>
            <span>{t('systemHealth.downFmt', { count: summary.down || 0 })}</span>
            <span>·</span>
            <span>{t('systemHealth.unknownFmt', { count: summary.unknown || 0 })}</span>
            <span>·</span>
            <span>{t('systemHealth.updatedAt', { time: formatTimestamp(snapshot.timestamp) })}</span>
          </div>
        </div>
        <button
          onClick={handleManualRefresh}
          disabled={refreshing}
          className="text-xs px-2 py-1 rounded border hover:opacity-80 disabled:opacity-40 flex-shrink-0"
          style={{ borderColor: 'var(--panel-border)', color: 'var(--text-secondary)' }}
          title={t('systemHealth.forceRefresh')}
        >
          <Icon icon={`fa-solid fa-rotate-right mr-1 ${refreshing ? 'animate-spin' : ''}`} />
          {refreshing ? t('systemHealth.refreshing') : t('systemHealth.refresh')}
        </button>
      </div>

      {/* Subsystem grid：需要关注常显，健康/未知折叠（P3b） */}
      {attention.length > 0 && (
        <div>
          <div className="flex items-center gap-2 text-xs font-medium mb-2" style={{ color: 'var(--warning)' }}>
            <Icon icon="fa-solid fa-triangle-exclamation text-2xs" />
            {t('systemHealth.groupAttention')}
            <span className="px-1.5 rounded-full" style={{ background: 'var(--warning-soft)', color: 'var(--warning)' }}>{attention.length}</span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {attention.map(renderSubsystemCard)}
          </div>
        </div>
      )}
      {/* #15 可观测看板：运行指标（PROACTIVE / CAPTURE 累计计数） */}
      {snapshot.counters && Object.keys(snapshot.counters).length > 0 && (
        <div>
          <div className="flex items-center gap-2 text-xs font-medium mb-2" style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-chart-line text-2xs" />
            {t('systemHealth.metricsTitle')}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <MetricCard
              icon="fa-solid fa-bolt"
              label={t('systemHealth.proactiveTriggered')}
              value={snapshot.counters['proactive.triggered'] ?? 0}
            />
            <MetricCard
              icon="fa-solid fa-inbox"
              label={t('systemHealth.captureEvents')}
              value={snapshot.counters['inbox.capture.success'] ?? 0}
            />
          </div>
        </div>
      )}
      {renderCollapsibleGroup(healthy, t('systemHealth.groupHealthy'), showHealthy, () => setShowHealthy(v => !v))}
      {renderCollapsibleGroup(unknown, t('systemHealth.groupUnknown'), showUnknown, () => setShowUnknown(v => !v))}
    </div>
  )
}

export default SystemHealthPanel
