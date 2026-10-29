import React, { useEffect, useState } from 'react'
import { useStore } from '../../store/useStore'
import { hexToRgb } from '../../utils/helpers'
import { initFileStore } from '../../utils/fileStore'
import { useTranslation } from '../../i18n'
import HeaderBar from '../MainWindow/HeaderBar'
import ChatModule from './ChatModule'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

const StandaloneChatView: React.FC = () => {
  const panel = useStore(s => s.panel)
  const backgroundMode = useStore(s => s.backgroundMode)
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await initFileStore()
        await useStore.getState().loadNotesFromDisk()
        if (cancelled) return
        setLoading(false)
      } catch (e: unknown) {
        console.error('[StandaloneChatView] init failed:', e)
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  // ── Background calculation (matches MainWindow) ──
  let bgColor: string
  let borderVal: string
  let boxShadowVal: string | undefined

  if (backgroundMode === 'pure-white') {
    const rgb = panel.color ? hexToRgb(panel.color) : null
    if (rgb) {
      bgColor = `rgba(${Math.round(250 + (rgb.r - 250) * 0.3)},${Math.round(252 + (rgb.g - 252) * 0.3)},${Math.round(252 + (rgb.b - 252) * 0.3)},0.95)`
    } else { bgColor = 'rgba(250,250,252,0.95)' }
    borderVal = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(0,0,0,0.06)'
    boxShadowVal = panel.color
      ? `0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04), 0 0 30px ${panel.color}12`
      : `0 8px 32px rgba(0,0,0,0.08), 0 0 0 1px rgba(0,0,0,0.04)`
  } else if (backgroundMode === 'pure-black') {
    const rgb = panel.color ? hexToRgb(panel.color) : null
    if (rgb) {
      bgColor = `rgba(${Math.round(14 + (rgb.r - 14) * 0.15)},${Math.round(14 + (rgb.g - 14) * 0.15)},${Math.round(20 + (rgb.b - 20) * 0.15)},0.98)`
    } else { bgColor = 'rgba(14,14,20,0.98)' }
    borderVal = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(255,255,255,0.04)'
    boxShadowVal = panel.color
      ? `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03), 0 0 30px ${panel.color}12`
      : `0 8px 32px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.03)`
  } else {
    const baseAlpha = isElectron ? 0.65 : 0.72
    const bgAlpha = baseAlpha * panel.opacity
    const rgb = panel.color ? hexToRgb(panel.color) : null
    if (rgb) {
      const tint = 0.30 * panel.opacity
      bgColor = `rgba(${Math.round(22 + (rgb.r - 22) * tint)},${Math.round(22 + (rgb.g - 22) * tint)},${Math.round(32 + (rgb.b - 32) * tint)},${bgAlpha.toFixed(2)})`
    } else { bgColor = `rgba(22,22,32,${bgAlpha.toFixed(2)})` }
    borderVal = panel.color ? `1px solid ${panel.color}40` : '1px solid rgba(255,255,255,0.05)'
    boxShadowVal = panel.color ? `0 8px 40px rgba(0,0,0,0.35), 0 0 30px ${panel.color}12` : undefined
  }

  if (loading) {
    return (
      <div className="w-screen h-screen flex items-center justify-center select-none"
        style={{ background: '#0c0c10' }}>
        <span className="text-xs text-white/48">{t('common.loading')}</span>
      </div>
    )
  }

  return (
    <div
      className="fixed inset-0 overflow-hidden flex flex-col"
      style={{
        background: bgColor,
        backdropFilter: isElectron ? 'none' : 'blur(20px) saturate(1.3)',
        border: borderVal,
        boxShadow: boxShadowVal,
      }}
    >
      <HeaderBar />
      <div className="flex flex-1 min-h-0 overflow-hidden">
        <ChatModule />
      </div>
    </div>
  )
}

export default StandaloneChatView
