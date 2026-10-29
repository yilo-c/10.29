import React, { useState } from 'react'
import Icon from '../Common/Icon'

import { ALL_CARDS, CARD_CONFIGS, COLOR_MAP, TEMPLATES, DEFAULT_COUNTDOWN_DATE, dateToInputValue, inputValueToDate, loadCountdowns, makeCountdownId, saveCountdowns, type CardKey, type LayoutPreset, type DensityKey, type TemplateKey, type CountdownItem } from './DashboardTypes'
import { useTranslation } from '../../i18n'

interface SettingsPanelProps {
  layout: LayoutPreset
  presetName: string
  savedPresets: LayoutPreset[]
  onToggleCard: (key: CardKey) => void
  onReorder: (order: CardKey[]) => void
  onSavePreset: () => void
  onLoadPreset: (preset: LayoutPreset) => void
  onDeletePreset: (name: string) => void
  onResetLayout: () => void
  onPresetNameChange: (name: string) => void
}

const DashboardSettings: React.FC<SettingsPanelProps> = ({
  layout, presetName, savedPresets,
  onToggleCard, onReorder,
  onSavePreset, onLoadPreset, onDeletePreset, onResetLayout, onPresetNameChange,
}) => {
  const { t } = useTranslation()

  // 倒计时配置（自管理状态，参照布局预设的 localStorage 存取模式）。
  const [countdowns, setCountdowns] = useState<CountdownItem[]>(() => {
    const stored = loadCountdowns()
    if (stored && stored.length > 0) return stored
    return [{ id: 'default', label: t('exam.countdownLabel'), date: DEFAULT_COUNTDOWN_DATE }]
  })

  const persistCountdowns = (next: CountdownItem[]) => {
    setCountdowns(next)
    saveCountdowns(next)
  }

  const addCountdown = () => {
    persistCountdowns([
      ...countdowns,
      { id: makeCountdownId(), label: t('dashboardSettings.countdownName'), date: Date.now() + 30 * 86400000 },
    ])
  }

  const changeCountdown = (id: string, patch: Partial<Omit<CountdownItem, 'id'>>) => {
    persistCountdowns(countdowns.map(c => (c.id === id ? { ...c, ...patch } : c)))
  }

  const removeCountdown = (id: string) => {
    persistCountdowns(countdowns.filter(c => c.id !== id))
  }

  return (
    <div className="rounded-xl p-4 border space-y-4" style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>
      {/* Card visibility */}
      <div>
        <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-muted)' }}>{t('dashboardSettings.showCards')}</div>
        <div className="flex flex-wrap gap-1.5">
          {ALL_CARDS.map(key => {
            const cfg = CARD_CONFIGS[key]
            const c = COLOR_MAP[cfg.color]
            const vis = layout.visibility[key]
            return (
              <button key={key} onClick={() => onToggleCard(key)}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs transition-all border"
                style={{ background: vis ? c.dim : 'transparent', borderColor: vis ? `${c.hex}40` : 'var(--panel-border)', color: vis ? c.hex : 'var(--text-muted)', opacity: vis ? 1 : 0.5 }}
              >
                <Icon icon={cfg.icon} />
                {cfg.shortTitle}
              </button>
            )
          })}
        </div>
      </div>

      {/* Card order */}
      <div>
        <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-muted)' }}>{t('dashboardSettings.cardOrder')}</div>
        <div className="flex flex-wrap gap-1.5">
          {ALL_CARDS.filter(k => layout.visibility[k]).map(key => {
            const cfg = CARD_CONFIGS[key]
            const c = COLOR_MAP[cfg.color]
            return (
              <div key={key} className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs border" style={{ borderColor: 'var(--panel-border)', color: c.hex }}>
                <Icon icon={cfg.icon} />
                <span>{cfg.shortTitle}</span>
                <div className="flex gap-0.5 ml-1">
                  <button onClick={() => {
                    const idx = layout.order.indexOf(key)
                    if (idx > 0) {
                      const newOrder = [...layout.order]
                      ;[newOrder[idx - 1], newOrder[idx]] = [newOrder[idx], newOrder[idx - 1]]
                      onReorder(newOrder)
                    }
                  }} className="text-[8px] opacity-50 hover:opacity-100">▲</button>
                  <button onClick={() => {
                    const idx = layout.order.indexOf(key)
                    if (idx < layout.order.length - 1) {
                      const newOrder = [...layout.order]
                      ;[newOrder[idx], newOrder[idx + 1]] = [newOrder[idx + 1], newOrder[idx]]
                      onReorder(newOrder)
                    }
                  }} className="text-[8px] opacity-50 hover:opacity-100">▼</button>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Layout presets */}
      <div>
        <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-muted)' }}>{t('dashboardSettings.layoutPresets')}</div>
        <div className="flex items-center gap-2">
          <input
            value={presetName}
            onChange={e => onPresetNameChange(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && onSavePreset()}
            placeholder={t('dashboardSettings.presetNamePlaceholder')}
            className="flex-1 px-2 py-1 rounded text-xs bg-white/5 border border-white/10 text-text-secondary outline-none focus:border-fluent-blue/40"
          />
          <button onClick={onSavePreset} className="px-2 py-1 rounded text-xs bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 transition-colors">{t('dashboardSettings.save')}</button>
          <button onClick={onResetLayout} className="px-2 py-1 rounded text-xs text-text-muted hover:text-text-secondary transition-colors">{t('dashboardSettings.reset')}</button>
        </div>
        {savedPresets.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {savedPresets.map(p => (
              <div key={p.name} className="flex items-center gap-1 px-2 py-1 rounded-lg text-xs border" style={{ borderColor: 'var(--panel-border)' }}>
                <button onClick={() => onLoadPreset(p)} className="hover:text-fluent-blue transition-colors">{p.name}</button>
                <button onClick={() => onDeletePreset(p.name)} className="text-[8px] opacity-40 hover:opacity-100 text-red-400">×</button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Countdowns */}
      <div>
        <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-muted)' }}>{t('dashboardSettings.countdowns')}</div>
        <div className="space-y-1.5">
          {countdowns.map(c => (
            <div key={c.id} className="flex items-center gap-2">
              <input
                value={c.label}
                onChange={e => changeCountdown(c.id, { label: e.target.value })}
                placeholder={t('dashboardSettings.countdownName')}
                className="flex-1 min-w-0 px-2 py-1 rounded text-xs bg-white/5 border border-white/10 text-text-secondary outline-none focus:border-fluent-blue/40"
              />
              <input
                type="date"
                value={dateToInputValue(c.date)}
                onChange={e => { if (e.target.value) changeCountdown(c.id, { date: inputValueToDate(e.target.value) }) }}
                title={t('dashboardSettings.countdownDate')}
                className="px-2 py-1 rounded text-xs bg-white/5 border border-white/10 text-text-secondary outline-none focus:border-fluent-blue/40"
              />
              <button
                onClick={() => removeCountdown(c.id)}
                className="w-6 h-6 flex items-center justify-center rounded text-xs text-red-400 hover:bg-red-400/10 transition-colors"
                title={t('dashboardSettings.countdownRemove')}
              >
                <Icon icon="fa-solid fa-xmark" />
              </button>
            </div>
          ))}
        </div>
        <button
          onClick={addCountdown}
          className="mt-2 px-2.5 py-1 rounded text-xs border transition-all hover:bg-white/[0.06]"
          style={{ borderColor: 'var(--panel-border)', color: 'var(--text-secondary)' }}
        >
          <Icon icon="fa-regular fa-plus text-2xs mr-1" />{t('dashboardSettings.countdownAdd')}
        </button>
      </div>
    </div>
  )
}

interface ControlBarProps {
  layout: LayoutPreset
  showSettings: boolean
  onSetTemplate: (t: TemplateKey) => void
  onSetDensity: (d: DensityKey) => void
  onToggleSettings: () => void
  onShowStats: () => void
}

export const DashboardControlBar: React.FC<ControlBarProps> = ({
  layout, showSettings,
  onSetTemplate, onSetDensity, onToggleSettings, onShowStats,
}) => {
  const { t } = useTranslation()
  const cycleDensity = () => {
    onSetDensity(layout.density === 'compact' ? 'normal' : layout.density === 'normal' ? 'comfortable' : 'compact')
  }

  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={cycleDensity}
        className="px-2 py-1 rounded-lg text-2xs transition-all hover:bg-white/[0.06]"
        style={{ color: 'var(--text-muted)' }}
        title={t('dashboardSettings.density', { value: layout.density })}
      >
        <Icon icon="fa-solid fa-grip-lines" />
      </button>

      <div className="flex items-center gap-0.5 rounded-lg p-0.5 border" style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>
        {TEMPLATES.map(t => (
          <button
            key={t.key}
            onClick={() => onSetTemplate(t.key)}
            className="px-2.5 py-1 rounded-md text-xs font-medium transition-all"
            style={{
              background: layout.template === t.key ? 'rgba(96,165,250,0.12)' : 'transparent',
              color: layout.template === t.key ? 'var(--brand)' : 'var(--text-muted)',
            }}
            title={t.desc}
          >
            {t.label}
          </button>
        ))}
      </div>

      <button
        onClick={onShowStats}
        className="w-7 h-7 flex items-center justify-center rounded-lg transition-all text-xs hover:bg-white/[0.08]"
        style={{ color: 'var(--text-muted)' }}
        title={t('dashboardSettings.stats')}
        data-testid="dashboard-stats-btn"
      >
        <Icon icon="fa-solid fa-chart-simple" />
      </button>

      <button
        onClick={onToggleSettings}
        className="w-7 h-7 flex items-center justify-center rounded-lg transition-all text-xs"
        style={{ background: showSettings ? 'var(--hover-bg)' : 'transparent', color: showSettings ? 'var(--text-primary)' : 'var(--text-muted)' }}
      >
        <Icon icon="fa-solid fa-sliders" />
      </button>
    </div>
  )
}

export default DashboardSettings
