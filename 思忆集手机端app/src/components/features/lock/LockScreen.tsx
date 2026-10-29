import { useState, useCallback, useRef, useEffect } from 'react'
import { useStore, useTranslation } from '@desk-notes/shared'
import { IconX } from '../../ui'
import { isBiometricAvailable, authenticateBiometric } from '../../../lib/biometric'
import { verifyPin } from '../../../lib/pinHash'
import { secureGet, secureSet } from '../../../lib/secureStore'

const MAX_LEN = 6
// V2.1 批次4（P1-12）：防爆破参数（持久化计数，重启不清零）
const LOCK_MAX_ATTEMPTS = 5
const LOCK_COOLDOWN_SECONDS = 30
const LOCK_WINDOW_SECONDS = 600  // 10 分钟滑动窗口：窗口内达上限才锁

export default function LockScreen() {
  const { t } = useTranslation()
  const appLocked = useStore(s => s.appLocked)
  const appPin = useStore(s => s.appPin)
  const unlockApp = useStore(s => s.unlockApp)
  const lockApp = useStore(s => s.lockApp)

  const [pin, setPin] = useState('')
  const [error, setError] = useState('')
  // 防爆破计数持久化（secureStore）：重启后恢复，避免"重启清零绕过冷却"
  const [attempts, setAttempts] = useState<number>(() => {
    const v = parseInt(secureGet('siyi_lock_attempts') || '0', 10)
    return Number.isFinite(v) && v > 0 ? v : 0
  })
  const [cooldown, setCooldown] = useState<number>(() => {
    // 基于窗口起始时间戳恢复剩余冷却（重启后仍生效）
    const windowStart = parseInt(secureGet('siyi_lock_window_start') || '0', 10)
    const now = Date.now()
    if (windowStart > 0 && now - windowStart < LOCK_WINDOW_SECONDS * 1000) {
      const count = parseInt(secureGet('siyi_lock_attempts') || '0', 10)
      if (count >= LOCK_MAX_ATTEMPTS) {
        const remaining = LOCK_COOLDOWN_SECONDS - Math.floor((now - windowStart) / 1000)
        return remaining > 0 ? remaining : 0
      }
    }
    return 0
  })
  const [biometricAvailable, setBiometricAvailable] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const cooldownTimer = useRef<number | null>(null)
  const pinRef = useRef('')

  // Check biometric availability on mount
  useEffect(() => {
    if (appLocked && appPin) {
      isBiometricAvailable().then(avail => {
        setBiometricAvailable(avail && !!localStorage.getItem('siyi-biometric-cred'))
      })
    }
  }, [appLocked, appPin])

  // Cooldown countdown（到期后重置窗口计数）
  useEffect(() => {
    if (cooldown <= 0) return
    cooldownTimer.current = window.setInterval(() => {
      setCooldown(prev => {
        if (prev <= 1) {
          if (cooldownTimer.current) clearInterval(cooldownTimer.current)
          // 冷却结束 → 清空窗口计数，允许再次尝试
          setAttempts(0)
          secureSet('siyi_lock_attempts', null)
          secureSet('siyi_lock_window_start', null)
          return 0
        }
        return prev - 1
      })
    }, 1000)
    return () => {
      if (cooldownTimer.current) clearInterval(cooldownTimer.current)
    }
  }, [cooldown])

  // Auto-lock when pin is set but unlocked (on mount)
  useEffect(() => {
    if (appPin && !appLocked) {
      lockApp()
    }
    // focus hidden input for hardware keyboard on some devices
    inputRef.current?.focus()
  }, [])

  const resetLockState = useCallback(() => {
    setAttempts(0)
    secureSet('siyi_lock_attempts', null)
    secureSet('siyi_lock_window_start', null)
  }, [])

  const handleBiometric = useCallback(async () => {
    const ok = await authenticateBiometric()
    if (ok) {
      pinRef.current = ''
      setPin('')
      resetLockState()
      unlockApp()
    }
  }, [unlockApp, resetLockState])

  const handleKeyPress = useCallback(async (key: string) => {
    if (cooldown > 0) return
    setError('')

    if (key === 'backspace') {
      const next = pinRef.current.slice(0, -1)
      pinRef.current = next
      setPin(next)
      return
    }

    if (pinRef.current.length >= MAX_LEN) return

    const newPin = pinRef.current + key
    pinRef.current = newPin
    setPin(newPin)

    if (newPin.length >= 4) {
      const matches = await verifyPin(newPin, appPin)
      if (matches) {
        await new Promise(r => setTimeout(r, 80))
        pinRef.current = ''
        setPin('')
        resetLockState()
        unlockApp()
      } else if (newPin.length >= MAX_LEN) {
        setError(t('mobileLock.pinError'))
        const next = attempts + 1
        setAttempts(next)
        // 持久化防爆破计数（抗重启清零绕过）
        secureSet('siyi_lock_attempts', String(next))
        if (!secureGet('siyi_lock_window_start')) {
          secureSet('siyi_lock_window_start', String(Date.now()))
        }
        pinRef.current = ''
        setPin('')
        if (next >= LOCK_MAX_ATTEMPTS) {
          setCooldown(LOCK_COOLDOWN_SECONDS)
        }
      }
    }
  }, [appPin, attempts, cooldown, unlockApp, t, resetLockState])

  if (!appLocked || !appPin) return null

  const dots = Array.from({ length: MAX_LEN }, (_, i) => i < pin.length)

  return (
    <div style={{
      position: 'fixed', inset: 0,
      zIndex: 9999,
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      background: 'var(--bg-primary)',
      animation: 'fadeIn 0.2s ease',
    }}>
      {/* App icon / title */}
      <div style={{
        fontSize: 28, fontWeight: 700,
        color: 'var(--text-primary)',
        marginBottom: 4, letterSpacing: -0.5,
      }}>
        {t('mobileLock.appName')}
      </div>
      <div style={{
        fontSize: 'var(--text-footnote)',
        color: 'var(--text-tertiary)',
        marginBottom: 40,
      }}>
        {t('mobileLock.prompt')}
      </div>

      {/* PIN dots */}
      <div style={{
        display: 'flex', gap: 14,
        marginBottom: 32, minHeight: 24,
      }}>
        {dots.map((filled, i) => (
          <div key={i} style={{
            width: 14, height: 14,
            borderRadius: '50%',
            background: filled ? 'var(--accent)' : 'var(--bg-tertiary)',
            border: filled ? 'none' : '1.5px solid var(--border-default)',
            transition: 'all 0.1s ease',
          }} />
        ))}
      </div>

      {/* Error */}
      {error && (
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--danger)',
          marginBottom: 16, minHeight: 18,
        }}>
          {cooldown > 0 ? t('mobileLock.cooldownFmt', { cooldown }) : error}
        </div>
      )}
      {!error && <div style={{ marginBottom: 16, minHeight: 18 }} />}

      {/* Biometric unlock */}
      {biometricAvailable && (
        <button
          onClick={handleBiometric}
          style={{
            marginBottom: 20,
            padding: '10px 24px',
            border: '1px solid var(--border-default)',
            borderRadius: 'var(--radius-md)',
            background: 'var(--bg-secondary)',
            color: 'var(--accent)',
            fontSize: 'var(--text-footnote)',
            fontWeight: 500,
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            WebkitTapHighlightColor: 'transparent',
          }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M2 12h1.5a.5.5 0 0 0 .5-.5V8.5a.5.5 0 0 1 .5-.5h1" />
            <path d="M15.5 11a.5.5 0 0 0-.5.5v1" />
            <path d="M18.5 11a.5.5 0 0 0-.5.5v1" />
            <path d="M5.5 11a.5.5 0 0 0-.5.5v1" />
            <path d="M8.5 11a.5.5 0 0 0-.5.5v1" />
            <path d="M11.5 11a.5.5 0 0 0-.5.5v1" />
            <path d="M14.5 11a.5.5 0 0 0-.5.5v1" />
            <rect x="2" y="8" width="2" height="8" rx=".5" />
            <rect x="5" y="6" width="2" height="12" rx=".5" />
            <rect x="8" y="4" width="2" height="16" rx=".5" />
            <rect x="11" y="2" width="2" height="20" rx=".5" />
            <rect x="14" y="4" width="2" height="16" rx=".5" />
            <rect x="17" y="6" width="2" height="12" rx=".5" />
            <rect x="20" y="8" width="2" height="8" rx=".5" />
          </svg>
          {t('mobileLock.useBiometric')}
        </button>
      )}

      {/* Numpad */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 76px)',
        gap: 10,
      }}>
        {[1,2,3,4,5,6,7,8,9].map(n => (
          <NumpadBtn key={n} label={String(n)} onClick={() => handleKeyPress(String(n))} disabled={cooldown > 0} ariaLabel={t('mobileLock.digitFmt', { n })} />
        ))}
        <div /> {/* empty */}
        <NumpadBtn label="0" onClick={() => handleKeyPress('0')} disabled={cooldown > 0} ariaLabel={t('mobileLock.digitFmt', { n: 0 })} />
        <NumpadBtn
          label={<IconX size={18} />}
          onClick={() => handleKeyPress('backspace')}
          disabled={pin.length === 0 || cooldown > 0}
          dim
          ariaLabel={t('mobileLock.delete')}
        />
      </div>
    </div>
  )
}

function NumpadBtn({ label, onClick, disabled, dim, ariaLabel }: {
  label: React.ReactNode
  onClick: () => void
  disabled?: boolean
  dim?: boolean
  ariaLabel?: string
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      style={{
        width: 76, height: 76,
        borderRadius: '50%',
        border: 'none',
        background: disabled ? 'var(--bg-tertiary)' : dim ? 'transparent' : 'var(--bg-secondary)',
        color: dim ? 'var(--text-tertiary)' : 'var(--text-primary)',
        fontSize: 28, fontWeight: 400,
        cursor: disabled ? 'default' : 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'background 0.1s ease',
        WebkitTapHighlightColor: 'transparent',
      }}
    >
      {label}
    </button>
  )
}
