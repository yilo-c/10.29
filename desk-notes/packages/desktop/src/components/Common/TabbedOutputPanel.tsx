import React, { useState } from 'react'

export interface OutputTab {
  key: string
  label: string
  render: () => React.ReactNode
}

interface TabbedOutputPanelProps {
  tabs: OutputTab[]
  defaultTab?: string
  className?: string
}

/** Quest 式输出区：Tab 化的右侧栏容器，承载执行进度/产物/详情视图（P2） */
const TabbedOutputPanel: React.FC<TabbedOutputPanelProps> = ({ tabs, defaultTab, className = '' }) => {
  const [activeKey, setActiveKey] = useState(defaultTab || tabs[0]?.key)
  const active = tabs.find(tb => tb.key === activeKey) || tabs[0]
  if (tabs.length === 0) return null
  return (
    <aside className={`output-panel${className ? ` ${className}` : ''}`}>
      <div className="output-panel-tabs">
        {tabs.map(tb => (
          <button
            key={tb.key}
            onClick={() => setActiveKey(tb.key)}
            className={`output-panel-tab${tb.key === active?.key ? ' output-panel-tab-active' : ''}`}
          >
            {tb.label}
          </button>
        ))}
      </div>
      <div className="output-panel-body">{active?.render()}</div>
    </aside>
  )
}

export default TabbedOutputPanel
