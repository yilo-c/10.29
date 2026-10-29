import React, { useEffect, useState } from 'react'
import Icon from './Icon'

import { motion } from 'framer-motion'
import { useTranslation } from '@desk-notes/shared'

// ── Types mirroring Python CostTracker output ────────────────────────────

interface TierBreakdown {
  cost: number
  count: number
}

interface DailySummary {
  date: string
  total_cost: number
  total_calls: number
  tier_breakdown: Record<string, TierBreakdown>
  vs_pure_cloud_saving?: {
    pure_cloud_estimated: number
    actual_cost: number
    savings_percent: number
  }
}

interface MonthlySummary {
  total_cost: number
  total_calls: number
  days_tracked?: number
}

interface UsageStats {
  snapshot: {
    today_cost: number
    month_cost: number
    total_calls: number
    tier_breakdown: Record<string, number>
  }
  daily: DailySummary
  monthly: MonthlySummary
}

// ── Helpers ─────────────────────────────────────────────────────────────

async function callService<T>(
  service: string,
  method: string,
  params: Record<string, any> = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const bridge = window.bridge
    if (!bridge?.call_service) {
      reject(new Error('bridge not available'))
      return
    }
    bridge.call_service(service, method, params, (result: { ok: boolean; data: T; error: string | null }) => {
      if (result.ok) resolve(result.data)
      else reject(new Error(result.error || 'Unknown error'))
    })
  })
}

// ── Constants ───────────────────────────────────────────────────────────

const TIER_KEYS = ['local', 'judge', 'mid_cloud', 'high_cloud'] as const
const TIER_COLORS: Record<string, string> = {
  local: 'var(--success)',
  judge: 'var(--brand)',
  mid_cloud: 'var(--warning)',
  high_cloud: 'var(--danger)',
}

// ── Component ───────────────────────────────────────────────────────────

interface Props {
  onClose: () => void
}

