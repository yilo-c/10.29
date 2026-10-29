import { useState, useMemo } from 'react'
import { getDayCellLunarInfo, getHuangli } from '@desk-notes/shared'
import { IconChevronDown } from '../../ui'
import type { TodoItem as TodoItemType } from '@desk-notes/shared'

const WEEKDAYS = ['日', '一', '二', '三', '四', '五', '六']

interface CalendarDayDetailProps {
  date: Date
  todos: TodoItemType[]
}

export default function CalendarDayDetail({ date, todos }: CalendarDayDetailProps) {
  const [almanacExpanded, setAlmanacExpanded] = useState(false)

  const lunarInfo = useMemo(() => getDayCellLunarInfo(date), [date])
  const huangli = useMemo(() => getHuangli(date), [date])

  const subtitle = [lunarInfo?.lunarDay || '', lunarInfo?.jieQi || '', ...(lunarInfo?.festivals || [])]
    .filter(Boolean)
    .join(' · ')

  const dayTodos = useMemo(() =>
    todos.filter(t => {
      if (!t.dueDate) return false
      const td = new Date(t.dueDate)
      return td.getFullYear() === date.getFullYear()
        && td.getMonth() === date.getMonth()
        && td.getDate() === date.getDate()
    }),
    [todos, date],
  )

  return (
    <div style={{
      flexShrink: 0,
      borderBottom: '0.5px solid var(--border-subtle)',
    }}>
      <div style={{ padding: '12px 20px 8px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <span style={{
            fontSize: 'var(--text-title-2)',
            fontWeight: 700,
            color: 'var(--text-primary)',
            letterSpacing: -0.3,
          }}>
            {date.getMonth() + 1}月{date.getDate()}日
          </span>
          <span style={{
            fontSize: 'var(--text-subhead)',
            color: 'var(--text-secondary)',
          }}>
            周{WEEKDAYS[date.getDay()]}
          </span>
        </div>
        {subtitle && (
          <div style={{
            fontSize: 'var(--text-footnote)',
            color: 'var(--text-tertiary)',
            marginTop: 2,
          }}>
            {subtitle}
          </div>
        )}
      </div>

      {huangli && (
        <div style={{
          margin: '0 20px 10px',
          borderRadius: 'var(--radius-md)',
          background: 'var(--bg-secondary)',
          overflow: 'hidden',
        }}>
          <button
            onClick={() => { setAlmanacExpanded(v => !v); navigator.vibrate?.(6) }}
            style={{
              display: 'flex',
              alignItems: 'center',
              width: '100%',
              padding: '6px 12px',
              border: 'none',
              background: 'none',
              cursor: 'pointer',
              gap: 8,
              color: 'var(--text-secondary)',
              fontSize: 'var(--text-caption-1)',
            }}
          >
            <span style={{ color: 'var(--tone-smooth)', fontWeight: 500, flexShrink: 0 }}>宜</span>
            <span style={{
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              textAlign: 'left',
            }}>
              {huangli.yiJi.yi.slice(0, 4).join(' · ')}
            </span>
            <span style={{
              flexShrink: 0,
              color: 'var(--text-tertiary)',
              display: 'flex',
              alignItems: 'center',
              transition: 'transform 0.2s ease',
              transform: almanacExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
            }}>
              <IconChevronDown size={14} />
            </span>
          </button>

          {almanacExpanded && (
            <div style={{
              padding: '0 12px 10px',
              fontSize: 'var(--text-caption-1)',
              color: 'var(--text-secondary)',
              display: 'flex',
              flexDirection: 'column',
              gap: 6,
              animation: 'fadeInUp 0.15s ease',
            }}>
              <div style={{ display: 'flex', gap: 8 }}>
                <span style={{ color: 'var(--tone-smooth)', fontWeight: 500, width: 16, flexShrink: 0 }}>宜</span>
                <span style={{ lineHeight: 1.5 }}>{huangli.yiJi.yi.join(' · ')}</span>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <span style={{ color: 'var(--tone-caution)', fontWeight: 500, width: 16, flexShrink: 0 }}>忌</span>
                <span style={{ lineHeight: 1.5 }}>{huangli.yiJi.ji.join(' · ')}</span>
              </div>
              <div style={{
                display: 'flex',
                gap: 12,
                fontSize: 'var(--text-caption-2)',
                color: 'var(--text-tertiary)',
                marginTop: 2,
              }}>
                <span>冲{huangli.chongSha.chong}</span>
                <span>煞{huangli.chongSha.sha}</span>
                <span>喜神{huangli.fangwei.xi}</span>
                <span>财神{huangli.fangwei.cai}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {dayTodos.length > 0 && (
        <div style={{
          padding: '0 20px 10px',
          display: 'flex',
          flexDirection: 'column',
          gap: 4,
        }}>
          {dayTodos.slice(0, 3).map(todo => (
            <div key={todo.id} style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 'var(--text-footnote)',
              color: 'var(--text-secondary)',
            }}>
              <span style={{
                width: 8,
                height: 8,
                borderRadius: '50%',
                background: todo.done ? 'var(--tone-neutral)' : 'var(--accent)',
                flexShrink: 0,
                opacity: todo.done ? 0.4 : 1,
              }} />
              <span style={{
                textDecoration: todo.done ? 'line-through' : 'none',
                opacity: todo.done ? 0.5 : 1,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}>
                {todo.text}
              </span>
            </div>
          ))}
          {dayTodos.length > 3 && (
            <div style={{
              fontSize: 'var(--text-caption-2)',
              color: 'var(--text-tertiary)',
              paddingLeft: 16,
            }}>
              还有 {dayTodos.length - 3} 项待办
            </div>
          )}
        </div>
      )}
    </div>
  )
}
