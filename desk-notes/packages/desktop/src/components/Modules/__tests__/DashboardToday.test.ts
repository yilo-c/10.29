import { describe, it, expect, beforeEach } from 'vitest'
import {
  COUNTDOWNS_LS_KEY,
  dateToInputValue,
  inputValueToDate,
  loadCountdowns,
  saveCountdowns,
  selectTodayTasks,
} from '../DashboardTypes'
import type { TodoItem } from '@desk-notes/shared'

function makeTodo(partial: Partial<TodoItem> & { id: string; text: string }): TodoItem {
  return { done: false, createdAt: 1000, ...partial }
}

function todayStart(): number {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

describe('selectTodayTasks（今天要处理聚合）', () => {
  it('按 overdue → dueToday → p0 分组聚合未完成任务', () => {
    const today = todayStart()
    const todos: TodoItem[] = [
      makeTodo({ id: 'done', text: '已完成', done: true, dueDate: today - 86400000 }),
      makeTodo({ id: 'overdue1', text: '逾期1', dueDate: today - 86400000 }),
      makeTodo({ id: 'overdue2', text: '逾期2', dueDate: today - 2 * 86400000 }),
      makeTodo({ id: 'due', text: '今日到期', dueDate: today + 3600000 }),
      makeTodo({ id: 'p0', text: '高优无期限', priority: 2 }),
      makeTodo({ id: 'p1', text: '普通无期限', priority: 1 }),
    ]
    const res = selectTodayTasks(todos)
    expect(res.overdue.map(t => t.id)).toEqual(['overdue2', 'overdue1'])
    expect(res.dueToday.map(t => t.id)).toEqual(['due'])
    expect(res.p0.map(t => t.id)).toEqual(['p0'])
    expect(res.total).toBe(4)
  })

  it('逾期任务不被无期限普通任务挤占', () => {
    const today = todayStart()
    const todos: TodoItem[] = [
      makeTodo({ id: 'overdue', text: '逾期', dueDate: today - 86400000 }),
      makeTodo({ id: 'normal', text: '新建普通', createdAt: 999999 }),
    ]
    const res = selectTodayTasks(todos)
    expect(res.overdue.map(t => t.id)).toContain('overdue')
    expect(res.p0.length).toBe(0)
    expect(res.total).toBe(1)
  })

  it('无相关任务时为空', () => {
    const res = selectTodayTasks([
      makeTodo({ id: 'a', text: '无期限', priority: 1 }),
      makeTodo({ id: 'b', text: '已完成', done: true }),
    ])
    expect(res.total).toBe(0)
    expect(res.overdue).toEqual([])
  })
})

describe('倒计时配置存取', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('无配置时返回 null（调用方回退默认单条）', () => {
    expect(loadCountdowns()).toBeNull()
  })

  it('保存后能读回，且可多条第（localStorage 持久化）', () => {
    saveCountdowns([
      { id: 'a', label: '考研', date: new Date(2026, 11, 21).getTime() },
      { id: 'b', label: '答辩', date: new Date(2026, 5, 1).getTime() },
    ])
    const loaded = loadCountdowns()
    expect(loaded).not.toBeNull()
    expect(loaded!.length).toBe(2)
    expect(loaded![1].label).toBe('答辩')
    expect(localStorage.getItem(COUNTDOWNS_LS_KEY)).toContain('答辩')
  })

  it('保存空数组视为无配置', () => {
    saveCountdowns([])
    expect(loadCountdowns()).toBeNull()
  })
})

describe('date <-> input 转换', () => {
  it('dateToInputValue 输出 YYYY-MM-DD 且 inputValueToDate 可还原同日', () => {
    const ms = new Date(2026, 11, 21).getTime()
    const iso = dateToInputValue(ms)
    expect(iso).toBe('2026-12-21')
    const back = new Date(inputValueToDate(iso))
    expect(back.getFullYear()).toBe(2026)
    expect(back.getMonth()).toBe(11)
    expect(back.getDate()).toBe(21)
  })
})
