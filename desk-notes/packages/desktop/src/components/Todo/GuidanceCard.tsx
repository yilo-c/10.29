import React from 'react'
import { generateSummaryQuote, type DailyGuidance } from '../../utils/astrology'

interface GuidanceCardProps {
  guidance: DailyGuidance
  t: (key: string, params?: Record<string, string | number>) => string
}

const levelColors: Record<string, { bg: string; text: string; border: string }> = {
  '大吉': { bg: 'rgba(34,197,94,0.12)', text: 'rgb(34,197,94)', border: 'rgba(34,197,94,0.25)' },
  '中吉': { bg: 'rgba(34,197,94,0.07)', text: 'rgb(74,222,128)', border: 'rgba(34,197,94,0.15)' },
  '平和': { bg: 'rgba(148,163,184,0.1)', text: 'rgb(148,163,184)', border: 'rgba(148,163,184,0.2)' },
  '小凶': { bg: 'rgba(251,146,60,0.1)', text: 'rgb(251,146,60)', border: 'rgba(251,146,60,0.2)' },
  '大凶': { bg: 'rgba(239,68,68,0.12)', text: 'rgb(239,68,68)', border: 'rgba(239,68,68,0.25)' },
}

const GuidanceCard: React.FC<GuidanceCardProps> = ({ guidance, t }) => {
  const { advice, todoStats } = guidance
  if (!advice) return null

  const colors = levelColors[advice.level] || levelColors['平和']
  const dateSeed = guidance.huangli?.solarDate
  const summary = generateSummaryQuote(guidance, dateSeed)

  return (
    <div className="rounded-lg px-3 py-2 space-y-1.5"
      style={{
        background: colors.bg,
        border: `1px solid ${colors.border}`,
      }}
    >
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium" style={{ color: colors.text }}>
          {t('guidance.title')}
        </span>
        <span className="text-2xs px-1.5 py-0.5 rounded-sm font-medium" style={{
          background: colors.text + '22',
          color: colors.text,
        }}>
          {t(`guidance.level_${advice.level}`)}
        </span>
        {advice.suitable.length > 0 && (
          <span className="text-[8px]" style={{ color: 'var(--text-muted)' }}>
            {t('guidance.suitable')} {advice.suitable.slice(0, 3).join('、')}
          </span>
        )}
      </div>

      <div className="text-2xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
        {summary}
      </div>

      {todoStats.total > 0 && (
        <div className="flex flex-wrap gap-2 text-[8px]" style={{ color: 'var(--text-muted)' }}>
          <span>{todoStats.total} {t('todo.items')}</span>
          <span style={{ color: 'rgb(34,197,94)' }}>{todoStats.done}</span>
          <span>{todoStats.pending}</span>
          {todoStats.overdue > 0 && (
            <span style={{ color: 'rgb(239,68,68)' }}>! {todoStats.overdue} {t('guidance.overdue')}</span>
          )}
          {todoStats.highPriority > 0 && (
            <span style={{ color: 'rgb(251,146,60)' }}>{todoStats.highPriority} {t('guidance.highPriority')}</span>
          )}
        </div>
      )}
    </div>
  )
}

export default GuidanceCard
