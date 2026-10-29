import { useState, useCallback, useEffect, useRef } from 'react'
import { useStore } from '@desk-notes/shared'
import { IconX } from '../../ui'
import { isBiometricAvailable, registerBiometric, removeBiometricCredential } from '../../../lib/biometric'
import { haptic } from '../../../lib/haptic'
import { linkBtnStyle } from '../../../lib/styles'
import { hashPin, verifyPin } from '../../../lib/pinHash'
import { SectionTitle, SettingsCard, SettingsRow } from './SettingsShared'

function NumBtn({ label, onClick, dim, ariaLabel }: { label: React.ReactNode; onClick: () => void; dim?: boolean; ariaLabel?: string }) {
  return (
    <button
      onClick={onClick}
      aria-label={ariaLabel}
      style={{
        width: '100%', aspectRatio: '1',
        borderRadius: '50%', border: 'none',
        background: dim ? 'transparent' : 'var(--bg-tertiary)',
        color: dim ? 'var(--text-tertiary)' : 'var(--text-primary)',
        fontSize: 24, fontWeight: 400,
        cursor: 'pointer', display: 'flex', alignItems: 'center',
        justifyContent: 'center', transition: 'background 0.1s ease',
        WebkitTapHighlightColor: 'transparent',
      }}
    >
      {label}
    </button>
  )
}

