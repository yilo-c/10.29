import React, { useState, useCallback, useRef, useEffect } from 'react'
import Icon from './Icon'

import { speak, stopSpeech, getSavedRate, saveRate } from '../../utils/speech'
import { useTranslation } from '../../i18n'

interface SpeakButtonProps {
  text: string
  lang: 'en' | 'zh'
  variant?: 'icon' | 'icon-sm'
  className?: string
}

const RATES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]

const SpeakButton: React.FC<SpeakButtonProps> = ({ text, lang, variant = 'icon', className = '' }) => {
  const { t } = useTranslation()
  const [speaking, setSpeaking] = useState(false)
  const [showRate, setShowRate] = useState(false)
  const [rate, setRate] = useState(getSavedRate)
  const rateRef = useRef(rate)
  const mountedRef = useRef(true)
  rateRef.current = rate

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      stopSpeech()
      setSpeaking(false)
    }
  }, [])

  const handleClick = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    if (speaking) {
      stopSpeech()
      setSpeaking(false)
      return
    }
    setSpeaking(true)
    speak(text, lang, rateRef.current, () => { if (mountedRef.current) setSpeaking(false) })
  }, [speaking, text, lang])

  const handleRateChange = useCallback((e: React.MouseEvent, r: number) => {
    e.stopPropagation()
    setRate(r)
    saveRate(r)
    setShowRate(false)
  }, [])

  const iconSize = variant === 'icon-sm' ? 'text-xs' : 'text-xs'
  const btnSize = variant === 'icon-sm' ? 'w-5 h-5' : 'w-6 h-6'

  return (
    <span className={`relative inline-flex items-center ${className}`}>
      <button
        onClick={handleClick}
        onContextMenu={e => { e.preventDefault(); setShowRate(v => !v) }}
        className={`${btnSize} flex items-center justify-center rounded-md transition-all shrink-0 ${
          speaking
            ? 'text-emerald-400 bg-emerald-400/10'
            : 'text-text-dim hover:text-text-secondary hover:bg-white/[0.06]'
        }`}
        title={speaking ? t('speakButton.stopReading') : t('speakButton.startReading')}
      >
        <Icon icon={`fa-solid ${speaking ? 'fa-stop' : 'fa-volume-high'} ${iconSize}`} />
      </button>

      {showRate && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setShowRate(false)} />
          <div
            className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 z-50 px-1.5 py-1 rounded-lg border flex gap-0.5"
            style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg)' }}
          >
            {RATES.map(r => (
              <button
                key={r}
                onClick={e => handleRateChange(e, r)}
                className={`px-1.5 py-0.5 rounded text-xs font-medium transition-all ${
                  rate === r ? 'text-emerald-400 bg-emerald-400/10' : 'text-text-muted hover:text-text-secondary'
                }`}
              >
                {r}x
              </button>
            ))}
          </div>
        </>
      )}
    </span>
  )
}

export default SpeakButton
