import React, { useMemo, useState, useCallback, useRef, useEffect } from 'react'
import Icon from '../Common/Icon'

import { useTranslation } from '../../i18n'
import { useStore } from '@desk-notes/shared'
import { uid } from '../../utils/helpers'
import TodoList from '../Todo/TodoList'
import TodoDetailPanel from '../Todo/TodoDetailPanel'
import PageHeader from '../Common/PageHeader'

const ei = window.electronAPI as ElectronAPI | undefined

const SUBJECTS: Record<string, { labelKey: string; icon: string; color: string }> = {
  politics: { labelKey: 'todoModule.subjects.politics', icon: 'fa-solid fa-flag', color: 'var(--danger)' },
  english: { labelKey: 'todoModule.subjects.english', icon: 'fa-solid fa-language', color: '#3b82f6' },
  math: { labelKey: 'todoModule.subjects.math', icon: 'fa-solid fa-square-root-variable', color: 'var(--success)' },
  mechanics: { labelKey: 'todoModule.subjects.mechanics', icon: 'fa-solid fa-gears', color: '#eab308' },
  agriculture: { labelKey: 'todoModule.subjects.agriculture', icon: 'fa-solid fa-seedling', color: '#a855f7' },
}

const STUDY_PLAN: { subject: string; labelKey: string; minutes: number }[] = [
  { subject: 'politics', labelKey: 'todoModule.subjects.politics', minutes: 120 },
  { subject: 'english', labelKey: 'todoModule.subjects.english', minutes: 90 },
  { subject: 'math', labelKey: 'todoModule.subjects.math', minutes: 120 },
  { subject: 'mechanics', labelKey: 'todoModule.subjects.mechanics', minutes: 90 },
  { subject: 'agriculture', labelKey: 'todoModule.subjects.agriculture', minutes: 90 },
]

