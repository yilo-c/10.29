import React, { useMemo, useState } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'

// ── Types ──────────────────────────────────────────────────────

interface DailyCount {
  date: string  // "MM-DD"
  label: string // "5/16"
  count: number
  day: Date
}

// ── Helpers ────────────────────────────────────────────────────

function lastNDays(n: number): Date[] {
  const days: Date[] = []
  const now = new Date()
  for (let i = n - 1; i >= 0; i--) {
    const d = new Date(now)
    d.setDate(d.getDate() - i)
    d.setHours(0, 0, 0, 0)
    days.push(d)
  }
  return days
}

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear()
    && a.getMonth() === b.getMonth()
    && a.getDate() === b.getDate()
}

function buildDailyCounts(
  days: Date[],
  items: { ts: number }[],
  filter?: (item: { ts: number }) => boolean,
): DailyCount[] {
  return days.map(day => ({
    date: day.toISOString().slice(5, 10),
    label: `${day.getMonth() + 1}/${day.getDate()}`,
    count: items.filter(item => {
      if (filter && !filter(item)) return false
      return isSameDay(day, new Date(item.ts))
    }).length,
    day,
  }))
}

// ── Summary Stat Card ──────────────────────────────────────────

interface StatCardProps {
  icon: string
  label: string
  value: string | number
  color: string
  subtitle?: string
}

const StatCard: React.FC<StatCardProps> = ({ icon, label, value, color, subtitle }) => (
  <div
    className="flex items-center gap-3 px-4 py-3 rounded-xl border"
    style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
  >
    <div
      className="w-9 h-9 rounded-xl flex items-center justify-center shrink-0"
      style={{ background: `${color}18`, color }}
    >
      <Icon icon={`${icon} text-sm`} />
    </div>
    <div className="min-w-0">
      <div className="text-lg font-bold leading-tight" style={{ color: 'var(--text-primary)' }}>
        {value}
      </div>
      <div className="text-xs leading-tight mt-0.5" style={{ color: 'var(--text-muted)' }}>
        {label}
      </div>
      {subtitle && (
        <div className="text-[8px] leading-tight mt-px" style={{ color: 'var(--text-dim)' }}>
          {subtitle}
        </div>
      )}
    </div>
  </div>
)

// ── SVG Bar Chart ──────────────────────────────────────────────

interface BarChartProps {
  data: DailyCount[]
  color: string
  barColor?: string
  height?: number
}

