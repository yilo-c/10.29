import React from 'react'
import Icon from '../Common/Icon'

import { ViewMode } from '../../types'
import { SortMode } from './todoListUtils'
import SortMenu, { SortOption } from '../Common/SortMenu'

const TODO_SORT_OPTIONS: SortOption[] = [
  { mode: 'created', tKey: 'todo.sortByCreated' },
  { mode: 'priority', tKey: 'todo.sortByPriority' },
  { mode: 'dueDate', tKey: 'todo.sortByDueDate' },
  { mode: 'alpha', tKey: 'todo.sortByAlpha' },
]

interface TodoToolBarProps {
  searchQuery: string
  setSearchQuery: (q: string) => void
  viewMode: ViewMode
  setViewMode: (m: ViewMode) => void
  sortMode: SortMode
  setSortMode: (m: SortMode) => void
  filterStatus: 'all' | 'active' | 'completed' | 'overdue'
  setFilterStatus: (s: 'all' | 'active' | 'completed' | 'overdue') => void
  filterPriority: number[]
  toggleFilterPriority: (p: number) => void
  t: (key: string, params?: Record<string, string | number>) => string
  batchMode?: boolean
  onToggleBatch?: () => void
  sortAsc?: boolean
  onToggleSortDir?: () => void
}

const TodoToolBar: React.FC<TodoToolBarProps> = ({
  searchQuery, setSearchQuery, viewMode, setViewMode, sortMode, setSortMode,
  filterStatus, setFilterStatus, filterPriority, toggleFilterPriority,
  t, batchMode, onToggleBatch, sortAsc, onToggleSortDir,
}) => {
  return (
    <>
      <div className="px-3 pt-2 pb-1 min-w-0">
        <div className="flex items-center gap-2 bg-white/[0.04] rounded-xl px-3 py-1.5 border border-white/[0.04] focus-within:border-white/[0.1] transition-colors min-w-0">
          <span className="text-text-secondary text-xs flex-shrink-0"><Icon icon="fa-solid fa-magnifying-glass" /></span>
          <input
            value={searchQuery}
            onChange={e => setSearchQuery(e.target.value)}
            placeholder={t('todo.searchPlaceholder')}
            className="flex-1 bg-transparent text-xs text-text-primary placeholder:text-text-secondary outline-none"
          />
          {searchQuery && (
            <button onClick={() => setSearchQuery('')}
              className="text-xs text-text-secondary hover:text-text-secondary transition-colors flex-shrink-0">
              <Icon icon="fa-solid fa-xmark" />
            </button>
          )}
          <button
            data-testid="todo-view-toggle"
            onClick={() => {
              const modes: ViewMode[] = ['list', 'calendar', 'kanban']
              const idx = modes.indexOf(viewMode)
              setViewMode(modes[(idx + 1) % modes.length])
            }}
            className="flex items-center gap-1 text-2xs px-1.5 py-0.5 rounded-lg transition-colors text-text-secondary hover:text-text-secondary hover:bg-white/[0.04] flex-shrink-0"
            title={
              viewMode === 'list' ? t('todo.viewMode.calendar') :
              viewMode === 'calendar' ? t('todo.viewMode.kanban') :
              t('todo.viewMode.list')
            }
          >
            <Icon icon={`fa-solid ${
              viewMode === 'list' ? 'fa-calendar-days' :
              viewMode === 'calendar' ? 'fa-columns' :
              'fa-list'
            } text-xs`} />
            <span className="hidden md:inline">
              {viewMode === 'list' ? t('todo.viewMode.calendarShort') :
               viewMode === 'calendar' ? t('todo.viewMode.kanbanShort') :
               t('todo.viewMode.listShort')}
            </span>
          </button>
          <SortMenu
            options={TODO_SORT_OPTIONS}
            current={sortMode}
            asc={sortAsc ?? true}
            onSelect={(mode) => setSortMode(mode as SortMode)}
            onToggleDir={onToggleSortDir ?? (() => {})}
            t={t}
            titleKey="todo.sortTitle"
          />
          <button
            onClick={onToggleBatch}
            className={`flex items-center gap-1 text-2xs px-1.5 py-0.5 rounded-lg transition-colors flex-shrink-0 ${batchMode ? 'text-text-primary bg-white/[0.08]' : 'text-text-secondary hover:text-text-secondary hover:bg-white/[0.04]'}`}
            title={t('todo.batchSelect')}
          >
            <Icon icon="fa-solid fa-check-square text-xs" />
          </button>
        </div>
      </div>

      {!batchMode && (
      <div className="flex items-center gap-1 px-3 pb-1">
        {(['all', 'active', 'completed', 'overdue'] as const).map(s => {
          const active = filterStatus === s
          const label = s === 'all' ? t('todo.all') : s === 'active' ? t('todo.active') : s === 'completed' ? t('todo.completed') : t('todo.overdue')
          return (
            <button key={s} onClick={() => setFilterStatus(s)}
              className={`text-2xs px-2 py-0.5 rounded-full transition-all ${active ? 'text-text-primary bg-white/[0.08]' : 'text-text-muted hover:text-text-secondary hover:bg-white/[0.04]'}`}>
              {label}
            </button>
          )
        })}
        <span className="text-white/[0.06] mx-0.5">|</span>
        {([0, 1, 2] as const).map(p => {
          const active = filterPriority.includes(p)
          const label = p === 0 ? 'P0' : p === 1 ? 'P1' : 'P2'
          const color = p === 0 ? '#f87171' : p === 1 ? '#fbbf24' : '#64748b'
          return (
            <button key={p} onClick={() => toggleFilterPriority(p)}
              className={`text-2xs px-1.5 py-0.5 rounded-full transition-all ${active ? 'text-text-primary' : 'text-text-muted hover:text-text-secondary hover:bg-white/[0.04]'}`}
              style={active ? { background: `${color}20`, color } : undefined}>
              {label}
            </button>
          )
        })}
      </div>
      )}
      {batchMode && (
        <div className="flex items-center gap-2 px-3 pb-1.5">
          <Icon icon="fa-solid fa-check-square text-[8px] text-fluent-blue/50" />
          <span className="text-2xs text-text-muted">{t('todo.batchModeHint')}</span>
        </div>
      )}
    </>
  )
}

export default TodoToolBar
