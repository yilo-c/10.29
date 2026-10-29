import { useState, useEffect } from 'react'
import { tokenManager } from '../../../lib/tokenManager'
import { haptic } from '../../../lib/haptic'
import DevicePairingSheet from './DevicePairingSheet'

interface SettingsDeviceCardProps {
  onPairingChange: () => void
}

export default function SettingsDeviceCard({ onPairingChange }: SettingsDeviceCardProps) {
  const [showPairing, setShowPairing] = useState(false)
  const [, setTick] = useState(0)

  // Re-render when pairing state changes
  useEffect(() => {
    const handler = () => setTick(t => t + 1)
    window.addEventListener('storage', handler)
    return () => window.removeEventListener('storage', handler)
  }, [])

  const isPaired = tokenManager.isPaired

  return (
    <>
      <div style={{ marginTop: 28 }}>
        <div style={sectionTitleStyle}>设备与服务</div>
        <div style={cardStyle}>
          <div
            onClick={() => { haptic.light(); setShowPairing(true) }}
            style={rowStyle}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 'var(--text-footnote)', fontWeight: 600, marginBottom: 2 }}>
                {isPaired ? '已配对的设备' : '配对桌面端'}
              </div>
              <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
                {isPaired
                  ? `${tokenManager.deviceName ?? ''} · ${tokenManager.baseUrl}`
                  : '连接 思忆集 桌面端以同步数据'
                }
              </div>
            </div>
            <div style={{
              width: 8, height: 8, borderRadius: '50%',
              background: isPaired ? '#34C759' : 'var(--text-disabled)',
              flexShrink: 0,
            }} />
          </div>
        </div>
      </div>

      <DevicePairingSheet
        open={showPairing}
        onClose={() => setShowPairing(false)}
        onPairingChange={() => { onPairingChange(); setTick(t => t + 1) }}
      />
    </>
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

const rowStyle: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: 12,
  padding: '14px 16px',
  cursor: 'pointer',
  WebkitTapHighlightColor: 'transparent',
}
