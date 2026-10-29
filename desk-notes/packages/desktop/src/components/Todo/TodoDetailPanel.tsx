import React, { useState, useMemo } from 'react'
import Icon from '../Common/Icon'

import { useStore, formatDateTime, type TodoItem } from '@desk-notes/shared'

/**
 * C1（前后端对齐规划）：待办右栏详情面板。
 *
 * 对齐 chat/agent 三栏工作台范式：列表 + 右栏详情。
 * 数据模型见 shared/types TodoItem（priority/dueDate/reminderTime/parentId…）。
 *
 * i18n 说明：按用户确认不做英文界面，本组件直接使用中文文案。
 */

interface Props {
  todo: TodoItem
  onClose: () => void
}

// priority 语义：0=高（红）1=中（黄）2=低（灰）；undefined=无
const PRIORITY_OPTIONS: Array<{ level: 0 | 1 | 2 | undefined; label: string; color?: string }> = [
  { level: undefined, label: '无' },
  { level: 2, label: '低', color: '#64748b' },
  { level: 1, label: '中', color: '#fbbf24' },
  { level: 0, label: '高', color: '#f87171' },
]

function toDatetimeLocal(ts?: number): string {
  if (!ts) return ''
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const TodoDetailPanel: React.FC<Props> = ({ todo, onClose }) => {
  const todos = useStore(s => s.todos)
  const setPriority = useStore(s => s.setPriority)
  const setDueDate = useStore(s => s.setDueDate)
  const clearReminder = useStore(s => s.clearReminder)
  const toggleTodo = useStore(s => s.toggleTodo)
  const addSubtask = useStore(s => s.addSubtask)
  const [subtaskText, setSubtaskText] = useState('')

  const subtasks = useMemo(
    () => todos.filter(t => t.parentId === todo.id),
    [todos, todo.id],
  )

  const handleDueDateChange = (value: string) => {
    if (!value) {
      setDueDate(todo.id, undefined)
      return
    }
    const ts = new Date(value).getTime()
    if (!Number.isNaN(ts)) setDueDate(todo.id, ts)
  }

  const handleAddSubtask = () => {
    const text = subtaskText.trim()
    if (!text) return
    addSubtask(todo.id, text)
    setSubtaskText('')
  }

  const sectionTitle = 'text-2xs font-medium uppercase tracking-wider mb-1.5'

  return (
    <div
      className="w-[280px] flex-shrink-0 border-l flex flex-col min-h-0 overflow-hidden"
      style={{ borderColor: 'var(--panel-border)', background: 'var(--panel-bg)' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 h-10 border-b flex-shrink-0" style={{ borderColor: 'var(--separator)' }}>
        <span className="text-xs font-medium text-text-primary">任务详情</span>
        <button
          onClick={onClose}
          className="w-6 h-6 flex items-center justify-center rounded-md text-text-muted hover:text-text-primary hover:bg-hover-bg transition-all text-xs"
          title="关闭详情"
        >
          <Icon icon="fa-solid fa-xmark" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
        {/* 内容 */}
        <div className="text-sm leading-relaxed text-text-primary break-words">
          {todo.text}
        </div>

        {/* 优先级 */}
        <div>
          <div className={`${sectionTitle} text-text-muted`}>优先级</div>
          <div className="flex items-center gap-1.5">
            {PRIORITY_OPTIONS.map(opt => {
              const active = todo.priority === opt.level
              return (
                <button
                  key={opt.label}
                  onClick={() => setPriority(todo.id, opt.level)}
                  className={`px-2 py-1 rounded-md text-xs font-medium transition-all border ${
                    active ? '' : 'text-text-muted hover:text-text-secondary'
                  }`}
                  style={{
                    borderColor: active ? (opt.color ?? 'var(--text-muted)') : 'var(--panel-border)',
                    color: active ? (opt.color ?? 'var(--text-primary)') : undefined,
                    background: active ? 'var(--overlay-faint)' : 'transparent',
                  }}
                >
                  {opt.label}
                </button>
              )
            })}
          </div>
        </div>

        {/* 截止日期 */}
        <div>
          <div className={`${sectionTitle} text-text-muted`}>截止日期</div>
          <div className="flex items-center gap-1.5">
            <input
              type="datetime-local"
              value={toDatetimeLocal(todo.dueDate)}
              onChange={e => handleDueDateChange(e.target.value)}
              className="flex-1 min-w-0 px-2 py-1 rounded-md text-xs outline-none border"
              style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
            />
            {todo.dueDate && (
              <button
                onClick={() => setDueDate(todo.id, undefined)}
                className="px-2 py-1 rounded-md text-xs text-text-muted hover:text-text-primary transition-all"
                title="清除截止日期"
              >
                ✕
              </button>
            )}
          </div>
        </div>

        {/* 提醒 */}
        {todo.reminderTime && (
          <div>
            <div className={`${sectionTitle} text-text-muted`}>提醒</div>
            <div className="flex items-center justify-between gap-2 px-2 py-1.5 rounded-md" style={{ background: 'var(--overlay-faint)' }}>
              <span className="text-xs text-text-secondary flex items-center gap-1.5 min-w-0">
                <Icon icon="fa-regular fa-bell text-2xs" />
                <span className="truncate">{formatDateTime(todo.reminderTime)}</span>
              </span>
              <button
                onClick={() => clearReminder(todo.id)}
                className="shrink-0 text-xs text-text-muted hover:text-text-primary transition-all"
                title="清除提醒"
              >
                ✕
              </button>
            </div>
          </div>
        )}

        {/* 子任务 */}
        <div>
          <div className={`${sectionTitle} text-text-muted`}>子任务（{subtasks.length}）</div>
          <div className="space-y-1">
            {subtasks.map(st => (
              <div key={st.id} className="flex items-center gap-2 px-2 py-1 rounded-md group" style={{ background: 'var(--overlay-faint)' }}>
                <button
                  onClick={() => toggleTodo(st.id)}
                  className="w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 transition-colors"
                  style={{ borderColor: st.done ? 'var(--fluent-blue)' : 'var(--text-dim)' }}
                >
                  {st.done && <Icon icon="fa-solid fa-check text-[8px]" style={{ color: 'var(--fluent-blue)' }} />}
                </button>
                <span className={`text-xs min-w-0 truncate ${st.done ? 'line-through text-text-dim' : 'text-text-secondary'}`}>
                  {st.text}
                </span>
              </div>
            ))}
            <div className="flex items-center gap-1.5">
              <input
                value={subtaskText}
                onChange={e => setSubtaskText(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); handleAddSubtask() } }}
                placeholder="添加子任务…"
                className="flex-1 min-w-0 px-2 py-1 rounded-md text-xs outline-none border"
                style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
              />
              <button
                onClick={handleAddSubtask}
                disabled={!subtaskText.trim()}
                className="px-2 py-1 rounded-md text-xs text-fluent-blue disabled:opacity-30 transition-all"
              >
                添加
              </button>
            </div>
          </div>
        </div>

        {/* 元信息 */}
        <div>
          <div className={`${sectionTitle} text-text-muted`}>信息</div>
          <div className="space-y-1 text-xs text-text-muted">
            <div className="flex justify-between gap-2">
              <span>创建</span>
              <span className="text-text-secondary">{formatDateTime(todo.createdAt)}</span>
            </div>
            {todo.updatedAt && (
              <div className="flex justify-between gap-2">
                <span>更新</span>
                <span className="text-text-secondary">{formatDateTime(todo.updatedAt)}</span>
              </div>
            )}
            {todo.completedAt && (
              <div className="flex justify-between gap-2">
                <span>完成</span>
                <span className="text-text-secondary">{formatDateTime(todo.completedAt)}</span>
              </div>
            )}
            {todo.estimatedMinutes && (
              <div className="flex justify-between gap-2">
                <span>预估时长</span>
                <span className="text-text-secondary">{todo.estimatedMinutes} 分钟</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default TodoDetailPanel
