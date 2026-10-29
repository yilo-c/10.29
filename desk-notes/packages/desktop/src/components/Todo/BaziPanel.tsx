import React from 'react'
import Icon from '../Common/Icon'

import { getWuxingColor, type BaziProfile } from '../../utils/astrology'

interface BaziPanelProps {
  bazi: BaziProfile
  onEdit: () => void
  t: (key: string, params?: Record<string, string | number>) => string
}

const BaziPanel: React.FC<BaziPanelProps> = ({ bazi, onEdit, t }) => {
  const allWx = ['金', '木', '水', '火', '土']
  const maxCount = Math.max(...allWx.map(el => bazi.wuxing[el] || 0), 1)

  return (
    <div className="rounded-lg px-3 py-2 space-y-1.5"
      style={{ background: 'var(--hover-bg)', border: '1px solid var(--panel-border)' }}
    >
      <div className="flex items-center justify-between">
        <span className="text-2xs font-medium" style={{ color: 'var(--text-secondary)' }}>
          {t('guidance.baziTitle')}
        </span>
        <button onClick={onEdit}
          className="text-[8px] px-1.5 py-0.5 rounded transition-all"
          style={{ color: 'var(--text-muted)' }}
        >
          <Icon icon="fa-solid fa-pen" />
        </button>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-xs font-bold" style={{ color: 'var(--text-primary)' }}>
          {t('guidance.dayMaster')}: {bazi.dayMaster}
        </span>
        {bazi.strength && (
          <span className="text-[8px] px-1.5 py-0.5 rounded" style={{
            background: bazi.strength === '强' ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)',
            color: bazi.strength === '强' ? 'rgb(34,197,94)' : 'rgb(239,68,68)',
          }}>
            {t('guidance.baziStrength', { strength: bazi.strength })}
          </span>
        )}
        {bazi.missingElement && (
          <span className="text-[8px] px-1.5 py-0.5 rounded"
            style={{ background: 'rgba(251,146,60,0.1)', color: 'rgb(251,146,60)' }}
          >
            {t('guidance.missingElement', { el: bazi.missingElement })}
          </span>
        )}
      </div>

      <div className="text-[8px]" style={{ color: 'var(--text-muted)' }}>
        {t('guidance.fourPillars')}:{' '}
        {bazi.fourPillars.year} {bazi.fourPillars.month} {bazi.fourPillars.day}
        {bazi.fourPillars.hour ? ` ${bazi.fourPillars.hour}` : ''}
      </div>

      {bazi.yongShen.length > 0 && (
        <div className="flex flex-wrap gap-2 items-center">
          <span className="text-[8px]" style={{ color: 'rgb(34,197,94)' }}>
            {t('guidance.yongShen')}: {bazi.yongShen.join('、')}
          </span>
          {bazi.jiShen.length > 0 && (
            <span className="text-[8px]" style={{ color: 'rgb(239,68,68)' }}>
              {t('guidance.jiShen')}: {bazi.jiShen.join('、')}
            </span>
          )}
        </div>
      )}

      <div className="space-y-0.5">
        <span className="text-[8px]" style={{ color: 'var(--text-muted)' }}>
          {t('guidance.wuxingChart')}:
        </span>
        <div className="flex gap-1.5">
          {allWx.map(el => {
            const count = bazi.wuxing[el] || 0
            const width = Math.max((count / maxCount) * 100, count > 0 ? 15 : 5)
            return (
              <div key={el} className="flex flex-col items-center gap-0.5 flex-1">
                <div className="w-full h-1.5 rounded-full overflow-hidden"
                  style={{ background: 'rgba(255,255,255,0.06)' }}>
                  <div className="h-full rounded-full transition-all"
                    style={{
                      width: `${width}%`,
                      background: getWuxingColor(el),
                      opacity: count > 0 ? 0.8 : 0.2,
                    }}
                  />
                </div>
                <span className="text-[6px]" style={{ color: count > 0 ? getWuxingColor(el) : 'var(--text-dim)' }}>
                  {el} {count > 0 ? count : ''}
                </span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default BaziPanel
