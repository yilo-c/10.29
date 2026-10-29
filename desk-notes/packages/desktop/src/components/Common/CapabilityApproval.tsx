import React, { useState } from 'react'
import { useTranslation } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'

interface CapabilitySuggestion {
  id: string
  icon: string
  title: string
  description: string
  risk: string
  enabled: boolean
}

interface Props {
  suggestion: CapabilitySuggestion
  onDone: () => void
}

const CapabilityApproval: React.FC<Props> = ({ suggestion, onDone }) => {
  const { t } = useTranslation()
  const [approving, setApproving] = useState(false)
  const [status, setStatus] = useState<'idle' | 'approved' | 'dismissed'>('idle')

  const handleApprove = async () => {
    setApproving(true)
    try {
      const ws = getWsClient()
      await ws.call('capability.approve', { cap_id: suggestion.id })
      setStatus('approved')
    } catch (e) { /* ignore */ swallowError('CapabilityApproval:28', e) }
    finally { setApproving(false) }
  }

  const handleDismiss = async () => {
    try {
      const ws = getWsClient()
      await ws.call('capability.dismiss', { cap_id: suggestion.id })
    } catch (e) { /* ignore */ swallowError('CapabilityApproval:36', e) }
    setStatus('dismissed')
    onDone()
  }

  if (status === 'approved') {
    return (
      <div style={{
        padding: '12px 16px', borderRadius: 12,
        background: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.2)',
        marginBottom: 8,
      }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--success)', marginBottom: 4 }}>
          {t('capability.approved', { title: suggestion.title })}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-muted)' }}>
          {t('capability.approvedHint')}
        </div>
      </div>
    )
  }

  const riskLabels: Record<string, { label: string; color: string }> = {
    low: { label: t('capability.riskLow'), color: 'var(--success)' },
    medium: { label: t('capability.riskMedium'), color: 'var(--warning)' },
    high: { label: t('capability.riskHigh'), color: 'var(--danger)' },
  }
  const riskInfo = riskLabels[suggestion.risk] || { label: t('capability.riskUnknown'), color: 'var(--text-muted)' }

  return (
    <div style={{
      padding: '12px 16px', borderRadius: 12,
      background: 'rgba(96,165,250,0.08)', border: '1px solid rgba(96,165,250,0.2)',
      marginBottom: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <span style={{ fontSize: 20 }}>{suggestion.icon || '🔌'}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>
            {suggestion.title}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 6, lineHeight: 1.4 }}>
            {suggestion.description}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
            <span style={{
              fontSize: 10, padding: '2px 6px', borderRadius: 4,
              background: `${riskInfo.color}18`, color: riskInfo.color,
              fontWeight: 500,
            }}>
              {riskInfo.label}
            </span>
            {suggestion.risk === 'low' && (
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                {t('capability.riskLowHint')}
              </span>
            )}
            {suggestion.risk === 'medium' && (
              <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>
                {t('capability.riskMediumHint')}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={handleApprove}
              disabled={approving}
              style={{
                padding: '5px 14px', borderRadius: 6, border: 'none',
                fontSize: 11, fontWeight: 600, cursor: 'pointer',
                background: 'var(--accent)', color: '#fff',
                opacity: approving ? 0.6 : 1,
              }}
            >
              {approving ? t('capability.approving') : t('capability.approve')}
            </button>
            <button
              onClick={handleDismiss}
              style={{
                padding: '5px 14px', borderRadius: 6, border: 'none',
                fontSize: 11, cursor: 'pointer',
                background: 'rgba(255,255,255,0.06)',
                color: 'var(--text-muted)',
              }}
            >
              {t('capability.dismiss')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

export default CapabilityApproval
