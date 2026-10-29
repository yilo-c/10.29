import { useState, useEffect } from 'react'
import { haptic } from '../../lib/haptic'
import { describeToolCall } from '../../lib/toolDescriber'
export { describeToolCall }

/**
 * 高风险操作审批 / Danger action approval
 *
 * 为什么必须有这一层：
 *   AI 的 tap/swipe 能力边界等于「用户的手指」。
 *   点错一次可能误付款、误删消息、误发送——不可逆。
 *   没有审批，用户只能事后发现，那时已经晚了。
 *
 * 设计原则：
 *   1. 默认「拒绝」—— 不点同意就不执行，防止误触
 *   2. 展示「AI 具体要做什么」，不是抽象的"确认操作"
 *   3. 给出 3 秒思考时间（倒计时结束前「允许」按钮不可点）
 *      避免用户条件反射式点确认
 *   4. 拒绝后要把原因回灌给 AI，让它知道被拒了、别重试
 */

export interface ApprovalRequest {
  id: string
  /** 工具显示名，如「点击屏幕」 */
  toolLabel: string
  /** 工具 id，如 phone.tap */
  toolId: string
  /** AI 打算做什么（人类可读） */
  summary: string
  /** 具体参数，逐条展示 */
  details: Array<{ label: string; value: string }>
  /** AI 给出的理由（可选） */
  reason?: string
}

interface Props {
  request: ApprovalRequest | null
  onDecide: (approved: boolean, reason?: string) => void
}


export default function ApprovalDialog({ request, onDecide }: Props) {
  // 倒计时：防止条件反射式点「允许」
  const [countdown, setCountdown] = useState(3)

  useEffect(() => {
    if (!request) return
    setCountdown(3)
    const timer = setInterval(() => {
      setCountdown(c => {
        if (c <= 1) { clearInterval(timer); return 0 }
        return c - 1
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [request])

  if (!request) return null

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100,
      background: 'rgba(0,0,0,0.55)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: 'var(--space-4)',
    }}>
      <div style={{
        width: '100%', maxWidth: 400,
        background: 'var(--bg-primary)',
        borderRadius: 'var(--radius-xl)',
        overflow: 'hidden',
        boxShadow: '0 20px 60px rgba(0,0,0,0.4)',
      }}>
        {/* 头部：明确警告色 */}
        <div style={{
          padding: 'var(--space-4)',
          background: 'rgba(255,149,0,0.12)',
          borderBottom: '0.5px solid rgba(255,149,0,0.3)',
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontSize: 22 }}>⚠️</span>
          <div style={{ flex: 1 }}>
            <div style={{
              fontSize: 'var(--text-headline)', fontWeight: 600,
              color: 'var(--text-primary)',
            }}>AI 想操作你的手机</div>
            <div style={{
              fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)',
              marginTop: 2,
            }}>{request.toolLabel} · 高风险</div>
          </div>
        </div>

        <div style={{ padding: 'var(--space-4)' }}>
          {/* 具体做什么 */}
          <div style={{
            fontSize: 'var(--text-footnote)', color: 'var(--text-primary)',
            lineHeight: 1.6, marginBottom: 'var(--space-3)',
          }}>{request.summary}</div>

          {request.reason && (
            <div style={{
              padding: 'var(--space-3)', marginBottom: 'var(--space-3)',
              background: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)',
              fontSize: 'var(--text-caption-1)', color: 'var(--text-secondary)',
              lineHeight: 1.5,
            }}>
              <span style={{ color: 'var(--text-tertiary)' }}>AI 的理由：</span>{request.reason}
            </div>
          )}

          {/* 参数详情 */}
          {request.details.length > 0 && (
            <div style={{
              background: 'var(--bg-secondary)',
              borderRadius: 'var(--radius-md)',
              padding: 'var(--space-3)',
              marginBottom: 'var(--space-3)',
            }}>
              {request.details.map((d, i) => (
                <div key={i} style={{
                  display: 'flex', justifyContent: 'space-between',
                  fontSize: 'var(--text-caption-1)',
                  padding: '3px 0',
                }}>
                  <span style={{ color: 'var(--text-tertiary)' }}>{d.label}</span>
                  <span style={{
                    color: 'var(--text-primary)',
                    fontVariantNumeric: 'tabular-nums',
                  }}>{d.value}</span>
                </div>
              ))}
            </div>
          )}

          <div style={{
            fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
            lineHeight: 1.5, marginBottom: 'var(--space-4)',
          }}>
            点击可能触发付款、删除、发送等不可逆操作。请确认你了解这次点击会做什么。
          </div>

          {/* 按钮：拒绝为主，允许在倒计时后可点 */}
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <button
              onClick={() => { haptic.light(); onDecide(false, '用户拒绝') }}
              style={{
                flex: 1, height: 46,
                border: '0.5px solid var(--border-default)',
                borderRadius: 'var(--radius-md)',
                background: 'var(--bg-secondary)',
                color: 'var(--text-primary)',
                fontSize: 'var(--text-callout)', fontWeight: 600,
                cursor: 'pointer',
              }}
            >拒绝</button>
            <button
              onClick={() => {
                if (countdown > 0) return
                haptic.light()
                onDecide(true)
              }}
              disabled={countdown > 0}
              style={{
                flex: 1, height: 46, border: 'none',
                borderRadius: 'var(--radius-md)',
                background: countdown > 0 ? 'var(--bg-tertiary)' : 'var(--danger)',
                color: countdown > 0 ? 'var(--text-tertiary)' : '#fff',
                fontSize: 'var(--text-callout)', fontWeight: 600,
                cursor: countdown > 0 ? 'not-allowed' : 'pointer',
              }}
            >{countdown > 0 ? `允许 (${countdown})` : '允许'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}