const TodoModule: React.FC = () => {
  const { t } = useTranslation()
  const todos = useStore(s => s.todos)
  const filterSubject = useStore(s => s.filterSubject)
  const setFilterSubject = useStore(s => s.setFilterSubject)
  const [studyPlanCreated, setStudyPlanCreated] = useState(false)
  // C1：右栏详情面板选中态（三栏范式）
  const [selectedTodoId, setSelectedTodoId] = useState<string | null>(null)
  const selectedTodo = selectedTodoId ? todos.find(td => td.id === selectedTodoId) ?? null : null
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [])

  const handleCreateStudyPlan = useCallback(() => {
    const now = Date.now()
    const newTodos = STUDY_PLAN.map((item, i) => {
      const id = uid()
      return {
        id,
        text: `[${t('todoModule.examPrefix')}] ${t(item.labelKey)} · ${item.minutes}min`,
        done: false,
        createdAt: now + i,
        sortOrder: now + i,
        subject: item.subject,
        category: 'study' as string | undefined,
        estimatedMinutes: item.minutes,
      }
    })
    useStore.setState(s => ({
      todos: [...newTodos, ...s.todos],
    }))
    setStudyPlanCreated(true)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setStudyPlanCreated(false), 3000)
  }, [t])

  // Compute study stats per subject key for today
  const stats = useMemo(() => {
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const todayEnd = new Date(today.getTime() + 86400000)
    const result: Record<string, { total: number; done: number; minutes: number }> = {}
    for (const key of Object.keys(SUBJECTS)) {
      result[key] = { total: 0, done: 0, minutes: 0 }
    }
    for (const todo of todos) {
      if (!todo.subject) continue
      if (!SUBJECTS[todo.subject]) continue
      const bucket = result[todo.subject] || (result[todo.subject] = { total: 0, done: 0, minutes: 0 })
      bucket.total++
      if (todo.done && todo.completedAt && todo.completedAt >= today.getTime() && todo.completedAt < todayEnd.getTime()) {
        bucket.done++
      }
      if (todo.estimatedMinutes) {
        bucket.minutes += todo.estimatedMinutes
      }
    }
    return result
  }, [todos])

  const hasStudyData = useMemo(() =>
    Object.values(stats).some(s => s.total > 0), [stats])

  const pendingCount = useMemo(() => todos.filter(td => !td.done).length, [todos])

  return (
    <div className="flex-1 flex min-h-0 overflow-hidden">
      {/* 主区：列表 */}
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 px-5 pt-5 pb-3">
        <PageHeader
          title={t('nav.todo')}
          subtitle={t('todoModule.subtitleFmt', { count: pendingCount })}
          actions={ei ? (
            <button
              onClick={() => ei.createViewWindow({ viewType: 'todo' })}
              className="w-8 h-8 flex items-center justify-center rounded-lg text-sm text-text-dim hover:text-text-secondary hover:bg-hover-bg transition-all"
              title={t('todoModule.independentWindow')}
            >
              <Icon icon="fa-solid fa-external-link-alt" />
            </button>
          ) : undefined}
        />
      </div>

      {/* Subject filter bar */}
      <div className="flex items-center gap-1.5 px-5 py-2.5 border-b border-border flex-shrink-0 overflow-x-auto">
        <button
          onClick={() => setFilterSubject('')}
          className={`px-2.5 py-1 rounded-full text-xs font-medium transition-all flex-shrink-0 ${
            !filterSubject ? 'bg-hover-bg text-text-primary' : 'text-text-dim hover:text-text-secondary'
          }`}
        >
          {t('todoModule.all')}
        </button>
        {Object.entries(SUBJECTS).map(([k, v]) => {
          const todoCount = todos.filter(td => td.subject === k).length
          if (todoCount === 0) return null
          return (
            <button
              key={k}
              onClick={() => setFilterSubject(filterSubject === k ? '' : k)}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-all flex-shrink-0 ${
                filterSubject === k ? 'bg-hover-bg' : 'text-text-dim hover:text-text-secondary'
              }`}
              style={{ color: filterSubject === k ? v.color : undefined }}
            >
              <Icon icon={`${v.icon} text-[9px]`} />
              {t(v.labelKey)}
              <span className="opacity-50">({todoCount})</span>
            </button>
          )
        })}
      </div>

      {/* Study stats bar */}
      {(hasStudyData || !studyPlanCreated) && (
        <div className="flex items-center gap-4 px-5 py-2 border-b border-border flex-shrink-0 overflow-x-auto">
          {hasStudyData && Object.entries(stats).filter(([, s]) => s.total > 0).map(([key, s]) => {
            const subj = SUBJECTS[key]
            return (
              <div key={key} className="flex items-center gap-1.5 text-xs" style={{ color: subj?.color ?? 'var(--text-muted)' }}>
                <Icon icon={`${subj?.icon ?? 'fa-solid fa-circle'} text-[9px]`} />
                <span>{subj ? t(subj.labelKey) : key}</span>
                <span className="text-text-secondary">{s.done}/{s.total}</span>
                {s.minutes > 0 && (
                  <span className="text-text-dim">{s.minutes}min</span>
                )}
              </div>
            )
          })}
          <div className="ml-auto flex items-center gap-1.5">
            {studyPlanCreated ? (
              <span className="text-xs text-green-400/80 flex-shrink-0">
                <Icon icon="fa-solid fa-check mr-0.5" />
                {t('exam.taskCreated')}
              </span>
            ) : (
              <button
                onClick={handleCreateStudyPlan}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium transition-all text-text-muted hover:text-text-secondary hover:bg-hover-bg flex-shrink-0"
              >
                <Icon icon="fa-regular fa-calendar-plus text-2xs" />
                {t('exam.createStudyTask')}
              </button>
            )}
          </div>
        </div>
      )}

      <TodoList onOpenDetail={setSelectedTodoId} />
      </div>

      {/* C1：右栏详情面板 */}
      {selectedTodo && (
        <TodoDetailPanel todo={selectedTodo} onClose={() => setSelectedTodoId(null)} />
      )}
    </div>
  )
}

export default TodoModule
