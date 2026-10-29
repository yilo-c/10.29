import { useState, useMemo } from 'react'
import { useStore, getHuangli } from '@desk-notes/shared'
import { Sheet, Button, Chip, IconBell } from '../../ui'
import type { QimenDomainKey } from '@desk-notes/shared'
import { useScreenWake } from '../../../hooks/useScreenWake'
import { swallowError } from '../../../lib/swallowError'
import { createReminder } from '../../../lib/bridges/reminderBridge'

interface NewTodoSheetProps {
  open: boolean
  onClose: () => void
  category?: string
}

const PRIORITIES: { value: 0 | 1 | 2; label: string }[] = [
  { value: 2, label: '低' },
  { value: 1, label: '中' },
  { value: 0, label: '高' },
]

const TASK_TYPES: { key: QimenDomainKey; label: string }[] = [
  { key: 'career', label: '事业' },
  { key: 'wealth', label: '求财' },
  { key: 'travel', label: '出行' },
  { key: 'study', label: '学业' },
  { key: 'relationship', label: '感情' },
  { key: 'life', label: '生活' },
  { key: 'other', label: '其他' },
]

const DOMAIN_KEYWORDS: Record<string, string[]> = {
  career: ['签约', '开市', '交易', '立券', '赴任', '开工', '上梁', '竖柱', '纳畜', '安门'],
  wealth: ['纳财', '开市', '交易', '求财', '立券', '纳畜'],
  relationship: ['嫁娶', '纳采', '订婚', '求嗣', '会友', '裁衣', '冠笄'],
  travel: ['出行', '移徙', '入宅', '远行'],
  study: ['入学', '开光', '求医', '赴考'],
  life: ['求医', '安葬', '破土', '动土', '修造', '祈福', '祭祀', '安床', '栽种'],
}

function getRelevantItems(yi: string[], ji: string[], domain: string) {
  const keywords = DOMAIN_KEYWORDS[domain] || []
  if (keywords.length === 0) return null
  const yiMatch = yi.filter(item => keywords.some(k => item.includes(k)))
  const jiMatch = ji.filter(item => keywords.some(k => item.includes(k)))
  if (yiMatch.length === 0 && jiMatch.length === 0) return null
  return { yi: yiMatch, ji: jiMatch }
}

