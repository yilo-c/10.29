import React, { useMemo } from 'react'
import Icon from '../Common/Icon'
import { useStore } from '../../store/useStore'
import { useTranslation } from '../../i18n'
import { COLOR_MAP, selectTodayTasks } from './DashboardTypes'
import type { TodoItem } from '@desk-notes/shared'

interface TodayGroup {
  key: 'overdue' | 'dueToday' | 'p0'
  label: string
  color: string
  items: TodoItem[]
}

/**
 * 「今天要处理」聚合区：卡片网格上方的独立区块。
 * 聚合排序 overdue（逾期）→ dueToday（今日到期）→ p0（高优先级且无截止日期），
 * 逾期标红；每条点击即完成（复用既有 toggleTodo RPC/数据获取方式）。
 * 独立于 DashboardCardContents 的 slice(0,5) 逻辑，逾期任务不会被新建任务挤出。
 */
export const DashboardTodayBar: React.FC = () => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const setActiveModule = useStore(s => s.setActiveModule)

  const groups = useMemo<TodayGroup[]>(() => {
    const { overdue, dueToday, p0 } = selectTodayTasks(todos)
    const out: TodayGroup[] = []
    if (overdue.length > 0) out.push({ key: 'overdue', label: t('dashboardToday.overdue'), color: 'var(--danger)', items: overdue })
    if (dueToday.length > 0) out.push({ key: 'dueToday', label: t('dashboardToday.dueToday'), color: '#fbbf24', items: dueToday })
    if (p0.length > 0) out.push({ key: 'p0', label: t('dashboardToday.priority'), color: COLOR_MAP.purple.hex, items: p0 })
    return out
  }, [todos, t])

  const total = groups.reduce((acc, g) => acc + g.items.length, 0)
  if (total === 0) return null

  const complete = (id: string) => {
    useStore.getState().toggleTodo(id)
  }

  const fmtDate = (ms?: number): string =>
    ms ? new Date(ms).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' }) : ''

  return (
    <div className="rounded-xl px-4 py-3 border" style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
          <Icon icon="fa-solid fa-bolt text-2xs" style={{ color: '#fbbf24' }} />
          {t('dashboardToday.title')}
          <span className="px-1.5 py-0.5 rounded text-2xs" style={{ background: 'rgba(251,191,36,0.12)', color: '#fbbf24' }}>
            {total}
          </span>
        </div>
        <button
          onClick={() => setActiveModule('todo')}
          className="text-xs hover:opacity-80 transition-opacity"
          style={{ color: 'var(--text-muted)' }}
        >
          {t('dashboardToday.viewAll')}
        </button>
      </div>
      <div className="space-y-1">
        {groups.map(group => (
          <div key={group.key}>
            {group.items.map(todo => (
              <div
                key={todo.id}
                className="flex items-center gap-2.5 py-1.5 px-2 rounded-lg cursor-pointer transition-all text-xs hover:bg-white/[0.05]"
                style={{ color: group.key === 'overdue' ? 'var(--danger)' : 'var(--text-secondary)' }}
                onClick={() => complete(todo.id)}
                title={group.key === 'overdue' ? t('dashboardToday.overdue') : undefined}
              >
                <div
                  className="w-4 h-4 rounded-full border-2 flex-shrink-0 transition-all"
                  style={{ borderColor: group.color }}
                />
                <span className="flex-1 truncate">{todo.text}</span>
                <span
                  className="text-2xs px-1.5 py-0.5 rounded shrink-0"
                  style={{ background: `${group.color}18`, color: group.color }}
                >
                  {group.label}
                </span>
                {todo.dueDate && (
                  <span className="text-xs flex-shrink-0 tabular-nums" style={{ color: 'var(--text-muted)' }}>
                    {fmtDate(todo.dueDate)}
                  </span>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}

export default DashboardTodayBar
