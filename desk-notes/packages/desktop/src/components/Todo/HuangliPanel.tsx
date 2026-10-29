import React from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import type { HuangliData } from '../../utils/astrology'

interface HuangliPanelProps {
  huangli: HuangliData
  collapsed: boolean
  onToggle: () => void
}

const HuangliPanel: React.FC<HuangliPanelProps> = ({ huangli, collapsed, onToggle }) => {
  const { t } = useTranslation()
  return (
    <div className="mt-2 pt-2 border-t border-white/[0.04]">
      <button
        onClick={onToggle}
        className="flex items-center gap-1 w-full text-left"
      >
        <Icon icon={`fa-solid fa-chevron-${collapsed ? 'right' : 'down'} text-[8px]`}
          style={{ color: 'var(--text-muted)' }} />
          <span className="text-2xs font-medium" style={{ color: 'var(--text-secondary)' }}>
            {t('huangliPanel.title')}
          </span>
        {huangli.jieQi && (
          <span className="text-2xs text-amber-400 ml-1">
            · {huangli.jieQi}
          </span>
        )}
      </button>

      {!collapsed && (
        <div className="mt-1.5 space-y-1 text-2xs leading-relaxed">
          <div className="flex flex-wrap gap-x-3 gap-y-0.5" style={{ color: 'var(--text-secondary)' }}>
            <span>{huangli.lunar.fullDate}</span>
            <span>{t('huangliPanel.yearFmt', { gzYear: huangli.ganzhi.year, animal: huangli.shengxiao.year })}</span>
            <span>{t('huangliPanel.monthDayFmt', { gzMonth: huangli.ganzhi.month, gzDay: huangli.ganzhi.day })}</span>
            <span>{t('huangliPanel.naYin', { value: huangli.naYin })}</span>
          </div>

          <div style={{ color: 'var(--text-secondary)' }}>
            {huangli.weekDay} · {t('huangliPanel.chongSha', { chong: huangli.chongSha.chong, sha: huangli.chongSha.sha })}
          </div>

          <div className="flex flex-wrap gap-2" style={{ color: 'var(--text-secondary)' }}>
            <span>{t('huangliPanel.xiuFmt', { name: huangli.xiu.name, animal: huangli.xiu.animal, luck: huangli.xiu.luck })}</span>
            {huangli.jianChu && (
              <span>{t('huangliPanel.jianChuFmt', { value: huangli.jianChu, luck: huangli.jianChuLuck })}</span>
            )}
          </div>

          {huangli.festivals.length + huangli.otherFestivals.length > 0 && (
            <div className="flex flex-wrap gap-x-3" style={{ color: 'var(--text-secondary)' }}>
              <span>{t('huangliPanel.festivals', { items: [...huangli.festivals, ...huangli.otherFestivals].join(t('huangliPanel.separator')) })}</span>
            </div>
          )}

          <div className="italic" style={{ color: 'var(--text-muted)' }}>
            {huangli.pengZu}
          </div>

          <div className="flex flex-wrap gap-x-3" style={{ color: 'var(--text-secondary)' }}>
            <span>{t('huangliPanel.xiShen', { value: huangli.fangwei.xi })}</span>
            <span>{t('huangliPanel.caiShen', { value: huangli.fangwei.cai })}</span>
            <span>{t('huangliPanel.fuShen', { value: huangli.fangwei.fu })}</span>
          </div>

          <div className="flex flex-wrap items-start gap-1">
            <span className="text-green-400/80 font-medium shrink-0">{t('huangliPanel.yi')}</span>
            <div className="flex flex-wrap gap-1">
              {huangli.yiJi.yi.map((item, i) => (
                <span key={i}
                  className="px-1.5 py-0.5 rounded text-[8px] leading-tight"
                  style={{ background: 'rgba(34,197,94,0.1)', color: 'rgba(34,197,94,0.8)' }}
                >{item}</span>
              ))}
              {huangli.yiJi.yi.length === 0 && (
                <span className="text-text-dim text-[8px]">{t('huangliPanel.none')}</span>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-start gap-1">
            <span className="text-red-400/80 font-medium shrink-0">{t('huangliPanel.ji')}</span>
            <div className="flex flex-wrap gap-1">
              {huangli.yiJi.ji.map((item, i) => (
                <span key={i}
                  className="px-1.5 py-0.5 rounded text-[8px] leading-tight"
                  style={{ background: 'rgba(239,68,68,0.1)', color: 'rgba(239,68,68,0.8)' }}
                >{item}</span>
              ))}
              {huangli.yiJi.ji.length === 0 && (
                <span className="text-text-dim text-[8px]">{t('huangliPanel.none')}</span>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export default HuangliPanel
