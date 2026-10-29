import { useStore, type TodoItem, type QimenDomainKey } from '@desk-notes/shared'
import { Sheet } from '../../ui'

interface TodoActionSheetProps {
  item: TodoItem | null
  open: boolean
  onClose: () => void
  onStartPomodoro?: (id: string, text: string) => void
}

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

export default function TodoActionSheet({ item, open, onClose, onStartPomodoro }: TodoActionSheetProps) {
  const setPriority = useStore(s => s.setPriority)
  const setQimenEvent = useStore(s => s.setQimenEvent)
  const deleteTodo = useStore(s => s.deleteTodo)

  if (!item) return null

  const handlePriority = (level: 0 | 1 | 2 | undefined) => {
    setPriority(item.id, level)
  }

  const handleTaskType = (domain: QimenDomainKey) => {
    if (item.qimenEvent?.domain === domain) {
      setQimenEvent(item.id, undefined)
    } else {
      setQimenEvent(item.id, { domain, scenario: undefined })
    }
  }

  return (
    <Sheet open={open} onClose={onClose} title="">
      <div style={{ fontSize: 'var(--text-h2)', fontWeight: 600, marginBottom: 4 }}>
        {item.text}
      </div>

      {/* Pomodoro button */}
      <button
        onClick={() => onStartPomodoro?.(item.id, item.text)}
        style={{
          marginTop: 12,
          padding: '10px 16px',
          width: '100%',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid var(--gold-border)',
          background: 'var(--gold-surface)',
          color: 'var(--gold-primary)',
          fontSize: 'var(--text-body)',
          fontWeight: 500,
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
        }}
      >
        🍅 开始番茄钟
      </button>

      {/* Priority */}
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)', marginBottom: 8 }}>
          优先级
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {[
            { value: 2, label: '低' },
            { value: 1, label: '中' },
            { value: 0, label: '高' },
          ].map(p => (
            <button
              key={p.value}
              onClick={() => handlePriority(p.value as 0 | 1 | 2)}
              style={{
                flex: 1,
                padding: '8px',
                borderRadius: 'var(--radius-sm)',
                border: `1px solid ${item.priority === p.value ? 'var(--gold-border)' : 'var(--border-card)'}`,
                background: item.priority === p.value ? 'var(--gold-surface)' : 'var(--bg-tertiary)',
                color: item.priority === p.value ? 'var(--gold-primary)' : 'var(--text-secondary)',
                fontSize: 'var(--text-small)',
                cursor: 'pointer',
              }}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* Task type for astrology reference */}
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 'var(--text-small)', color: 'var(--text-tertiary)', marginBottom: 8 }}>
          事项类型
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {TASK_TYPES.map(t => (
            <button
              key={t.key}
              onClick={() => handleTaskType(t.key)}
              style={{
                padding: '4px 12px',
                borderRadius: 'var(--radius-sm)',
                border: `1px solid ${item.qimenEvent?.domain === t.key ? 'var(--gold-border)' : 'var(--border-card)'}`,
                background: item.qimenEvent?.domain === t.key ? 'var(--gold-surface)' : 'var(--bg-tertiary)',
                color: item.qimenEvent?.domain === t.key ? 'var(--gold-primary)' : 'var(--text-secondary)',
                fontSize: 'var(--text-small)',
                cursor: 'pointer',
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Reference info (placeholder — will be real in Phase 2) */}
      {item.qimenEvent && (
        <div
          style={{
            marginTop: 16,
            padding: 12,
            borderRadius: 'var(--radius-sm)',
            background: 'var(--gold-surface)',
            border: '1px solid var(--gold-border)',
            fontSize: 'var(--text-small)',
            color: 'var(--text-secondary)',
          }}
        >
          <div style={{ color: 'var(--gold-primary)', marginBottom: 4 }}>
            传统参考
          </div>
          当前时辰对&quot;{TASK_TYPES.find(t => t.key === item.qimenEvent!.domain)?.label}&quot;类事项为较顺利时段。
          吉方在西南。
          <div style={{ fontSize: 'var(--text-tiny)', color: 'var(--text-tertiary)', marginTop: 4 }}>
            ℹ️ 此信息依据传统历法理论，供您参考
          </div>
        </div>
      )}

      {/* Delete */}
      <button
        onClick={() => { deleteTodo(item.id); onClose() }}
        style={{
          marginTop: 20,
          padding: '10px',
          width: '100%',
          borderRadius: 'var(--radius-sm)',
          border: '1px solid rgba(229,115,115,0.3)',
          background: 'transparent',
          color: '#e57373',
          fontSize: 'var(--text-body)',
          cursor: 'pointer',
        }}
      >
        删除待办
      </button>
    </Sheet>
  )
}