export default function SettingsSecurity() {
  const appPin = useStore(s => s.appPin)
  const setAppPin = useStore(s => s.setAppPin)
  const lockApp = useStore(s => s.lockApp)
  const [pinMode, setPinMode] = useState<'none' | 'set' | 'confirm' | 'change' | 'remove'>('none')
  const [pinInput, setPinInput] = useState('')
  const [pinConfirm, setPinConfirm] = useState('')
  const [pinError, setPinError] = useState('')
  const pinInputRef = useRef('')
  const [biometricAvailable, setBiometricAvailable] = useState(false)
  const [biometricRegistered, setBiometricRegistered] = useState(false)

  useEffect(() => {
    isBiometricAvailable().then(setBiometricAvailable)
    if (appPin && localStorage.getItem('siyi-biometric-cred')) {
      setBiometricRegistered(true)
    }
  }, [appPin])

  const handleStartSetPin = () => {
    setPinMode('set')
    setPinInput('')
    setPinConfirm('')
    setPinError('')
  }

  const handlePinDigit = useCallback(async (d: string) => {
    const current = pinInputRef.current
    setPinError('')
    if (pinMode === 'set' || pinMode === 'change') {
      if (current.length >= 6) return
      const next = current + d
      pinInputRef.current = next
      setPinInput(next)
      if (next.length >= 4 && next.length <= 6) {
        if (next.length === 6) {
          setPinMode('confirm')
          setPinConfirm(next)
          pinInputRef.current = ''
          setPinInput('')
        }
      }
    } else if (pinMode === 'confirm') {
      if (current.length >= 6) return
      const next = current + d
      pinInputRef.current = next
      setPinInput(next)
      if (next.length >= 4) {
        if (next === pinConfirm) {
          const hash = await hashPin(next)
          setAppPin(hash)
          if (biometricAvailable) {
            const ok = await registerBiometric(next)
            setBiometricRegistered(ok)
          }
          setPinMode('none')
          pinInputRef.current = ''
          setPinInput('')
          setPinConfirm('')
        } else if (next.length >= 6 || (next.length >= 4 && next.length < 6 && next.length === pinConfirm.length)) {
          setPinError('两次输入的 PIN 不一致')
          pinInputRef.current = ''
          setPinInput('')
        }
      }
    } else if (pinMode === 'remove') {
      if (current.length >= 6) return
      const next = current + d
      pinInputRef.current = next
      setPinInput(next)
      if (next.length >= 4) {
        const matches = await verifyPin(next, appPin || '')
        if (matches) {
          setAppPin(null)
          setPinMode('none')
          removeBiometricCredential()
          setBiometricRegistered(false)
          pinInputRef.current = ''
          setPinInput('')
        } else if (next.length >= 6) {
          setPinError('原 PIN 错误')
          pinInputRef.current = ''
          setPinInput('')
        }
      }
    }
  }, [pinMode, pinConfirm, appPin, setAppPin])

  const handlePinBackspace = useCallback(() => {
    setPinInput(prev => prev.slice(0, -1))
    setPinError('')
  }, [])

  const handleCancelPin = useCallback(() => {
    setPinMode('none')
    pinInputRef.current = ''
    setPinInput('')
    setPinConfirm('')
    setPinError('')
  }, [])

  return (
    <div style={{ marginTop: 28 }}>
      <SectionTitle text="安全锁" />
      {pinMode === 'none' ? (
        <SettingsCard>
          <SettingsRow label="锁屏 PIN" last={!appPin}>
            {appPin ? (
              <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--tone-smooth)' }}>已启用</div>
            ) : (
              <button onClick={handleStartSetPin} style={linkBtnStyle}>设置 PIN</button>
            )}
          </SettingsRow>
          {appPin && (
            <>
              {biometricAvailable && (
                <SettingsRow label="生物识别">
                  <button
                    onClick={async () => {
                      if (biometricRegistered) {
                        removeBiometricCredential()
                        setBiometricRegistered(false)
                      } else {
                        const ok = await registerBiometric('siyi-device')
                        setBiometricRegistered(ok)
                      }
                    }}
                    style={{ ...linkBtnStyle, color: biometricRegistered ? 'var(--tone-smooth)' : 'var(--text-tertiary)' }}
                  >
                    {biometricRegistered ? '已启用' : '启用 Face ID/指纹'}
                  </button>
                </SettingsRow>
              )}
              <SettingsRow label="更改 PIN">
                <button onClick={() => { setPinMode('change'); setPinInput(''); setPinError(''); setPinConfirm('') }} style={linkBtnStyle}>更改</button>
              </SettingsRow>
              <SettingsRow label="移除 PIN">
                <button onClick={() => { setPinMode('remove'); setPinInput(''); setPinError('') }} style={{ ...linkBtnStyle, color: 'var(--danger)' }}>移除</button>
              </SettingsRow>
              <SettingsRow label="立即锁定" last>
                <button onClick={() => { haptic.medium(); lockApp() }} style={{ ...linkBtnStyle, color: 'var(--accent)' }}>锁定</button>
              </SettingsRow>
            </>
          )}
        </SettingsCard>
      ) : (
        <SettingsCard>
          <div style={{ padding: '16px 16px 20px' }}>
            <div style={{ fontSize: 'var(--text-body)', fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
              {pinMode === 'set' && '设置 PIN（4-6位数字）'}
              {pinMode === 'confirm' && '再次输入以确认'}
              {pinMode === 'change' && '输入新 PIN（4-6位数字）'}
              {pinMode === 'remove' && '输入当前 PIN 以移除'}
            </div>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'center', margin: '20px 0 12px' }}>
              {Array.from({ length: 6 }, (_, i) => (
                <div key={i} style={{
                  width: 12, height: 12, borderRadius: '50%',
                  background: i < pinInput.length ? 'var(--accent)' : 'var(--bg-tertiary)',
                  border: i < pinInput.length ? 'none' : '1.5px solid var(--border-default)',
                  transition: 'all 0.1s ease',
                }} />
              ))}
            </div>
            {pinError && (
              <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--danger)', textAlign: 'center', marginBottom: 12 }}>
                {pinError}
              </div>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, maxWidth: 240, margin: '0 auto' }}>
              {[1, 2, 3, 4, 5, 6, 7, 8, 9].map(n => (
                <NumBtn key={n} label={String(n)} onClick={() => handlePinDigit(String(n))} ariaLabel={`数字 ${n}`} />
              ))}
              <div />
              <NumBtn label="0" onClick={() => handlePinDigit('0')} ariaLabel="数字 0" />
              <NumBtn label={<IconX size={16} />} onClick={handlePinBackspace} dim ariaLabel="删除" />
            </div>
            <div style={{ textAlign: 'center', marginTop: 12 }}>
              <button onClick={handleCancelPin} style={{
                background: 'none', border: 'none',
                color: 'var(--text-tertiary)', cursor: 'pointer',
                fontSize: 'var(--text-footnote)', padding: '8px 16px',
              }}>取消</button>
            </div>
          </div>
        </SettingsCard>
      )}
    </div>
  )
}
