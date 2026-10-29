import React from 'react'

export type StatusTone = 'ok' | 'running' | 'warn' | 'error' | 'idle'

interface StatusPillProps {
  /** 语义色调：ok(绿) / running(蓝) / warn(橙) / error(红) / idle(灰) */
  tone?: StatusTone
  label: React.ReactNode
  /** 状态点是否呼吸闪烁（用于进行中的状态） */
  pulse?: boolean
  title?: string
  className?: string
}

/** Quest 式状态胶囊：软底色 + 状态点，替代裸小圆点（P1） */
const StatusPill: React.FC<StatusPillProps> = ({ tone = 'idle', label, pulse = false, title, className = '' }) => (
  <span className={`status-pill${className ? ` ${className}` : ''}`} data-tone={tone} title={title}>
    <span className={`status-pill-dot${pulse ? ' animate-pulse' : ''}`} />
    {label}
  </span>
)

export default StatusPill
