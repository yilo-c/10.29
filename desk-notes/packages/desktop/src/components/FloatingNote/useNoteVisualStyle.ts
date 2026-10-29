import { useMemo } from 'react'
import { hexToRgb } from '../../utils/helpers'

const ei = window.electronAPI as ElectronAPI | undefined
const isElectron = !!ei

export function useNoteVisualStyle(
  backgroundMode: string,
  color: string | undefined,
  pinned: boolean | undefined,
  locked: boolean | undefined,
  noteOpacity: number,
) {
  return useMemo(() => {
    let bgColor: string
    let backdropFilter: string | undefined
    let noteBorder: string
    let noteBoxShadow: string | undefined

    if (backgroundMode === 'pure-white') {
      bgColor = color
        ? `rgba(250,250,252,0.95)`
        : 'rgba(250,250,252,0.95)'
      backdropFilter = 'none'
      noteBorder = color ? `1px solid ${color}50` : '1px solid rgba(0,0,0,0.06)'
      noteBoxShadow = '0 4px 24px rgba(0,0,0,0.06), 0 0 0 1px rgba(0,0,0,0.04)'
    } else if (backgroundMode === 'pure-black') {
      bgColor = color
        ? `rgba(14,14,20,0.98)`
        : 'rgba(14,14,20,0.98)'
      backdropFilter = 'none'
      noteBorder = color ? `1px solid ${color}50` : '1px solid rgba(255,255,255,0.04)'
      noteBoxShadow = '0 4px 24px rgba(0,0,0,0.5)'
    } else {
      const baseAlpha = isElectron ? 0.65 : 0.78
      const bgAlpha = baseAlpha * noteOpacity
      if (color) {
        const rgb = hexToRgb(color)
        if (rgb) {
          const tint = 0.30 * noteOpacity
          const r = Math.round(24 + (rgb.r - 24) * tint)
          const g = Math.round(24 + (rgb.g - 24) * tint)
          const b = Math.round(34 + (rgb.b - 34) * tint)
          bgColor = `rgba(${r},${g},${b},${bgAlpha.toFixed(2)})`
        } else {
          bgColor = `rgba(24,24,34,${bgAlpha.toFixed(2)})`
        }
      } else {
        bgColor = `rgba(24,24,34,${bgAlpha.toFixed(2)})`
      }
      backdropFilter = isElectron ? 'none' : 'blur(18px) saturate(1.2)'
      noteBorder = pinned ? `1px solid ${color ? `${color}70` : 'rgba(96,165,250,0.2)'}` :
        locked ? `1px solid ${color ? `${color}70` : 'rgba(251,191,36,0.2)'}` :
        color ? `1px solid ${color}50` :
        '1px solid rgba(255,255,255,0.06)'
      noteBoxShadow = pinned
        ? `0 8px 40px rgba(0,0,0,0.35), 0 0 0 1px ${color ? `${color}30` : 'rgba(96,165,250,0.08)'}${color ? `, 0 0 25px ${color}18` : ''}`
        : locked
        ? `0 8px 40px rgba(0,0,0,0.35), 0 0 0 1px ${color ? `${color}30` : 'rgba(251,191,36,0.08)'}${color ? `, 0 0 25px ${color}18` : ''}`
        : color
        ? `0 8px 40px rgba(0,0,0,0.35), 0 0 30px ${color}15`
        : '0 8px 40px rgba(0,0,0,0.35)'
    }
    return { bgColor, backdropFilter, noteBorder, noteBoxShadow }
  }, [backgroundMode, color, pinned, locked, noteOpacity])
}
