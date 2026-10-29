import { useState } from 'react'
import { useStore, uid, type QimenDomainKey } from '@desk-notes/shared'
import { Sheet, Button, Chip } from '../../ui'

interface NewTodoSheetProps {
  open: boolean
  onClose: () => void
  category?: string
}

const PRIORITIES = [
  { value: 2 as 0 | 1 | 2, label: '低' },
  { value: 1 as 0 | 1 | 2, label: '中' },
  { value: 0 as 0 | 1 | 2, label: '高' },
]

const TASK_TYPES: { key: QimenDomainKey; label: string }[] = [
  { key: 'career', label: '事业' },
  { key: 'travel', label: '出行' },
  { key: 'wealth', label: '求财' },
  { key: 'study', label: '学业' },
  { key: 'life', label: '健康' },
  { key: 'life', label: '生活' },
  { key: 'relationship', label: '感情' },
  { key: 'other', label: '其他' },
]

export default function NewTodoSheet({ open, onClose, category }: NewTodoSheetProps) {
  const [text, setText] = useState('')
  const [priority, setPriority] = useState<0 | 1 | 2>(1)
  const [taskType, setTaskType] = useState<QimenDomainKey | null>(null)
  const [hasDate, setHasDate] = useState(false)
  const [dueDate, setDueDate] = useState('')

  const addTodo = useStore(s => s.addTodo)
  const setPriorityMethod = useStore(s => s.setPriority)

  const handleSubmit = () => {
    const trimmed = text.trim()
    if (!trimmed) return

    const id = uid()
    addTodo(trimmed, category)
    // Since addTodo creates with default priority, set it after
    setPriorityMethod(id, priority)

    if (taskType) {
      useStore.getState().setQimenEvent(id, {
        domain: taskType,
        scenario: undefined,
      })
    }

    if (hasDate && dueDate) {
      useStore.getState().setDueDate(id, new Date(dueDate).getTime())
    }

    setText('')
    setPriority(1)
    setTaskType(null)
    setHasDate(false)
    setDueDate('')
    onClose()
  }

  return (
    <Sheet open={open} onClose={onClose} title="新建待办">
      {/* Title input */}
      <input
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="待办内容"
        autoFocus
        style={{
          width: '100%',
          padding: '12px 14px',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--border-card)',
          background: 'var(--bg-tertiary)',
          color: 'var(--text-primary)',
          fontSize: 'var(--text-body)',
          outline: 'none',
          marginBottom: 16,
        }}
        onKeyDown={e => e.key === 'Enter' && handleSubmit()}
      />

      {/* Task type */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)', marginBottom: 8 }}>
          事项类型（选填，用于关联参考信息）
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {TASK_TYPES.map(t => (
            <Chip
              key={t.key}
              type="tag"
              selected={taskType === t.key}
              label={t.label}
              onClick={() => setTaskType(taskType === t.key ? null : t.key)}
            />
          ))}
        </div>
      </div>

      {/* Date */}
      <div style={{ marginBottom: 16 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            marginBottom: 8,
          }}
        >
          <label style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={hasDate}
              onChange={e => setHasDate(e.target.checked)}
              style={{ accentColor: 'var(--gold-primary)' }}
            />
            设置日期
          </label>
        </div>
        {hasDate && (
          <input
            type="date"
            value={dueDate}
            onChange={e => setDueDate(e.target.value)}
            style={{
              width: '100%',
              padding: '10px 14px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-card)',
              background: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              fontSize: 'var(--text-body)',
              outline: 'none',
            }}
          />
        )}
      </div>

      {/* Priority */}
      <div style={{ marginBottom: 20 }}>
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)', marginBottom: 8 }}>
          优先级
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {PRIORITIES.map(p => (
            <button
              key={p.value}
              onClick={() => setPriority(p.value)}
              style={{
                flex: 1,
                padding: '8px',
                borderRadius: 'var(--radius-sm)',
                border: `1px solid ${priority === p.value ? 'var(--gold-border)' : 'var(--border-card)'}`,
                background: priority === p.value ? 'var(--gold-surface)' : 'var(--bg-tertiary)',
                color: priority === p.value ? 'var(--gold-primary)' : 'var(--text-secondary)',
                fontSize: 'var(--text-small)',
                cursor: 'pointer',
                transition: 'all var(--duration-sm) var(--ease-out)',
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 12 }}>
        <Button variant="ghost" onClick={onClose} style={{ flex: 1 }}>
          取消
        </Button>
        <Button
          variant="primary"
          onClick={handleSubmit}
          disabled={!text.trim()}
          style={{ flex: 1 }}
        >
          确定
        </Button>
      </div>
    </Sheet>
  )
}
