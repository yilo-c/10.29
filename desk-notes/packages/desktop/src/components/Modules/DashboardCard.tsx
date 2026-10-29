import React, { useState } from 'react'
import Icon from '../Common/Icon'

import { COLOR_MAP, DENSITY_CONFIG, SIZE_LABELS, type CardSize, type DensityKey, type CardConfig } from './DashboardTypes'
import { useTranslation } from '@desk-notes/shared/i18n'

interface CardProps {
  config: CardConfig
  visible: boolean
  size: CardSize
  span: number
  density: DensityKey
  children: React.ReactNode
  onAction?: () => void
  onCycleSize?: () => void
  onCycleSpan?: () => void
}

const DashboardCard: React.FC<CardProps> = ({ config, visible, size, span, density, children, onAction, onCycleSize, onCycleSpan }) => {
  const { t } = useTranslation()
  const [hovered, setHovered] = useState(false)
  const c = COLOR_MAP[config.color]
  const dd = DENSITY_CONFIG[density]

  if (!visible) return null

  const padSizes: Record<CardSize, string> = { small: 'px-3 pt-2.5 pb-1.5', medium: 'px-4 pt-3.5 pb-2', large: 'px-5 pt-4 pb-3' }
  const bodyPad: Record<CardSize, string> = { small: 'px-3 pb-2', medium: 'px-4 pb-3', large: 'px-5 pb-4' }
  const titleSize: Record<CardSize, string> = { small: 'text-xs', medium: 'text-sm', large: 'text-base' }

  return (
    <div
      className={`group rounded-xl flex flex-col overflow-hidden transition-all duration-200 border ${span === 2 ? 'md:col-span-2' : ''}`}
      style={{
        background: 'var(--panel-bg)',
        borderColor: 'var(--panel-border)',
        boxShadow: hovered ? '0 8px 24px rgba(0,0,0,0.20)' : '0 1px 3px rgba(0,0,0,0.08)',
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <div className={`flex items-center justify-between ${padSizes[size]}`}>
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0" style={{ background: c.dim, color: c.hex, fontSize: size === 'small' ? '9px' : '11px' }}>
            <Icon icon={config.icon} />
          </div>
          <div className="min-w-0">
            <div className={`font-medium truncate ${titleSize[size]}`} style={{ color: 'var(--text-primary)' }}>
              {config.title}
            </div>
            {size !== 'small' && (
              <div className="text-xs mt-0.5 leading-tight truncate" style={{ color: 'var(--text-muted)' }}>
                {config.subtitle}
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0 ml-2">
          {onCycleSpan && (
            <button
              onClick={e => { e.stopPropagation(); onCycleSpan() }}
              className="w-5 h-5 flex items-center justify-center rounded text-[8px] opacity-0 group-hover:opacity-60 hover:opacity-100 hover:bg-white/[0.08] transition-all"
              style={{ color: 'var(--text-muted)' }}
              title={t('dashboardCard.spanLabelFmt', { span })}
            >
              <Icon icon="fa-solid fa-arrows-left-right" />
            </button>
          )}
          {onCycleSize && (
            <button
              onClick={e => { e.stopPropagation(); onCycleSize() }}
              className="w-5 h-5 flex items-center justify-center rounded text-[8px] opacity-0 group-hover:opacity-60 hover:opacity-100 hover:bg-white/[0.08] transition-all"
              style={{ color: 'var(--text-muted)' }}
              title={t('dashboardCard.sizeLabelFmt', { size: SIZE_LABELS[size] })}
            >
              <Icon icon="fa-solid fa-expand" />
            </button>
          )}
          {onAction && (
            <button
              onClick={onAction}
              className="px-1.5 py-0.5 rounded-md text-2xs font-medium transition-all border hover:text-text-secondary ml-1"
              style={{ borderColor: 'var(--panel-border)', color: 'var(--text-muted)' }}
            >
              {t('dashboardCard.open')}
            </button>
          )}
        </div>
      </div>

      <div className={`flex-1 min-h-0 ${bodyPad[size]} ${dd.fontSize}`}>
        {children}
      </div>
    </div>
  )
}

export default DashboardCard
