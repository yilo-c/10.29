import React, { useState, useMemo, useRef, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import type { TodoItem } from '../../types'
import { useTranslation } from '../../i18n'
import { getWeekDays } from './calendarUtils'

const HOURS = Array.from({ length: 18 }, (_, i) => i + 6) // 06:00 – 23:00
const ROW_HEIGHT = 36

interface CreateSlot {
  day: Date
  hour: number
}

interface WeekViewProps {
  weekStart: Date
  selectedDate: Date | null
  onSelectDate: (date: Date) => void
  todosByDate: Map<string, TodoItem[]>
  weekdays: readonly string[]
  today: Date
}

function formatHour(h: number): string {
  return `${h.toString().padStart(2, '0')}:00`
}

/** Extract hour from timestamp; -1 = all-day (no time component) */
function getHourFromDueDate(ts: number | undefined): number {
  if (!ts) return -1
  const d = new Date(ts)
  if (d.getHours() === 0 && d.getMinutes() === 0) return -1
  return d.getHours()
}

/**
 * A single hour × day slot button rendered inside the time grid.
 * Shows existing todo chips or a quick-add "+" button when empty.
 */
const SlotCell: React.FC<{
  day: Date
  hour: number
  todos: TodoItem[]
  isCreate: boolean
  createText: string
  onTextChange: (v: string) => void
  onConfirm: () => void
  onKeyDown: (e: React.KeyboardEvent) => void
  onClick: (e: React.MouseEvent) => void
}> = ({ day, hour, todos, isCreate, createText, onTextChange, onConfirm, onKeyDown, onClick }) => {
  const setDueDate = useStore(s => s.setDueDate)
  const [dragOver, setDragOver] = useState(false)

  return (
    <div
      onClick={isCreate ? undefined : onClick}
      onDrop={(e) => {
        e.preventDefault()
        e.stopPropagation()
        const todoId = e.dataTransfer.getData('text/plain')
        if (todoId) {
          const due = new Date(day)
          due.setHours(hour, 0, 0, 0)
          setDueDate(todoId, due.getTime())
        }
        setDragOver(false)
      }}
      onDragOver={(e) => {
        e.preventDefault()
        e.dataTransfer.dropEffect = 'move'
        setDragOver(true)
      }}
      onDragLeave={() => setDragOver(false)}
      className="relative border-r border-b transition-all cursor-pointer select-none"
      style={{
        flex: 1,
        borderColor: 'var(--panel-border)',
        background: dragOver ? 'rgba(96,165,250,0.06)' : undefined,
      }}
    >
      {isCreate ? (
        <div className="absolute inset-0 z-10 px-0.5" style={{ paddingTop: 1 }}>
          <input
            ref={el => el?.focus()}
            value={createText}
            onChange={e => onTextChange(e.target.value)}
            onKeyDown={onKeyDown}
            onBlur={onConfirm}
            placeholder="..."
            className="w-full h-[30px] px-1 rounded text-xs outline-none border"
            style={{
              background: 'rgba(30,30,60,0.95)',
              borderColor: 'rgba(96,165,250,0.5)',
              color: 'var(--text-primary)',
            }}
            onClick={e => e.stopPropagation()}
          />
        </div>
      ) : (
        <>
          {/* Todo chips */}
          {todos.map(todo => (
            <div
              key={todo.id}
              draggable
              onDragStart={(e) => {
                e.dataTransfer.setData('text/plain', todo.id)
                e.dataTransfer.effectAllowed = 'move'
              }}
              className="flex items-center gap-0.5 px-0.5 rounded-sm truncate cursor-grab active:cursor-grabbing transition-all hover:brightness-125"
              style={{
                height: ROW_HEIGHT - 5,
                marginTop: 1,
                marginLeft: 0.5,
                marginRight: 0.5,
                background: todo.done ? 'rgba(52,211,153,0.1)' : 'rgba(96,165,250,0.12)',
                borderLeft: `2px solid ${todo.done ? 'rgba(52,211,153,0.4)' : 'rgba(96,165,250,0.5)'}`,
                opacity: todo.done ? 0.5 : 1,
              }}
              title={todo.text}
            >
              <span className="text-[8px] truncate leading-none" style={{ color: 'var(--text-secondary)' }}>
                {todo.text}
              </span>
              {todo.priority !== undefined && todo.priority <= 1 && (
                <span className={`text-[6px] shrink-0 ${todo.priority === 0 ? 'text-red-400' : 'text-yellow-400'}`}>
                  P{todo.priority}
                </span>
              )}
            </div>
          ))}

          {/* Quick-add "+" on empty slots */}
          {todos.length === 0 && (
            <div
              onClick={(e) => { e.stopPropagation(); onClick(e) }}
              className="absolute top-0 right-0 w-3 h-3 flex items-center justify-center rounded-sm opacity-0 hover:opacity-100 transition-all"
              style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}
            >
              <Icon icon="fa-solid fa-plus text-[6px]" />
            </div>
          )}
        </>
      )}
    </div>
  )
}

