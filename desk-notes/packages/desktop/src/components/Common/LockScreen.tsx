import React, { useState, useRef, useEffect, useCallback } from 'react'
import Icon from './Icon'

import { motion, AnimatePresence } from 'framer-motion'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'

/**
 * Simple non-cryptographic hash for PIN verification.
 * Purpose: prevent casual snooping, not withstand brute-force.
 * For stronger protection, use a proper KDF with salt.
 */
function hashPin(pin: string): string {
  let hash = 0
  for (let i = 0; i < pin.length; i++) {
    hash = ((hash << 5) - hash) + pin.charCodeAt(i)
    hash |= 0
  }
  return 'h' + Math.abs(hash).toString(36)
}

const PIN_MAX_LEN = 6
const PIN_MIN_LEN = 4

type Step = 'enter' | 'setup' | 'confirm' | 'change-pin'

const LockScreen: React.FC = () => {
  const { t } = useTranslation()
  const appPin = useStore(s => s.appPin)
  const setAppPin = useStore(s => s.setAppPin)
  const unlockApp = useStore(s => s.unlockApp)
  const [pin, setPin] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [step, setStep] = useState<Step>(appPin ? 'enter' : 'setup')
  const [setupPin, setSetupPin] = useState('')
  const [setupConfirm, setSetupConfirm] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([])

  // If user re-opens without a PIN, go to setup
  useEffect(() => {
    if (!appPin && step !== 'setup' && step !== 'change-pin') {
      setStep('setup')
    }
  }, [appPin, step])

  useEffect(() => {
    inputRef.current?.focus()
  }, [step])

  useEffect(() => {
    return () => {
      for (const t of timersRef.current) clearTimeout(t)
      timersRef.current = []
    }
  }, [])

  /** Cancel any pending timer so stale callbacks don't override user input */
  const cancelTimer = useCallback(() => {
    for (const t of timersRef.current) clearTimeout(t)
    timersRef.current = []
  }, [])

  const addTimer = (fn: () => void, ms: number) => {
    cancelTimer()
    const id = setTimeout(() => {
      timersRef.current = timersRef.current.filter(t => t !== id)
      fn()
    }, ms)
    timersRef.current.push(id)
  }

  const clearError = () => setError(null)

  const handleDigit = (d: string) => {
    clearError()

    if (step === 'change-pin') {
      // Same as setup but starting fresh
      if (setupPin.length < PIN_MAX_LEN) {
        const next = setupPin + d
        setSetupPin(next)
        if (next.length >= PIN_MIN_LEN) {
          addTimer(() => { setStep('confirm'); setSetupConfirm('') }, 200)
        }
      }
      return
    }

    if (step === 'setup') {
      if (setupPin.length < PIN_MAX_LEN) {
        const next = setupPin + d
        setSetupPin(next)
        if (next.length >= PIN_MIN_LEN) {
          addTimer(() => { setStep('confirm'); setSetupConfirm('') }, 200)
        }
      }
      return
    }

    if (step === 'confirm') {
      if (setupConfirm.length < PIN_MAX_LEN) {
        const next = setupConfirm + d
        setSetupConfirm(next)
        if (next.length >= PIN_MIN_LEN) {
          if (next === setupPin) {
            setAppPin(hashPin(next))
            addTimer(() => {
              setStep('enter')
              setPin('')
              unlockApp()
            }, 150)
          } else {
            setError(t('lock.pinMismatch'))
            addTimer(() => {
              setStep(appPin ? 'change-pin' : 'setup')
              setSetupPin('')
              setSetupConfirm('')
              setError(null)
            }, 800)
          }
        }
      }
      return
    }

    // Enter step — unlock
    if (pin.length < PIN_MAX_LEN) {
      const next = pin + d
      setPin(next)
      if (next.length >= PIN_MIN_LEN && appPin) {
        if (hashPin(next) === appPin) {
          addTimer(() => unlockApp(), 150)
        } else {
          setError(t('lock.wrongPin'))
          addTimer(() => { setPin(''); setError(null) }, 600)
        }
      }
    }
  }

  const handleDelete = () => {
    cancelTimer()
    clearError()
    if (step === 'setup' || step === 'change-pin') {
      setSetupPin(setupPin.slice(0, -1))
    } else if (step === 'confirm') {
      setSetupConfirm(setupConfirm.slice(0, -1))
    } else {
      setPin(pin.slice(0, -1))
    }
  }

  const currentLength = step === 'enter' ? pin.length
    : step === 'setup' || step === 'change-pin' ? setupPin.length
    : setupConfirm.length

  const title = step === 'setup' || step === 'change-pin' ? t('lock.setupPin')
    : step === 'confirm' ? t('lock.confirmPin')
    : t('lock.enterPin')

  // Show all dot positions (PIN_MAX_LEN) so users entering 5-6 digit PINs get full visual feedback
  const dotLen = PIN_MAX_LEN

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      className="fixed inset-0 z-[999999] flex items-center justify-center"
      style={{ background: 'var(--lock-bg, rgba(10,10,18,0.98))', backdropFilter: 'blur(30px)' }}
    >
      <div className="flex flex-col items-center gap-6">
        {/* App icon + title */}
        <div className="flex flex-col items-center gap-2">
          <div className="w-14 h-14 rounded-2xl flex items-center justify-center"
            style={{ background: 'rgba(96,165,250,0.15)', border: '1px solid rgba(96,165,250,0.2)' }}>
            <Icon icon="fa-regular fa-note-sticky text-2xl" style={{ color: 'rgba(96,165,250,0.8)' }} />
          </div>
          <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
            {t('app.title')}
          </span>
        </div>

        {/* Title */}
        <span className="text-xs" style={{ color: 'var(--text-muted)' }}>
          {title}
        </span>

        {/* Dots */}
        <div className="flex items-center gap-2.5 h-6">
          {Array.from({ length: dotLen }).map((_, i) => {
            const filled = i < currentLength
            return (
              <motion.div
                key={i}
                animate={{ scale: filled ? 1.2 : 1 }}
                className="w-3 h-3 rounded-full transition-all"
                style={{
                  background: filled ? 'rgba(96,165,250,0.7)' : 'rgba(255,255,255,0.08)',
                  border: filled ? 'none' : '1px solid rgba(255,255,255,0.1)',
                }}
              />
            )
          })}
        </div>

        {/* Error */}
        <AnimatePresence mode="wait">
          {error && (
            <motion.span
              key={error}
              initial={{ opacity: 0, y: -4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              className="text-xs text-red-400"
            >
              {error}
            </motion.span>
          )}
        </AnimatePresence>

        {/* Numpad */}
        <div className="grid grid-cols-3 gap-3 mt-2">
          {[1, 2, 3, 4, 5, 6, 7, 8, 9].map(n => (
            <button key={n} onClick={() => handleDigit(String(n))}
              className="w-14 h-14 rounded-2xl text-base font-light transition-all active:scale-90"
              style={{
                background: 'rgba(255,255,255,0.04)',
                color: 'var(--text-primary)',
                border: '1px solid rgba(255,255,255,0.04)',
              }}>
              {n}
            </button>
          ))}
          <div />
          <button onClick={() => handleDigit('0')}
            className="w-14 h-14 rounded-2xl text-base font-light transition-all active:scale-90"
            style={{
              background: 'rgba(255,255,255,0.04)',
              color: 'var(--text-primary)',
              border: '1px solid rgba(255,255,255,0.04)',
            }}>
            0
          </button>
          <button onClick={handleDelete}
            className="w-14 h-14 rounded-2xl flex items-center justify-center transition-all active:scale-90"
            style={{ color: 'var(--text-muted)' }}>
            <Icon icon="fa-solid fa-delete-left text-lg" />
          </button>
        </div>

        {/* Bottom buttons */}
        <div className="flex items-center gap-3">
          {step === 'enter' && (
            <>
              {/* Skip — unlock without PIN (only when no PIN set, normally hidden) */}
              {!appPin && (
                <button onClick={() => unlockApp()}
                  className="text-xs px-3 py-1.5 rounded-lg transition-all"
                  style={{ color: 'var(--text-muted)' }}>
                  {t('lock.skip')}
                </button>
              )}
              {/* Change PIN */}
              <button onClick={() => {
                setStep('change-pin')
                setSetupPin('')
                setSetupConfirm('')
                clearError()
              }}
                className="text-xs px-3 py-1.5 rounded-lg transition-all"
                style={{ color: 'var(--text-muted)' }}>
                {t('lock.changePin')}
              </button>
              {/* Remove PIN */}
              <button onClick={() => {
                setAppPin(null)
                unlockApp()
              }}
                className="text-xs px-3 py-1.5 rounded-lg transition-all"
                style={{ color: 'var(--text-muted)' }}>
                {t('lock.removePin')}
              </button>
            </>
          )}
          {(step === 'setup' || step === 'change-pin') && !appPin && (
            <button onClick={() => { setAppPin(null); unlockApp() }}
              className="text-xs px-3 py-1.5 rounded-lg transition-all"
              style={{ color: 'var(--text-muted)' }}>
              {t('lock.skip')}
            </button>
          )}
          {step === 'confirm' && (
            <button onClick={() => {
              setStep(appPin ? 'change-pin' : 'setup')
              setSetupPin('')
              setSetupConfirm('')
              clearError()
            }}
              className="text-xs px-3 py-1.5 rounded-lg transition-all"
              style={{ color: 'var(--text-muted)' }}>
              {t('common.cancel')}
            </button>
          )}
        </div>
      </div>
    </motion.div>
  )
}

export default LockScreen
