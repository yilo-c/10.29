import React from 'react'

interface PageHeaderProps {
  title: string
  subtitle?: string
  /** 右侧操作区（按钮等） */
  actions?: React.ReactNode
  className?: string
}

/** 模块级页面头：大标题 + 副标题 + 操作区，统一各模块的头部层级（P1） */
const PageHeader: React.FC<PageHeaderProps> = ({ title, subtitle, actions, className = '' }) => (
  <div className={`page-header${className ? ` ${className}` : ''}`}>
    <div className="page-header-title">
      <h1>{title}</h1>
      {subtitle && <p>{subtitle}</p>}
    </div>
    {actions && <div className="page-header-actions">{actions}</div>}
  </div>
)

export default PageHeader
