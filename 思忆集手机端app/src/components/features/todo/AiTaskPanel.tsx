import { useState, useCallback } from 'react'
import { haptic } from '../../../lib/haptic'
import { usingCloudAi } from '../../../lib/cloudAiClient'
import {
  breakdownGoal, planDay, triageTodos,
  type BreakdownResult, type PlanResult, type TriageItem, type PlanInput,
} from '../../../lib/aiTaskEngine'

export type AiTaskMode = 'breakdown' | 'plan' | 'triage'

interface Props {
  open: boolean
  mode: AiTaskMode
  onClose: () => void
  /** 现有待办（plan / triage 模式用） */
  todos: PlanInput[]
  /** 采纳子任务建议（breakdown 模式） */
  onAcceptSubtasks: (items: string[]) => void
}

/**
 * AI 待办面板 / AI task panel
 *
 * 三种模式共用一个壳：
 *   breakdown — 输入一个模糊目标，AI 拆成可执行子任务，用户勾选后批量建待办
 *   plan      — 给现有待办排今日时间表
 *   triage    — 对积压待办分诊（保留/推迟/删除/合并）
 *
 * 关键取舍：AI 的结果一律「先展示、后应用」，不直接写库。
 * 自动化再聪明也不能替用户做删除决定。
 */
export default function AiTaskPanel({ open, mode, onClose, todos, onAcceptSubtasks }: Props) {
  const [goal, setGoal] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [bd, setBd] = useState<BreakdownResult | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())

  const [plan, setPlan] = useState<PlanResult | null>(null)
  const [triage, setTriage] = useState<TriageItem[] | null>(null)

  const reset = useCallback(() => {
    setError(null); setBd(null); setPlan(null); setTriage(null); setPicked(new Set())
  }, [])

  const run = useCallback(async () => {
    if (!usingCloudAi()) {
      setError('未配置云 AI。请到 设置 → AI 配置 填入 API Key。')
      return
    }
    haptic.light()
    setBusy(true)
    setError(null)
    setBd(null); setPlan(null); setTriage(null)

    try {
      if (mode === 'breakdown') {
        if (!goal.trim()) { setError('请输入一个目标'); setBusy(false); return }
        const r = await breakdownGoal(goal.trim())
        setBd(r)
        // 默认全选（用户通常想要全部子任务）
        setPicked(new Set(r.subtasks.map((_, i) => i)))
      } else if (mode === 'plan') {
        const r = await planDay(todos)
        setPlan(r)
      } else {
        const r = await triageTodos(todos)
        setTriage(r)
        if (r.length === 0) setError('AI 未返回分诊建议')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'AI 调用失败')
    } finally {
      setBusy(false)
    }
  }, [mode, goal, todos])

  if (!open) return null

  const title = mode === 'breakdown' ? 'AI 拆解目标' : mode === 'plan' ? 'AI 安排今日' : 'AI 分诊积压'

  return (
    <div
      style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'flex-end' }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%', maxHeight: '85vh',
          background: 'var(--bg-primary)',
          borderRadius: 'var(--radius-xl) var(--radius-xl) 0 0',
          paddingBottom: 'var(--safe-bottom)',
          display: 'flex', flexDirection: 'column',
        }}
      >
        <div style={{
          display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
          padding: 'var(--space-4)', borderBottom: '0.5px solid var(--border-subtle)',
        }}>
          <span style={{ fontSize: 18 }}>🤖</span>
          <div style={{ flex: 1, fontSize: 'var(--text-headline)', fontWeight: 600 }}>{title}</div>
          <button
            onClick={() => { reset(); onClose() }}
            aria-label="关闭"
            style={{
              width: 32, height: 32, border: 'none', background: 'var(--bg-secondary)',
              borderRadius: '50%', color: 'var(--text-secondary)', fontSize: 16, cursor: 'pointer',
            }}
          >✕</button>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-4)' }}>
          {mode === 'breakdown' && (
            <textarea
              value={goal}
              onChange={e => setGoal(e.target.value)}
              placeholder="描述你想完成的事，越具体越好。例如：下周三前做完考研报名"
              rows={3}
              style={{
                width: '100%', padding: 'var(--space-3)',
                border: '0.5px solid var(--border-default)',
                borderRadius: 'var(--radius-md)',
                background: 'var(--bg-secondary)',
                color: 'var(--text-primary)',
                fontSize: 'var(--text-footnote)',
                lineHeight: 1.6,
                resize: 'none', outline: 'none',
                fontFamily: 'inherit',
              }}
            />
          )}

          {mode !== 'breakdown' && !plan && !triage && (
            <div style={{
              padding: 'var(--space-3)', background: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-md)', fontSize: 'var(--text-footnote)',
              color: 'var(--text-secondary)', lineHeight: 1.6,
            }}>
              将分析当前 {todos.length} 条未完成待办。
              {mode === 'plan' ? ' AI 按优先级与截止时间排进今天的时间段。' : ' AI 判断哪些该做、哪些该丢、哪些重复。'}
            </div>
          )}

          <button
            onClick={() => { void run() }}
            disabled={busy}
            style={{
              width: '100%', height: 46, marginTop: 'var(--space-3)',
              border: 'none', borderRadius: 'var(--radius-md)',
              background: busy ? 'var(--bg-tertiary)' : 'var(--accent)',
              color: busy ? 'var(--text-tertiary)' : '#fff',
              fontSize: 'var(--text-callout)', fontWeight: 600,
              cursor: busy ? 'wait' : 'pointer',
            }}
          >{busy ? '分析中…' : (bd || plan || triage ? '重新生成' : '开始')}</button>

          {error && (
            <div style={{
              marginTop: 'var(--space-3)', padding: 'var(--space-3)',
              background: 'rgba(255,59,48,0.1)', borderRadius: 'var(--radius-md)',
              color: 'var(--danger)', fontSize: 'var(--text-footnote)', lineHeight: 1.5,
            }}>{error}</div>
          )}

          {/* ── 拆解结果 ── */}
          {bd && (
            <div style={{ marginTop: 'var(--space-4)' }}>
              <div style={{
                fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)',
                marginBottom: 6,
              }}>AI 的理解：{bd.understanding}</div>
              {bd.suggestedSlot && (
                <div style={{
                  fontSize: 'var(--text-caption-1)', color: 'var(--accent)', marginBottom: 8,
                }}>建议时段：{bd.suggestedSlot}</div>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {bd.subtasks.map((s, i) => {
                  const on = picked.has(i)
                  return (
                    <button
                      key={i}
                      onClick={() => {
                        haptic.light()
                        const n = new Set(picked)
                        if (n.has(i)) n.delete(i); else n.add(i)
                        setPicked(n)
                      }}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 10,
                        padding: 'var(--space-3)', textAlign: 'left',
                        background: on ? 'var(--accent-surface)' : 'var(--bg-secondary)',
                        border: on ? '1px solid var(--accent)' : '1px solid transparent',
                        borderRadius: 'var(--radius-md)', cursor: 'pointer',
                      }}
                    >
                      <span style={{
                        width: 20, height: 20, borderRadius: 5, flexShrink: 0,
                        border: on ? 'none' : '1.5px solid var(--border-default)',
                        background: on ? 'var(--accent)' : 'transparent',
                        color: '#fff', fontSize: 12,
                        display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>{on ? '✓' : ''}</span>
                      <span style={{ flex: 1 }}>
                        <span style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-primary)' }}>
                          {s.order}. {s.text}
                        </span>
                        {s.minutes != null && (
                          <span style={{
                            fontSize: 'var(--text-caption-2)',
                            color: 'var(--text-tertiary)', marginLeft: 6,
                          }}>约 {s.minutes} 分钟</span>
                        )}
                      </span>
                    </button>
                  )
                })}
              </div>

              <button
                onClick={() => {
                  haptic.light()
                  const chosen = bd.subtasks.filter((_, i) => picked.has(i)).map(s => s.text)
                  if (chosen.length === 0) { setError('至少选一项'); return }
                  onAcceptSubtasks(chosen)
                  reset(); onClose()
                }}
                style={{
                  width: '100%', height: 44, marginTop: 'var(--space-3)',
                  border: 'none', borderRadius: 'var(--radius-md)',
                  background: 'var(--accent)', color: '#fff',
                  fontSize: 'var(--text-callout)', fontWeight: 600, cursor: 'pointer',
                }}
              >创建 {picked.size} 条待办</button>
            </div>
          )}

          {/* ── 排程结果 ── */}
          {plan && (
            <div style={{ marginTop: 'var(--space-4)' }}>
              {plan.note && (
                <div style={{
                  fontSize: 'var(--text-caption-1)', color: 'var(--text-secondary)',
                  marginBottom: 10, lineHeight: 1.6,
                }}>{plan.note}</div>
              )}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {plan.slots.map((s, i) => (
                  <div key={i} style={{
                    display: 'flex', gap: 10, padding: 'var(--space-3)',
                    background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)',
                  }}>
                    <div style={{
                      fontSize: 'var(--text-caption-1)', fontWeight: 600,
                      color: 'var(--accent)', flexShrink: 0, width: 88,
                      fontVariantNumeric: 'tabular-nums',
                    }}>{s.start}–{s.end}</div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 'var(--text-footnote)', color: 'var(--text-primary)' }}>{s.text}</div>
                      <div style={{
                        fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
                        marginTop: 2, lineHeight: 1.4,
                      }}>{s.reason}</div>
                    </div>
                  </div>
                ))}
              </div>
              {plan.deferred.length > 0 && (
                <div style={{ marginTop: 'var(--space-3)' }}>
                  <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--tone-caution)', marginBottom: 4 }}>
                    今天排不下（{plan.deferred.length}）
                  </div>
                  <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', lineHeight: 1.6 }}>
                    {plan.deferred.join('、')}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ── 分诊结果 ── */}
          {triage && (
            <div style={{ marginTop: 'var(--space-4)', display: 'flex', flexDirection: 'column', gap: 6 }}>
              {triage.map((t, i) => {
                const text = todos.find(x => x.id === t.todoId)?.text ?? t.todoId
                const color = t.verdict === 'do' ? 'var(--tone-smooth)'
                  : t.verdict === 'drop' ? 'var(--danger)'
                  : t.verdict === 'defer' ? 'var(--tone-caution)' : 'var(--accent)'
                const label = t.verdict === 'do' ? '保留' : t.verdict === 'drop' ? '建议删除'
                  : t.verdict === 'defer' ? '推迟' : '合并'
                return (
                  <div key={i} style={{
                    padding: 'var(--space-3)', background: 'var(--bg-secondary)',
                    borderRadius: 'var(--radius-md)',
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{
                        fontSize: 'var(--text-caption-2)', fontWeight: 600,
                        color, padding: '2px 7px', background: 'var(--bg-glass)',
                        borderRadius: 'var(--radius-full)', flexShrink: 0,
                      }}>{label}</span>
                      <span style={{
                        fontSize: 'var(--text-footnote)', color: 'var(--text-primary)',
                        flex: 1, minWidth: 0, overflow: 'hidden',
                        textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>{text}</span>
                    </div>
                    <div style={{
                      fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
                      marginTop: 4, lineHeight: 1.5,
                    }}>{t.reason}</div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
