import { useState, useMemo, useRef, useEffect } from 'react'
import { useStore, getHuangli } from '@desk-notes/shared'
import { Sheet, Button, Chip, IconTrash, IconBell, IconX, IconPlus } from '../../ui'
import type { TodoItem, QimenDomainKey } from '@desk-notes/shared'

function toDatetimeLocal(ts: number): string {
  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

interface TodoActionSheetProps {
  item: TodoItem | null
  open: boolean
  onClose: () => void
}

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
  career: ['签约', '开市', '交易', '立券', '赴任', '开工', '上梁', '竖柱'],
  wealth: ['纳财', '开市', '交易', '求财', '立券'],
  relationship: ['嫁娶', '纳采', '订婚', '求嗣', '会友'],
  travel: ['出行', '移徙', '入宅', '远行'],
  study: ['入学', '开光', '求医'],
  life: ['求医', '安葬', '破土', '动土', '修造', '祈福', '祭祀'],
}

export default function TodoActionSheet({ item, open, onClose }: TodoActionSheetProps) {
  const editTodo = useStore(s => s.editTodo)
  const setPriority = useStore(s => s.setPriority)
  const setQimenEvent = useStore(s => s.setQimenEvent)
  const deleteTodo = useStore(s => s.deleteTodo)
  const setReminderTimeFn = useStore(s => s.setReminderTime)
  const clearReminderFn = useStore(s => s.clearReminder)
  const todos = useStore(s => s.todos)
  const addSubtask = useStore(s => s.addSubtask)
  const toggleTodo = useStore(s => s.toggleTodo)
  const setRepeatInterval = useStore(s => s.setRepeatInterval)
  const moveTodoToPosition = useStore(s => s.moveTodoToPosition)
  const moveTodoToCategory = useStore(s => s.moveTodoToCategory)
  const categories = useStore(s => s.categories)

  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState('')
  const [showReminderPicker, setShowReminderPicker] = useState(false)
  const [reminderTime, setReminderTime] = useState('')
  const [subtaskText, setSubtaskText] = useState('')
  const reminderRef = useRef<HTMLDivElement>(null)

  // Sync state when item changes
  useEffect(() => {
    if (open && item) {
      setReminderTime(item.reminderTime ? toDatetimeLocal(item.reminderTime) : '')
      setShowReminderPicker(false)
      setEditing(false)
      setEditText(item.text || '')
      setSubtaskText('')
    }
  }, [open, item?.id, item?.text])

  // Scroll reminder picker into view when activated
  useEffect(() => {
    if (showReminderPicker && reminderRef.current) {
      reminderRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [showReminderPicker])

  if (!item) return null

  const huangli = useMemo(() => getHuangli(new Date()), [])
  const subtasks = useMemo(() =>
    todos.filter(t => t.parentId === item.id),
    [todos, item.id],
  )
  const subtaskDone = subtasks.filter(t => t.done).length
  const activeTodos = useMemo(() =>
    todos.filter(t => !t.done && !t.parentId)
      .sort((a, b) => (a.sortOrder ?? a.createdAt) - (b.sortOrder ?? b.createdAt)),
    [todos],
  )
  const currentIndex = activeTodos.findIndex(t => t.id === item.id)
  const activeCount = activeTodos.length

  const domain = item.qimenEvent?.domain
  const keywords = domain ? DOMAIN_KEYWORDS[domain] || [] : []
  const yiList = Array.isArray(huangli?.yiJi?.yi) ? huangli.yiJi.yi : []
  const jiList = Array.isArray(huangli?.yiJi?.ji) ? huangli.yiJi.ji : []
  const yiMatch = yiList.filter(i => keywords.some(k => i.includes(k)))
  const jiMatch = jiList.filter(i => keywords.some(k => i.includes(k)))
  const hasRef = yiMatch.length > 0 || jiMatch.length > 0

  const handleTaskType = (key: QimenDomainKey) => {
    if (item.qimenEvent?.domain === key) {
      setQimenEvent(item.id, undefined)
    } else {
      setQimenEvent(item.id, { domain: key })
    }
  }

  return (
    <Sheet open={open} onClose={onClose} title="">
      {editing ? (
        <div style={{ marginBottom: 16 }}>
          <textarea
            value={editText}
            onChange={e => setEditText(e.target.value)}
            autoFocus
            rows={3}
            style={{
              width: '100%',
              padding: '10px 12px',
              border: '0.5px solid var(--border-default)',
              borderRadius: 'var(--radius-md)',
              background: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              fontSize: 'var(--text-body)',
              lineHeight: 1.45,
              outline: 'none',
              resize: 'none',
              boxSizing: 'border-box',
            }}
          />
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <Button variant="ghost" onClick={() => setEditing(false)} style={{ flex: 1 }}>
              取消
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                if (editText.trim()) {
                  editTodo(item.id, editText.trim())
                  setEditing(false)
                }
              }}
              style={{ flex: 1 }}
            >
              保存
            </Button>
          </div>
        </div>
      ) : (
      <>
        <div style={{
          fontSize: 'var(--text-headline)',
          fontWeight: 600,
          color: 'var(--text-primary)',
          marginBottom: 16,
          lineHeight: 1.35,
          cursor: 'pointer',
        }}
          onClick={() => { setEditText(item.text); setEditing(true) }}
        >
          {item.text}
          <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', marginLeft: 8, fontWeight: 400 }}>编辑</span>
        </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          marginBottom: 8,
        }}>
          优先级
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {([
            { value: 2, label: '低' },
            { value: 1, label: '中' },
            { value: 0, label: '高' },
          ] as const).map(p => (
            <button
              key={p.value}
              onClick={() => setPriority(item.id, p.value)}
              style={{
                flex: 1,
                padding: '8px',
                borderRadius: 'var(--radius-md)',
                border: `1.5px solid ${item.priority === p.value ? 'var(--accent)' : 'var(--border-default)'}`,
                background: item.priority === p.value ? 'var(--accent-surface)' : 'transparent',
                color: item.priority === p.value ? 'var(--accent)' : 'var(--text-secondary)',
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

      <div style={{ marginBottom: 16 }}>
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          marginBottom: 8,
        }}>
          重复周期
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {([
            { value: undefined as number | undefined, label: '不重复' },
            { value: 1, label: '每天' },
            { value: 7, label: '每周' },
            { value: 30, label: '每月' },
            { value: 365, label: '每年' },
          ]).map(r => (
            <Chip
              key={r.label}
              type="tag"
              selected={item.repeatInterval === r.value}
              label={r.label}
              onClick={() => setRepeatInterval(item.id, r.value)}
            />
          ))}
        </div>
      </div>

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
              selected={item.qimenEvent?.domain === t.key}
              label={t.label}
              onClick={() => handleTaskType(t.key)}
            />
          ))}
        </div>
      </div>

      {domain && hasRef && (
        <div style={{
          marginBottom: 16,
          padding: 12,
          borderRadius: 'var(--radius-md)',
          background: 'var(--bg-secondary)',
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-secondary)',
          animation: 'fadeInUp 0.15s ease',
        }}>
          <div style={{
            fontSize: 'var(--text-caption-2)',
            color: 'var(--text-tertiary)',
            fontWeight: 500,
            marginBottom: 6,
          }}>
            今日参考 · {TASK_TYPES.find(t => t.key === domain)?.label}
          </div>
          {yiMatch.length > 0 && (
            <div style={{ marginBottom: jiMatch.length > 0 ? 4 : 0 }}>
              <span style={{ color: 'var(--tone-smooth)', fontWeight: 500 }}>宜 </span>
              {yiMatch.join(' · ')}
            </div>
          )}
          {jiMatch.length > 0 && (
            <div>
              <span style={{ color: 'var(--tone-caution)', fontWeight: 500 }}>忌 </span>
              {jiMatch.join(' · ')}
            </div>
          )}
          {huangli && (
            <div style={{ marginTop: 4, fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)' }}>
              喜神{huangli.fangwei.xi} · 财神{huangli.fangwei.cai}
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

      <div ref={reminderRef} style={{ marginBottom: 16 }}>
        {item.reminderTime && !showReminderPicker ? (
          <div
            onClick={() => {
              setReminderTime(toDatetimeLocal(item.reminderTime!))
              setShowReminderPicker(true)
            }}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: 10,
              borderRadius: 'var(--radius-md)',
              background: 'var(--accent-surface)',
              fontSize: 'var(--text-footnote)',
              color: 'var(--accent)',
              cursor: 'pointer',
            }}
          >
            <IconBell size={16} />
            <span style={{ flex: 1, fontWeight: 500 }}>
              提醒时间 {new Date(item.reminderTime).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: 'numeric', minute: '2-digit' })}
            </span>
            <span style={{ fontSize: 'var(--text-caption-2)', opacity: 0.6, marginRight: 4 }}>编辑</span>
            <button
              onClick={(e) => { e.stopPropagation(); clearReminderFn(item.id) }}
              aria-label="清除提醒"
              style={{
                background: 'none', border: 'none', color: 'var(--accent)',
                cursor: 'pointer', display: 'flex', padding: 2,
              }}
            >
              <IconX size={14} />
            </button>
          </div>
        ) : !showReminderPicker ? (
          <button
            onClick={() => setShowReminderPicker(true)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              width: '100%',
              padding: '10px 0',
              background: 'none',
              border: 'none',
              color: 'var(--accent)',
              fontWeight: 500,
              fontSize: 'var(--text-footnote)',
              cursor: 'pointer',
            }}
          >
            <IconBell size={16} />
            设置提醒
          </button>
        ) : (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
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
            <Button
              variant="primary"
              disabled={!reminderTime}
              onClick={() => {
                setReminderTimeFn(item.id, new Date(reminderTime).getTime())
                setShowReminderPicker(false)
                setReminderTime('')
              }}
              style={{ flexShrink: 0 }}
            >
              保存
            </Button>
          </div>
        )}
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          marginBottom: 8,
          display: 'flex',
          alignItems: 'center',
          gap: 6,
        }}>
          子任务
          {subtasks.length > 0 && (
            <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-disabled)' }}>
              {subtaskDone}/{subtasks.length}
            </span>
          )}
        </div>
        {subtasks.length > 0 && (
          <div style={{ marginBottom: 8 }}>
            {subtasks.map(st => (
              <div key={st.id} style={{
                display: 'flex', alignItems: 'center', gap: 8,
                padding: '4px 8px', borderRadius: 'var(--radius-sm)',
                fontSize: 'var(--text-footnote)',
                color: st.done ? 'var(--text-tertiary)' : 'var(--text-primary)',
                textDecoration: st.done ? 'line-through' : 'none',
              }}>
                <button
                  onClick={() => toggleTodo(st.id)}
                  aria-label={st.done ? '标记未完成' : '标记完成'}
                  style={{
                    width: 18, height: 18, borderRadius: '50%', flexShrink: 0,
                    border: `2px solid ${st.done ? 'var(--tone-smooth)' : 'var(--border-default)'}`,
                    background: st.done ? 'var(--tone-smooth)' : 'transparent',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    cursor: 'pointer', padding: 0,
                  }}
                >
                  {st.done && <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="4"><path d="M20 6L9 17l-5-5"/></svg>}
                </button>
                <span style={{ flex: 1 }}>{st.text}</span>
                <button
                  onClick={() => deleteTodo(st.id)}
                  aria-label="删除子任务"
                  style={{ background: 'none', border: 'none', color: 'var(--text-disabled)', cursor: 'pointer', padding: 2 }}
                >
                  <IconX size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            value={subtaskText}
            onChange={e => setSubtaskText(e.target.value)}
            placeholder="添加子任务..."
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
            onKeyDown={e => {
              if (e.key === 'Enter' && subtaskText.trim()) {
                addSubtask(item.id, subtaskText.trim())
                setSubtaskText('')
              }
            }}
          />
          <Button
            variant="ghost"
            disabled={!subtaskText.trim()}
            onClick={() => {
              addSubtask(item.id, subtaskText.trim())
              setSubtaskText('')
            }}
            style={{ flexShrink: 0, display: 'flex', alignItems: 'center', gap: 4 }}
          >
            <IconPlus size={14} />
            添加
          </Button>
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{
          fontSize: 'var(--text-caption-1)',
          color: 'var(--text-tertiary)',
          marginBottom: 8,
        }}>
          排序
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button
            onClick={() => { moveTodoToPosition(item.id, 0); onClose() }}
            style={{
              flex: 1, padding: '6px 0', borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)', background: 'transparent',
              color: 'var(--text-secondary)', fontSize: 'var(--text-footnote)',
              cursor: 'pointer', fontWeight: 500,
            }}
          >
            置顶
          </button>
          <button
            onClick={() => { moveTodoToPosition(item.id, Math.max(0, currentIndex - 1)); onClose() }}
            disabled={currentIndex <= 0}
            style={{
              flex: 1, padding: '6px 0', borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)', background: 'transparent',
              color: currentIndex <= 0 ? 'var(--text-disabled)' : 'var(--text-secondary)',
              fontSize: 'var(--text-footnote)', cursor: 'pointer', fontWeight: 500,
            }}
          >
            上移
          </button>
          <button
            onClick={() => { moveTodoToPosition(item.id, currentIndex + 1); onClose() }}
            disabled={currentIndex >= activeCount - 1}
            style={{
              flex: 1, padding: '6px 0', borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)', background: 'transparent',
              color: currentIndex >= activeCount - 1 ? 'var(--text-disabled)' : 'var(--text-secondary)',
              fontSize: 'var(--text-footnote)', cursor: 'pointer', fontWeight: 500,
            }}
          >
            下移
          </button>
          <button
            onClick={() => { moveTodoToPosition(item.id, activeCount - 1); onClose() }}
            style={{
              flex: 1, padding: '6px 0', borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-default)', background: 'transparent',
              color: 'var(--text-secondary)', fontSize: 'var(--text-footnote)',
              cursor: 'pointer', fontWeight: 500,
            }}
          >
            置底
          </button>
        </div>
      </div>

      {categories.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{
            fontSize: 'var(--text-caption-1)',
            color: 'var(--text-tertiary)',
            marginBottom: 8,
          }}>
            移动到分类
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Chip
              type="tag"
              selected={!item.category || item.category === 'default'}
              label="未分类"
              onClick={() => moveTodoToCategory(item.id, '')}
            />
            {categories.map(cat => (
              <Chip
                key={cat.id}
                type="tag"
                selected={item.category === cat.id}
                label={cat.label}
                onClick={() => moveTodoToCategory(item.id, cat.id)}
              />
            ))}
          </div>
        </div>
      )}

      <div style={{ display: 'flex', gap: 12 }}>
        <Button
          variant="destructive"
          onClick={() => { deleteTodo(item.id); onClose() }}
          style={{ flex: 1, gap: 6 }}
        >
          <IconTrash size={16} />
          删除
        </Button>
      </div>
      </>
      )}
    </Sheet>
  )
}
