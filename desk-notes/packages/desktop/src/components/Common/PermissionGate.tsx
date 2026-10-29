import React, { useCallback } from 'react'
import Icon from './Icon'

import { useStore } from '../../store/useStore'
import type { PermissionRequest } from '@desk-notes/shared/store/slices/permissionSlice'
import { useTranslation } from '../../i18n'

const RISK_COLORS: Record<string, { border: string; badge: string; badgeBg: string }> = {
  low: { border: 'rgba(96,165,250,0.2)', badge: 'var(--brand)', badgeBg: 'rgba(96,165,250,0.15)' },
  medium: { border: 'rgba(251,191,36,0.25)', badge: 'var(--warning)', badgeBg: 'rgba(251,191,36,0.15)' },
  high: { border: 'rgba(248,113,113,0.3)', badge: 'var(--danger)', badgeBg: 'rgba(248,113,113,0.15)' },
}

interface PermissionGateProps {
  mode?: 'card' | 'modal' | 'banner'
}

const PermissionCard: React.FC<{
  req: PermissionRequest
  onResolve: (id: string, decision: 'allow' | 'deny') => void
  onRemember: (group: string, decision: 'allow' | 'deny') => void
}> = ({ req, onResolve, onRemember }) => {
  const { t } = useTranslation()
  const colors = RISK_COLORS[req.risk] || RISK_COLORS.medium
  const riskLabels: Record<string, string> = {
    low: t('permissionGate.riskLow'),
    medium: t('permissionGate.riskMedium'),
    high: t('permissionGate.riskHigh'),
  }
  const [remember, setRemember] = React.useState(false)

  return (
    <div
      className="rounded-xl px-3 py-2.5 shadow-lg border max-w-[360px]"
      style={{
        background: 'var(--panel-bg-solid)',
        borderColor: colors.border,
      }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 mb-2">
        <span
          className="text-xs font-medium px-1.5 py-0.5 rounded"
          style={{ color: colors.badge, background: colors.badgeBg }}
        >
          <Icon icon={`fa-solid ${req.icon || 'fa-shield'} mr-1`} />
          {riskLabels[req.risk] || req.risk} · {req.source}
        </span>
      </div>

      {/* Action description */}
      <div className="text-xs leading-relaxed mb-1.5" style={{ color: 'var(--text-primary)' }}>
        <span className="font-medium">{req.actionLabel}</span>
        {req.targetLabel && (
          <span className="text-text-secondary"> — {req.targetLabel}</span>
        )}
      </div>

      {req.description && (
        <div className="text-xs mb-2 leading-relaxed" style={{ color: 'var(--text-muted)' }}>
          {req.description}
        </div>
      )}

      {/* Remember checkbox */}
      <label className="flex items-center gap-1.5 mb-2 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={remember}
          onChange={(e) => setRemember(e.target.checked)}
          className="w-3 h-3 rounded border-white/20"
        />
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
{t('permissionGate.rememberDecision')}
        </span>
      </label>

      {/* Actions */}
      <div className="flex items-center gap-1.5 justify-end border-t pt-1.5" style={{ borderColor: 'var(--separator)' }}>
        <button
          onClick={() => {
            onResolve(req.id, 'deny')
            if (remember) onRemember(req.group, 'deny')
          }}
          className="px-2.5 py-1 rounded text-xs transition-all hover:bg-white/[0.06]"
          style={{ color: 'var(--text-muted)' }}
        >
{t('permissionGate.deny')}
        </button>
        <button
          onClick={() => {
            onResolve(req.id, 'allow')
            if (remember) onRemember(req.group, 'allow')
          }}
          className="px-2.5 py-1 rounded text-xs transition-all"
          style={{
            color: colors.badge,
            background: colors.badgeBg,
          }}
        >
{t('permissionGate.allow')}
        </button>
      </div>
    </div>
  )
}

const PermissionGate: React.FC<PermissionGateProps> = ({ mode = 'card' }) => {
  const queue = useStore(s => s.permissionQueue)
  const resolvePermission = useStore(s => s.resolvePermission)
  const rememberPermissionDecision = useStore(s => s.rememberPermissionDecision)

  const handleResolve = useCallback((id: string, decision: 'allow' | 'deny') => {
    resolvePermission(id, decision)
  }, [resolvePermission])

  const handleRemember = useCallback((group: string, decision: 'allow' | 'deny') => {
    rememberPermissionDecision(group, decision)
  }, [rememberPermissionDecision])

  if (queue.length === 0) return null

  if (mode === 'modal') {
    // Modal overlay — shows on top of everything
    const req = queue[0]
    return (
      <div className="fixed inset-0 z-[99999] flex items-center justify-center bg-black/40 backdrop-blur-sm">
        <div className="p-4">
          <PermissionCard
            req={req}
            onResolve={handleResolve}
            onRemember={handleRemember}
          />
        </div>
      </div>
    )
  }

  if (mode === 'banner') {
    // Banner — sticks below a toolbar or header
    return (
      <div className="flex flex-col gap-1.5 px-3 py-2">
        {queue.map(req => (
          <PermissionCard
            key={req.id}
            req={req}
            onResolve={handleResolve}
            onRemember={handleRemember}
          />
        ))}
      </div>
    )
  }

  // card (default) — floating stack in bottom-right
  return (
    <div className="fixed bottom-4 right-4 z-[99999] flex flex-col gap-2">
      {queue.map(req => (
        <PermissionCard
          key={req.id}
          req={req}
          onResolve={handleResolve}
          onRemember={handleRemember}
        />
      ))}
    </div>
  )
}

export { PermissionCard }
export default PermissionGate