const BarChart: React.FC<BarChartProps> = ({ data, color, barColor, height = 140 }) => {
  const max = Math.max(1, ...data.map(d => d.count))
  const barW = Math.max(4, Math.min(16, (300 / data.length) - 2))
  const usableH = height - 20

  return (
    <svg width="100%" height={height} className="overflow-visible">
      {/* Y-axis reference lines */}
      {[0, 0.5, 1].map(frac => {
        const y = 4 + (1 - frac) * usableH
        return (
          <g key={frac}>
            <line x1={0} y1={y} x2="100%" y2={y} stroke="rgba(255,255,255,0.04)" strokeWidth={0.5} />
            <text x={-4} y={y + 2} textAnchor="end" fill="var(--text-dim)" fontSize={7}>
              {Math.round(max * frac)}
            </text>
          </g>
        )
      })}

      {/* Bars */}
      {data.map((d, i) => {
        const barH = (d.count / max) * usableH
        const x = i * (barW + 2) + 2
        const y = 4 + usableH - barH
        return (
          <g key={i}>
            <rect
              x={x} y={y} width={barW} height={Math.max(1, barH)}
              rx={1.5}
              fill={barColor || color}
              opacity={d.count > 0 ? 0.85 : 0.15}
            />
            {/* Label */}
            <text
              x={x + barW / 2} y={height - 2}
              textAnchor="middle" fill="var(--text-dim)" fontSize={6}
            >
              {d.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ── Module header ──────────────────────────────────────────────

const ModuleHeader: React.FC<{
  title: string
  icon: string
  color: string
  onBack?: () => void
}> = ({ title, icon, color, onBack }) => (
  <div className="flex items-center gap-2 px-1">
    {onBack && (
      <button
        onClick={onBack}
        className="w-6 h-6 flex items-center justify-center rounded-lg text-xs hover:bg-white/[0.08] transition-all"
        style={{ color: 'var(--text-muted)' }}
      >
        <Icon icon="fa-solid fa-chevron-left" />
      </button>
    )}
    <div
      className="w-7 h-7 rounded-lg flex items-center justify-center"
      style={{ background: `${color}18`, color }}
    >
      <Icon icon={`${icon} text-xs`} />
    </div>
    <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
      {title}
    </span>
  </div>
)

// ── Main Component ─────────────────────────────────────────────

interface StatisticsModuleProps {
  onBack?: () => void
}

const StatisticsModule: React.FC<StatisticsModuleProps> = ({ onBack }) => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const floatingNotes = useStore(s => s.floatingNotes)
  // ── Summary stats ──
  const stats = useMemo(() => {
    const totalTasks = todos.length
    const completedTasks = todos.filter(t => t.done).length
    const completionRate = totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0
    const overdueTasks = todos.filter(t => !t.done && t.dueDate && t.dueDate < Date.now()).length
    const totalNotes = floatingNotes.length
    const highPriority = todos.filter(t => !t.done && t.priority !== undefined && t.priority <= 1).length
    return { totalTasks, completedTasks, completionRate, overdueTasks, totalNotes, highPriority }
  }, [todos, floatingNotes])

  // ── Priority distribution ──
  const priorityDist = useMemo(() => {
    const total = todos.filter(t => !t.done)
    const p0 = total.filter(t => t.priority === 0).length
    const p1 = total.filter(t => t.priority === 1).length
    const p2 = total.filter(t => t.priority === 2 || t.priority === undefined).length
    return { p0, p1, p2, total: total.length }
  }, [todos])

  const [range, setRange] = useState<'7' | '14' | '30'>('14')
  const rangeDays = useMemo(() => lastNDays(parseInt(range)), [range])

  const taskTrendRanged = useMemo(() => {
    const completed = todos.filter(t => t.done && t.completedAt)
    return buildDailyCounts(rangeDays, completed.map(t => ({ ts: t.completedAt! })))
  }, [todos, rangeDays])

  const noteTrendRanged = useMemo(() => {
    const withTs = floatingNotes.filter((n): n is typeof n & { createdAt: number } => !!n.createdAt)
    return buildDailyCounts(rangeDays, withTs.map(n => ({ ts: n.createdAt })))
  }, [floatingNotes, rangeDays])

  return (
    <div className="flex-1 flex flex-col min-h-0 p-4 gap-3 overflow-y-auto">
      {/* Header */}
      <ModuleHeader
        title={t('statistics.title')}
        icon="fa-solid fa-chart-simple"
        color="var(--brand)"
        onBack={onBack}
      />

      {/* Summary cards row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <StatCard icon="fa-regular fa-square-check" label={t('statistics.taskCount')} value={stats.totalTasks} color="var(--brand)"
          subtitle={t('statistics.completedFmt', { value: stats.completedTasks })} />
        <StatCard icon="fa-solid fa-gauge-high" label={t('statistics.completionRate')} value={`${stats.completionRate}%`} color="#34d399"
          subtitle={stats.completionRate >= 80 ? t('statistics.goodJob') : stats.completionRate >= 50 ? t('statistics.keepGoing') : t('statistics.needsImprovement')} />
        <StatCard icon="fa-regular fa-clock" label={t('statistics.overdueTasks')} value={stats.overdueTasks} color="var(--danger)"
          subtitle={stats.overdueTasks > 0 ? t('statistics.overdueUrgent') : t('statistics.noOverdue')} />
        <StatCard icon="fa-regular fa-note-sticky" label={t('statistics.noteCount')} value={stats.totalNotes} color="#a78bfa"
          subtitle={t('statistics.highPriorityFmt', { value: stats.highPriority })} />
      </div>

      {/* Range toggle */}
      <div className="flex items-center gap-1.5">
        <span className="text-xs font-medium mr-1" style={{ color: 'var(--text-muted)' }}>{t('statistics.rangeLabel')}</span>
        {(['7', '14', '30'] as const).map(d => (
          <button
            key={d}
            onClick={() => setRange(d)}
            className="px-2 py-0.5 rounded text-xs font-medium transition-all border"
            style={{
              background: range === d ? 'rgba(96,165,250,0.12)' : 'transparent',
              borderColor: range === d ? 'rgba(96,165,250,0.3)' : 'var(--panel-border)',
              color: range === d ? 'var(--brand)' : 'var(--text-muted)',
            }}
          >
            {t('statistics.rangeDaysFmt', { days: d })}
          </button>
        ))}
      </div>

      {/* Charts grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* Task completion trend */}
        <div
          className="rounded-xl p-4 border"
          style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
        >
          <div className="flex items-center gap-2 mb-3">
            <div className="w-2 h-2 rounded-full bg-green-400/60" />
            <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
              {t('statistics.taskTrend')}
            </span>
            <span className="text-2xs ml-auto" style={{ color: 'var(--text-muted)' }}>
              {t('statistics.totalFmt', { value: stats.completedTasks })}
            </span>
          </div>
          <BarChart
            data={taskTrendRanged}
            color="#34d399"
            barColor="rgba(52,211,153,0.7)"
          />
          <div className="flex items-center justify-between mt-1 text-[8px]" style={{ color: 'var(--text-dim)' }}>
            <span>{t('statistics.earliest')} {taskTrendRanged.find(d => d.count > 0)?.label || '—'}</span>
            <span>{t('statistics.dailyAvgFmt', { value: Math.round(taskTrendRanged.reduce((s, d) => s + d.count, 0) / taskTrendRanged.length * 10) / 10 })}</span>
          </div>
        </div>

        {/* Note creation trend */}
        <div
          className="rounded-xl p-4 border"
          style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
        >
          <div className="flex items-center gap-2 mb-3">
            <div className="w-2 h-2 rounded-full bg-purple-400/60" />
            <span className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
              {t('statistics.noteTrend')}
            </span>
            <span className="text-2xs ml-auto" style={{ color: 'var(--text-muted)' }}>
              {t('statistics.noteTotalFmt', { value: stats.totalNotes })}
            </span>
          </div>
          <BarChart
            data={noteTrendRanged}
            color="#a78bfa"
            barColor="rgba(167,139,250,0.7)"
          />
          <div className="flex items-center justify-between mt-1 text-[8px]" style={{ color: 'var(--text-dim)' }}>
            <span>{t('statistics.earliest')} {noteTrendRanged.find(d => d.count > 0)?.label || '—'}</span>
            <span>{t('statistics.noteDailyAvgFmt', { value: Math.round(noteTrendRanged.reduce((s, d) => s + d.count, 0) / noteTrendRanged.length * 10) / 10 })}</span>
          </div>
        </div>
      </div>

      {/* Bottom: priority distribution + completion time */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {/* Priority distribution */}
        <div
          className="rounded-xl p-4 border"
          style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
        >
          <div className="text-xs font-medium mb-3" style={{ color: 'var(--text-primary)' }}>
            {t('statistics.priorityDist')}
          </div>
          <div className="space-y-2">
            {[
              { label: t('statistics.p0Urgent'), count: priorityDist.p0, color: 'var(--danger)' },
              { label: t('statistics.p1Important'), count: priorityDist.p1, color: 'var(--warning)' },
              { label: t('statistics.p2Normal'), count: priorityDist.p2, color: 'var(--text-dim)' },
            ].map(({ label, count, color }) => {
              const pct = priorityDist.total > 0 ? (count / priorityDist.total) * 100 : 0
              return (
                <div key={label} className="flex items-center gap-2">
                  <span className="text-xs w-16 shrink-0" style={{ color: 'var(--text-secondary)' }}>{label}</span>
                  <div className="flex-1 h-2 rounded-full" style={{ background: 'rgba(255,255,255,0.06)' }}>
                    <div
                      className="h-full rounded-full transition-all"
                      style={{ width: `${pct}%`, background: color, opacity: pct > 0 ? 0.8 : 0.2 }}
                    />
                  </div>
                  <span className="text-xs w-8 text-right" style={{ color: 'var(--text-muted)' }}>
                    {count}
                  </span>
                </div>
              )
            })}
          </div>
        </div>

        {/* Completion rate gauge */}
        <div
          className="rounded-xl p-4 border flex flex-col items-center justify-center"
          style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
        >
          <div className="relative w-20 h-20">
            <svg width="80" height="80" viewBox="0 0 80 80">
              <circle cx="40" cy="40" r="34" fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="6" />
              <circle
                cx="40" cy="40" r="34" fill="none"
                stroke="#34d399" strokeWidth="6" strokeLinecap="round"
                strokeDasharray={`${(stats.completionRate / 100) * 213.6} 213.6`}
                transform="rotate(-90 40 40)"
                opacity={stats.completionRate > 0 ? 0.85 : 0.15}
              />
              <text x="40" y="36" textAnchor="middle" fill="var(--text-primary)" fontSize="16" fontWeight="bold">
                {stats.completionRate}%
              </text>
              <text x="40" y="50" textAnchor="middle" fill="var(--text-dim)" fontSize="7">
                {t('statistics.rateLabel')}
              </text>
            </svg>
          </div>
          <div className="flex items-center gap-3 mt-2 text-2xs" style={{ color: 'var(--text-muted)' }}>
            <span>{t('statistics.completedRatioFmt', { done: stats.completedTasks, total: stats.totalTasks })}</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export default StatisticsModule