export default function NewTodoSheet({ open, onClose, category }: NewTodoSheetProps) {
  const [text, setText] = useState('')
  const [priority, setPriority] = useState<0 | 1 | 2>(1)
  const [taskType, setTaskType] = useState<QimenDomainKey | null>(null)
  const [hasDate, setHasDate] = useState(false)
  const [dueDate, setDueDate] = useState('')
  const [hasReminder, setHasReminder] = useState(false)
  const [reminderTime, setReminderTime] = useState('')

  useScreenWake(open)

  const addTodo = useStore(s => s.addTodo)
  const setPriorityFn = useStore(s => s.setPriority)
  const setQimenEvent = useStore(s => s.setQimenEvent)
  const setDueDateFn = useStore(s => s.setDueDate)
  const setReminderTimeFn = useStore(s => s.setReminderTime)

  const huangli = useMemo(() => getHuangli(new Date()), [])

  const relevantRef = useMemo(() => {
    if (!taskType || !huangli) return null
    return getRelevantItems(huangli.yiJi.yi, huangli.yiJi.ji, taskType)
  }, [taskType, huangli])

  const handleSubmit = () => {
    const trimmed = text.trim()
    if (!trimmed) return

    addTodo(trimmed, category)

    const newId = useStore.getState().todos[0]?.id
    if (newId) {
      if (priority !== 1) setPriorityFn(newId, priority)
      if (taskType) setQimenEvent(newId, { domain: taskType })
      if (hasDate && dueDate) setDueDateFn(newId, new Date(dueDate).getTime())
      if (hasReminder && reminderTime) {
      setReminderTimeFn(newId, new Date(reminderTime).getTime())
      // 通过 reminderBridge → callRpc（WS 优先 + HTTP 回落）把提醒注册到桌面端。
      createReminder({
        title: trimmed,
        time: new Date(reminderTime),
        todo_id: newId,
      }).catch(e => swallowError('NewTodoSheet:reminder', e))
    }
    }

    setText('')
    setPriority(1)
    setTaskType(null)
    setHasDate(false)
    setDueDate('')
    setHasReminder(false)
    setReminderTime('')
    onClose()
  }

  return (
    <Sheet open={open} onClose={onClose} title="新建待办">
      <input
        value={text}
        onChange={e => setText(e.target.value)}
        placeholder="想做些什么？"
        autoFocus
        style={{
          width: '100%',
          padding: '12px 0',
          border: 'none',
          borderBottom: '1px solid var(--border-subtle)',
          background: 'transparent',
          color: 'var(--text-primary)',
          fontSize: 'var(--text-body)',
          outline: 'none',
          marginBottom: 16,
        }}
        onKeyDown={e => e.key === 'Enter' && handleSubmit()}
      />

      <div style={{ marginBottom: 16 }}>
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          marginBottom: 8,
        }}>
          事项类型
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {TASK_TYPES.map(t => (
            <Chip
              key={t.label}
              type="tag"
              selected={taskType === t.key}
              label={t.label}
              onClick={() => setTaskType(taskType === t.key ? null : t.key)}
            />
          ))}
        </div>
      </div>

      {taskType && relevantRef && (
        <div style={{
          marginBottom: 16,
          padding: 12,
          borderRadius: 'var(--radius-md)',
          background: 'var(--bg-secondary)',
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-secondary)',
          animation: 'fadeInUp 0.15s ease',
        }}>
          {relevantRef.yi.length > 0 && (
            <div style={{ marginBottom: relevantRef.ji.length > 0 ? 4 : 0 }}>
              <span style={{ color: 'var(--tone-smooth)', fontWeight: 500 }}>宜 </span>
              {relevantRef.yi.join(' · ')}
            </div>
          )}
          {relevantRef.ji.length > 0 && (
            <div>
              <span style={{ color: 'var(--tone-caution)', fontWeight: 500 }}>忌 </span>
              {relevantRef.ji.join(' · ')}
            </div>
          )}
          <div style={{
            fontSize: 10,
            color: 'var(--text-disabled)',
            marginTop: 6,
          }}>
            依据传统历法理论，仅供决策参考
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 12, marginBottom: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          cursor: 'pointer',
        }}>
          <input
            type="checkbox"
            checked={hasDate}
            onChange={e => setHasDate(e.target.checked)}
            style={{ accentColor: 'var(--accent)' }}
          />
          设置日期
        </label>
        {hasDate && (
          <input
            type="date"
            value={dueDate}
            onChange={e => setDueDate(e.target.value)}
            style={{
              flex: 1,
              padding: '6px 10px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)',
              background: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              fontSize: 'var(--text-footnote)',
              outline: 'none',
            }}
          />
        )}
        <label style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          cursor: 'pointer',
        }}>
          <input
            type="checkbox"
            checked={hasReminder}
            onChange={e => setHasReminder(e.target.checked)}
            style={{ accentColor: 'var(--accent)' }}
          />
          <IconBell size={14} />
          提醒
        </label>
        {hasReminder && (
          <input
            type="datetime-local"
            value={reminderTime}
            onChange={e => setReminderTime(e.target.value)}
            style={{
              flex: 1,
              padding: '6px 10px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)',
              background: 'var(--bg-secondary)',
              color: 'var(--text-primary)',
              fontSize: 'var(--text-footnote)',
              outline: 'none',
            }}
          />
        )}
      </div>

      <div style={{ marginBottom: 20 }}>
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          marginBottom: 8,
        }}>
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
                borderRadius: 'var(--radius-md)',
                border: `1.5px solid ${priority === p.value ? 'var(--accent)' : 'var(--border-default)'}`,
                background: priority === p.value ? 'var(--accent-surface)' : 'transparent',
                color: priority === p.value ? 'var(--accent)' : 'var(--text-secondary)',
                fontSize: 'var(--text-footnote)',
                fontWeight: 500,
                cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

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
          添加
        </Button>
      </div>
    </Sheet>
  )
}