const WeekView: React.FC<WeekViewProps> = ({
  weekStart, selectedDate, onSelectDate,
  todosByDate, weekdays, today,
}) => {
  const setDueDate = useStore(s => s.setDueDate)
  const addTodo = useStore(s => s.addTodo)
  const { t } = useTranslation()

  const [createSlot, setCreateSlot] = useState<CreateSlot | null>(null)
  const [createText, setCreateText] = useState('')
  const gridRef = useRef<HTMLDivElement>(null)
  const [currentTimePos, setCurrentTimePos] = useState<number | null>(null)
  const updateTimerRef = useRef<number | null>(null)

  const days = useMemo(() => getWeekDays(weekStart), [weekStart])

  // Auto-scroll to current time on mount
  useEffect(() => {
    const now = new Date()
    const h = now.getHours()
    if (h >= 6 && h <= 23) {
      const pos = (h - 6) * ROW_HEIGHT + (now.getMinutes() / 60) * ROW_HEIGHT
      setCurrentTimePos(pos)
      requestAnimationFrame(() => {
        if (gridRef.current) {
          gridRef.current.scrollTop = Math.max(0, pos - 120)
        }
      })
    }
  }, [])

  // Update current time indicator every minute
  useEffect(() => {
    const tick = () => {
      const now = new Date()
      const h = now.getHours()
      if (h >= 6 && h <= 23) {
        setCurrentTimePos((h - 6) * ROW_HEIGHT + (now.getMinutes() / 60) * ROW_HEIGHT)
      } else {
        setCurrentTimePos(null)
      }
    }
    updateTimerRef.current = window.setInterval(tick, 60000)
    return () => {
      if (updateTimerRef.current !== null) clearInterval(updateTimerRef.current)
    }
  }, [])

  // All-day todos per day (no specific hour)
  const allDayMap = useMemo(() => {
    const map = new Map<string, TodoItem[]>()
    for (const [key, items] of todosByDate) {
      const allDay = items.filter(t => getHourFromDueDate(t.dueDate) === -1)
      if (allDay.length > 0) map.set(key, allDay)
    }
    return map
  }, [todosByDate])

  const handleSlotClick = useCallback((day: Date, hour: number, e: React.MouseEvent) => {
    e.stopPropagation()
    onSelectDate(day)
    setCreateSlot({ day, hour })
    setCreateText('')
  }, [onSelectDate])

  const handleCreateConfirm = useCallback(() => {
    if (!createSlot) return
    const text = createText.trim()
    if (text) {
      const dueDate = new Date(createSlot.day)
      dueDate.setHours(createSlot.hour, 0, 0, 0)
      addTodo(text)
      const { todos } = useStore.getState()
      const newTodo = todos.filter(t => !t.dueDate).sort((a, b) => b.createdAt - a.createdAt)[0]
      if (newTodo) setDueDate(newTodo.id, dueDate.getTime())
    }
    setCreateSlot(null)
    setCreateText('')
  }, [createSlot, createText, addTodo, setDueDate])

  const handleCreateKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleCreateConfirm()
    else if (e.key === 'Escape') { setCreateSlot(null); setCreateText('') }
  }, [handleCreateConfirm])

  // Click outside grid → commit or cancel
  useEffect(() => {
    if (!createSlot) return
    const handler = (e: MouseEvent) => {
      if (gridRef.current && !gridRef.current.contains(e.target as Node)) {
        if (createText.trim()) handleCreateConfirm()
        else { setCreateSlot(null); setCreateText('') }
      }
    }
    document.addEventListener('mousedown', handler, true)
    return () => document.removeEventListener('mousedown', handler, true)
  }, [createSlot, createText, handleCreateConfirm])

  const todayColIndex = days.findIndex(d =>
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate()
  )

  return (
    <div className="flex flex-col">
      {/* ── Day header row ── */}
      <div className="flex border-b" style={{ borderColor: 'var(--panel-border)' }}>
        <div className="shrink-0" style={{ width: 48 }} />
        {days.map((day, i) => {
          const isToday = todayColIndex === i
          const isSel = selectedDate && day.toDateString() === selectedDate.toDateString()
          return (
            <div
              key={i}
              onClick={() => onSelectDate(day)}
              className={`py-1.5 text-center cursor-pointer transition-all border-r ${
                isSel ? 'bg-fluent-blue/15' : 'hover:bg-white/[0.04]'
              }`}
              style={{
                flex: 1,
                borderColor: 'var(--panel-border)',
                background: isToday && !isSel ? 'rgba(96,165,250,0.08)' : undefined,
              }}
            >
              <div className="text-2xs leading-tight" style={{ color: 'var(--text-dim)' }}>
                {weekdays[i]}
              </div>
              <div className={`text-base leading-tight mt-0.5 ${
                isToday ? 'font-bold text-fluent-blue' : ''
              }`} style={{ color: isToday ? undefined : 'var(--text-secondary)' }}>
                {day.getDate()}
              </div>
            </div>
          )
        })}
      </div>

      {/* ── All-day section ── */}
      {days.some(d => (allDayMap.get(d.toDateString()) || []).length > 0) && (
        <div className="flex border-b" style={{ borderColor: 'var(--panel-border)' }}>
          <div
            className="shrink-0 flex items-start justify-end pr-1.5 pt-1 border-r"
            style={{ width: 48, borderColor: 'var(--panel-border)' }}
          >
            <span className="text-[7px] leading-none" style={{ color: 'var(--text-dim)' }}>
              {t('calendar.allDay')}
            </span>
          </div>
          {days.map((day, i) => {
            const items = allDayMap.get(day.toDateString()) || []
            return (
              <div key={i} className="border-r px-1 py-0.5" style={{ flex: 1, borderColor: 'var(--panel-border)', minHeight: 22 }}>
                {items.map(t => (
                  <div
                    key={t.id}
                    draggable
                    onDragStart={(e) => {
                      e.dataTransfer.setData('text/plain', t.id)
                      e.dataTransfer.effectAllowed = 'move'
                    }}
                    className="text-[8px] truncate rounded-sm px-1 mb-0.5 cursor-grab active:cursor-grabbing"
                    style={{
                      background: 'rgba(167,139,250,0.12)',
                      color: 'var(--text-secondary)',
                      borderLeft: '2px solid rgba(167,139,250,0.4)',
                      opacity: t.done ? 0.5 : 1,
                      textDecoration: t.done ? 'line-through' : 'none',
                    }}
                    title={t.text}
                  >
                    {t.text}
                  </div>
                ))}
              </div>
            )
          })}
        </div>
      )}

      {/* ── Scrollable time grid ── */}
      <div
        ref={gridRef}
        className="overflow-y-auto"
        style={{ maxHeight: 480 }}
        onClick={() => { if (createSlot && !createText.trim()) { setCreateSlot(null) } }}
      >
        <div className="relative" style={{ minHeight: HOURS.length * ROW_HEIGHT }}>
          {/* Current time indicator line */}
          {currentTimePos !== null && todayColIndex >= 0 && (
            <div className="absolute left-0 right-0 z-20 pointer-events-none" style={{ top: currentTimePos }}>
              <div className="flex items-center">
                <div className="w-1.5 h-1.5 rounded-full bg-red-400 -ml-[3px]" />
                <div className="flex-1 h-px bg-red-400/60" />
              </div>
            </div>
          )}

          {/* Hour rows */}
          {HOURS.map(hour => (
            <div key={hour} className="flex" style={{ height: ROW_HEIGHT }}>
              {/* Time label */}
              <div
                className="flex items-start justify-end pr-1.5 shrink-0 border-r select-none"
                style={{ width: 48, borderColor: 'var(--panel-border)', paddingTop: 2 }}
              >
                <span className="text-[8px] leading-none" style={{ color: 'var(--text-dim)' }}>
                  {formatHour(hour)}
                </span>
              </div>

              {/* 7 day cells */}
              {days.map((day, di) => {
                const key = day.toDateString()
                const todos = (todosByDate.get(key) || []).filter(t => getHourFromDueDate(t.dueDate) === hour)
                const isCreate = createSlot !== null
                  && day.toDateString() === createSlot.day.toDateString()
                  && createSlot.hour === hour

                return (
                  <SlotCell
                    key={di}
                    day={day}
                    hour={hour}
                    todos={todos}
                    isCreate={isCreate}
                    createText={createText}
                    onTextChange={setCreateText}
                    onConfirm={handleCreateConfirm}
                    onKeyDown={handleCreateKeyDown}
                    onClick={(e) => handleSlotClick(day, hour, e)}
                  />
                )
              })}
            </div>
          ))}

          {/* Today column past-hour dimming */}
          {todayColIndex >= 0 && currentTimePos !== null && (
            <div
              className="absolute pointer-events-none"
              style={{
                left: `${(todayColIndex + 1) / (days.length + 1) * 100}%`,
                width: `${100 / (days.length + 1)}%`,
                height: currentTimePos,
                top: 0,
                background: 'rgba(0,0,0,0.06)',
              }}
            />
          )}
        </div>
      </div>

      {/* ── Bottom legend ── */}
      <div className="flex items-center gap-3 mt-2 pt-2 border-t" style={{ borderColor: 'var(--panel-border)' }}>
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-sm" style={{ background: 'rgba(96,165,250,0.3)', borderLeft: '2px solid rgba(96,165,250,0.6)' }} />
          <span className="text-[8px]" style={{ color: 'var(--text-muted)' }}>{t('calendar.pendingCount')}</span>
        </div>
        <div className="flex items-center gap-1">
          <div className="w-2 h-2 rounded-sm" style={{ background: 'rgba(167,139,250,0.3)', borderLeft: '2px solid rgba(167,139,250,0.6)' }} />
          <span className="text-[8px]" style={{ color: 'var(--text-muted)' }}>{t('calendar.allDay')}</span>
        </div>
        <div className="flex items-center gap-1 ml-auto">
          <Icon icon="fa-solid fa-plus text-[6px]" style={{ color: 'var(--brand)' }} />
          <span className="text-[8px]" style={{ color: 'var(--text-muted)' }}>{t('calendar.createTodoHint')}</span>
        </div>
      </div>
    </div>
  )
}

export default WeekView
