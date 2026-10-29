import React, { useState } from 'react'
import Icon from './Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import type { CustomTheme } from '../../types'

const CustomThemeEditor: React.FC = () => {
  const { t } = useTranslation()
  const savedThemes = useStore(s => s.savedThemes)
  const activeThemeId = useStore(s => s.activeThemeId)
  const saveTheme = useStore(s => s.saveTheme)
  const deleteTheme = useStore(s => s.deleteTheme)
  const setActiveTheme = useStore(s => s.setActiveTheme)
  const panelColor = useStore(s => s.panel.color)

  const [showEditor, setShowEditor] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [name, setName] = useState('')
  const [colors, setColors] = useState<CustomTheme['colors']>({
    panelBg: 'rgba(22,22,32,0.7)',
    panelBgSolid: 'rgba(18,18,28,0.96)',
    textPrimary: 'rgba(255,255,255,0.87)',
    textSecondary: 'rgba(255,255,255,0.65)',
    accent: '#60a5fa',
    borderColor: 'rgba(255,255,255,0.05)',
    bodyBg: '#0c0c10',
  })
  const [savedFeedback, setSavedFeedback] = useState(false)

  const handleSave = () => {
    if (!name.trim()) return
    saveTheme(name.trim(), colors)
    setName('')
    setSavedFeedback(true)
    setTimeout(() => setSavedFeedback(false), 2000)
  }

  const startNew = () => {
    setColors({
      panelBg: panelColor ? `${panelColor}40` : 'rgba(22,22,32,0.7)',
      panelBgSolid: panelColor ? `${panelColor}80` : 'rgba(18,18,28,0.96)',
      textPrimary: 'rgba(255,255,255,0.87)',
      textSecondary: 'rgba(255,255,255,0.65)',
      accent: panelColor || '#60a5fa',
      borderColor: panelColor ? `${panelColor}40` : 'rgba(255,255,255,0.05)',
      bodyBg: '#0c0c10',
    })
    setName('')
    setShowEditor(true)
  }

  const loadTheme = (theme: CustomTheme) => {
    setActiveTheme(theme.id)
    setShowEditor(false)
  }

  const colorFields: { key: 'panelBg' | 'textPrimary' | 'accent' | 'borderColor' | 'bodyBg'; label: string }[] = [
    { key: 'panelBg', label: t('settings.panelBg') },
    { key: 'textPrimary', label: t('settings.textPrimary') },
    { key: 'accent', label: t('settings.accent') },
    { key: 'borderColor', label: t('settings.borderColor') },
    { key: 'bodyBg', label: t('settings.bodyBg') },
  ]

  const optionalColorFields: { key: 'success' | 'warning' | 'danger' | 'accentWeak' | 'hoverBg' | 'overlayBg'; label: string }[] = [
    { key: 'success', label: t('settings.success') },
    { key: 'warning', label: t('settings.warning') },
    { key: 'danger', label: t('settings.danger') },
    { key: 'accentWeak', label: t('settings.accentWeak') },
    { key: 'hoverBg', label: t('settings.hoverBg') },
    { key: 'overlayBg', label: t('settings.overlayBg') },
  ]

  return (
    <div className="border-t border-white/[0.06] pt-2">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-text-secondary">{t('settings.customTheme')}</span>
        {!showEditor && (
          <button onClick={startNew}
            className="text-2xs px-2 py-0.5 rounded-lg text-fluent-blue hover:bg-fluent-blue/[0.08] transition-colors">
            + {t('settings.createCustom')}
          </button>
        )}
      </div>

      {/* Saved themes list */}
      {!showEditor && savedThemes.length > 0 && (
        <div className="flex flex-col gap-1 mb-1.5 max-h-[100px] overflow-y-auto">
          {savedThemes.map(theme => (
            <div key={theme.id}
              className={`flex items-center gap-2 px-2 py-1 rounded-lg transition-colors ${
                activeThemeId === theme.id ? 'bg-white/[0.06]' : 'hover:bg-white/[0.03]'
              }`}>
              <button onClick={() => loadTheme(theme)}
                className="flex-1 text-xs text-left text-text-secondary truncate">
                {theme.name}
              </button>
              <button onClick={() => deleteTheme(theme.id)}
                className="text-[8px] text-text-dim hover:text-red-400/60 transition-colors">
                <Icon icon="fa-solid fa-xmark" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Active theme indicator */}
      {activeThemeId && !showEditor && (
        <button onClick={() => setActiveTheme(null)}
          className="text-2xs px-2 py-0.5 rounded-lg text-text-muted hover:text-text-secondary hover:bg-white/[0.04] transition-colors w-full text-left">
          {t('settings.resetTheme')}
        </button>
      )}

      {/* Theme editor */}
      {showEditor && (
        <div className="flex flex-col gap-2">
          <input type="text" value={name}
            onChange={e => setName(e.target.value)}
            placeholder={t('settings.themeName')}
            className="w-full text-xs px-2 py-1 rounded-lg bg-white/[0.04] border border-white/[0.06] text-text-secondary placeholder-white/30 outline-none focus:border-white/[0.12] transition-colors"
          />
          <div className="flex flex-col gap-1.5">
            {colorFields.map(({ key, label }) => (
              <div key={key} className="flex items-center gap-2">
                <span className="text-2xs text-text-secondary w-14 flex-shrink-0">{label}</span>
                <input type="color" value={toHex(colors[key])}
                  onChange={e => setColors(c => ({ ...c, [key]: e.target.value }))}
                  className="w-5 h-5 rounded border-0 cursor-pointer flex-shrink-0" style={{ background: 'none' }} />
                <input type="text" value={colors[key]}
                  onChange={e => setColors(c => ({ ...c, [key]: e.target.value }))}
                  className="flex-1 text-2xs px-1.5 py-0.5 rounded bg-white/[0.03] border border-white/[0.04] text-text-secondary font-mono outline-none focus:border-white/[0.1] transition-colors"
                />
              </div>
            ))}
          </div>
          <button onClick={() => setShowAdvanced(v => !v)}
            className="self-start text-2xs text-text-muted hover:text-text-secondary transition-colors">
            {showAdvanced ? '▾' : '▸'} {t('settings.advancedColors')}
          </button>
          {showAdvanced && (
            <div className="flex flex-col gap-1.5 pl-2 border-l border-white/[0.06]">
              {optionalColorFields.map(({ key, label }) => (
                <div key={key} className="flex items-center gap-2">
                  <span className="text-2xs text-text-secondary w-14 flex-shrink-0">{label}</span>
                  <input type="color" value={toHex(colors[key] ?? '')}
                    onChange={e => setColors(c => ({ ...c, [key]: e.target.value }))}
                    className="w-5 h-5 rounded border-0 cursor-pointer flex-shrink-0" style={{ background: 'none' }} />
                  <input type="text" value={colors[key] ?? ''}
                    onChange={e => setColors(c => ({ ...c, [key]: e.target.value || undefined }))}
                    className="flex-1 text-2xs px-1.5 py-0.5 rounded bg-white/[0.03] border border-white/[0.04] text-text-secondary font-mono outline-none focus:border-white/[0.1] transition-colors"
                  />
                </div>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2">
            <button onClick={handleSave}
              className="flex-1 text-2xs px-2 py-1 rounded-lg bg-fluent-blue/15 text-fluent-blue hover:bg-fluent-blue/20 transition-colors"
              disabled={!name.trim()}>
              {savedFeedback ? t('settings.themeSaved') : t('settings.saveTheme')}
            </button>
            <button onClick={() => setShowEditor(false)}
              className="text-2xs px-2 py-1 rounded-lg text-text-muted hover:text-text-secondary hover:bg-white/[0.04] transition-colors">
              {t('common.cancel')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function toHex(rgba: string): string {
  // If already hex, return as-is
  if (rgba.startsWith('#')) return rgba
  // Parse rgba(r,g,b,a) or rgb(r,g,b)
  const m = rgba.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/)
  if (m) {
    const r = parseInt(m[1]).toString(16).padStart(2, '0')
    const g = parseInt(m[2]).toString(16).padStart(2, '0')
    const b = parseInt(m[3]).toString(16).padStart(2, '0')
    return `#${r}${g}${b}`
  }
  return '#3b82f6'
}

export default CustomThemeEditor