const AIActivityPanel: React.FC<Props> = ({ onClose }) => {
  const { t } = useTranslation()
  const tierMeta = React.useMemo(() => {
    const m: Record<string, { label: string; color: string }> = {}
    for (const k of TIER_KEYS) {
      m[k] = { label: t(`aiUsage.tier.${k}`), color: TIER_COLORS[k] }
    }
    return m
  }, [t])
  const [stats, setStats] = useState<UsageStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const bridgeAvailable = !!window.bridge?.call_service

  useEffect(() => {
    if (!bridgeAvailable) {
      setLoading(false)
      return
    }
    callService<UsageStats>('assistant', 'get_usage_stats')
      .then(data => { setStats(data); setLoading(false) })
      .catch(err => { setError(err.message); setLoading(false) })
  }, [bridgeAvailable])

  const fmt = (v: number) => `$${v.toFixed(4)}`


  // ESC 关闭（2026-10-16 E2E sweep 缺陷修复：modal 家族共性无 ESC）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onClose])
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.15 }}
      className="fixed inset-0 z-[99999] flex items-center justify-center"
      role="dialog"
      aria-modal="true"
      aria-label={t('aiUsage.title')}
      style={{ background: 'rgba(0,0,0,0.4)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div
        className="border rounded-xl shadow-2xl overflow-hidden w-[400px] max-h-[80vh]"
        style={{
          background: 'var(--panel-bg-solid)',
          borderColor: 'var(--panel-border-accent)',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 h-10 border-b" style={{ borderColor: 'var(--separator)' }}>
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-chart-simple text-text-muted text-xs" />
            <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
              {t('aiUsage.title')}
            </span>
          </div>
          <button onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text-secondary hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs">
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>

        {/* Content */}
        <div className="overflow-y-auto max-h-[60vh] px-4 py-3 space-y-4">
          {!bridgeAvailable ? (
            <div className="text-center py-8">
              <Icon icon="fa-solid fa-plug text-2xl text-text-dim mb-3" />
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {t('aiUsage.notConnected')}
              </p>
              <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                {t('aiUsage.notConnectedHint')}
              </p>
            </div>
          ) : loading ? (
            <div className="flex items-center justify-center py-8">
              <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
            </div>
          ) : error ? (
            <div className="text-center py-8">
              <Icon icon="fa-solid fa-triangle-exclamation text-lg text-red-400/60 mb-2" />
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {t('aiUsage.loadFailed', { error: error ?? '' })}
              </p>
            </div>
          ) : stats ? (
            <>
              {/* Summary cards */}
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-lg p-3 text-center" style={{ background: 'var(--hover-bg)' }}>
                  <div className="text-xl font-semibold font-mono" style={{ color: 'var(--text-primary)' }}>
                    {fmt(stats.snapshot.today_cost)}
                  </div>
                  <div className="text-2xs mt-1" style={{ color: 'var(--text-muted)' }}>{t('aiUsage.todayCost')}</div>
                </div>
                <div className="rounded-lg p-3 text-center" style={{ background: 'var(--hover-bg)' }}>
                  <div className="text-xl font-semibold font-mono" style={{ color: 'var(--text-primary)' }}>
                    {fmt(stats.snapshot.month_cost)}
                  </div>
                  <div className="text-2xs mt-1" style={{ color: 'var(--text-muted)' }}>{t('aiUsage.monthCost')}</div>
                </div>
                <div className="rounded-lg p-3 text-center" style={{ background: 'var(--hover-bg)' }}>
                  <div className="text-xl font-semibold font-mono" style={{ color: 'var(--text-primary)' }}>
                    {stats.snapshot.total_calls}
                  </div>
                  <div className="text-2xs mt-1" style={{ color: 'var(--text-muted)' }}>{t('aiUsage.totalCalls')}</div>
                </div>
              </div>

              {/* Tier breakdown */}
              <div>
                <div className="text-xs font-medium uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>
                  {t('aiUsage.tierBreakdown')}
                </div>
                <div className="space-y-1">
                  {Object.entries(stats.snapshot.tier_breakdown).map(([tier, cost]) => {
                    const meta = tierMeta[tier] || { label: tier, color: 'var(--text-muted)' }
                    const maxCost = Math.max(...Object.values(stats.snapshot.tier_breakdown), 0.001)
                    const pct = (cost / maxCost) * 100
                    return (
                      <div key={tier} className="py-1.5 px-2 rounded-lg" style={{ background: 'var(--hover-bg)' }}>
                        <div className="flex items-center justify-between mb-1">
                          <div className="flex items-center gap-1.5">
                            <span className="w-1.5 h-1.5 rounded-full" style={{ background: meta.color }} />
                            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{meta.label}</span>
                          </div>
                          <span className="text-xs font-mono" style={{ color: 'var(--text-primary)' }}>
                            {fmt(cost)}
                          </span>
                        </div>
                        <div className="w-full h-1 rounded-full overflow-hidden" style={{ background: 'var(--input-bg)' }}>
                          <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: meta.color }} />
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>

              {/* Daily detail */}
              {stats.daily.total_calls > 0 && (
                <div>
                  <div className="text-xs font-medium uppercase tracking-wider mb-2" style={{ color: 'var(--text-muted)' }}>
                    {t('aiUsage.todayDetail')}
                  </div>
                  <div className="space-y-1">
                    {Object.entries(stats.daily.tier_breakdown).map(([tier, info]) => {
                      const meta = tierMeta[tier] || { label: tier, color: 'var(--text-muted)' }
                      const row = info as TierBreakdown
                      return (
                        <div key={tier} className="flex items-center justify-between py-2 px-3 rounded-lg" style={{ background: 'var(--hover-bg)' }}>
                          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{meta.label}</span>
                          <span className="text-xs font-mono font-medium" style={{ color: 'var(--text-primary)' }}>
                            {t('aiUsage.callsCostFmt', { count: row.count, cost: fmt(row.cost) })}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}

              {/* Cloud savings */}
              {stats.daily.vs_pure_cloud_saving && (() => {
                const saving = stats.daily.vs_pure_cloud_saving!
                return (
                  <div className="rounded-lg p-3 text-center" style={{ background: 'var(--hover-bg)' }}>
                    <div className="text-2xs mb-1" style={{ color: 'var(--text-muted)' }}>{t('aiUsage.savingsLabel')}</div>
                    <div className="text-lg font-semibold font-mono" style={{ color: 'var(--success)' }}>
                      {saving.savings_percent}%
                    </div>
                  </div>
                )
              })()}
            </>
          ) : null}
        </div>

        {/* Footer */}
        <div className="px-4 py-2 border-t text-center" style={{ borderColor: 'var(--separator)' }}>
          <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>
            {t('aiUsage.footer')}
          </span>
        </div>
      </div>
    </motion.div>
  )
}

export default AIActivityPanel
