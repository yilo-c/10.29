import React from 'react'
import Icon from './Icon'


interface EmptyStateProps {
  /** FontAwesome 图标类名 */
  icon: string
  title: string
  subtitle?: string
  action?: React.ReactNode
  /** 紧凑模式：卡片内小空间使用 */
  compact?: boolean
  className?: string
}

/** 统一空态：光晕图标 + 主副文案 + 可选操作（P3b） */
const EmptyState: React.FC<EmptyStateProps> = ({ icon, title, subtitle, action, compact = false, className = '' }) => (
  <div className={`empty-state${compact ? ' empty-state-compact' : ''}${className ? ` ${className}` : ''}`}>
    <div className="empty-state-icon">
      <Icon icon={icon} />
    </div>
    <div className="empty-state-title">{title}</div>
    {subtitle && <div className="empty-state-subtitle">{subtitle}</div>}
    {action && <div className="empty-state-action">{action}</div>}
  </div>
)

export default EmptyState
