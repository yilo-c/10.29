/**
 * A5（前后端对齐规划）：晨报卡专属渲染。
 *
 * 数据来源：proactive.triggered（trigger=morning_brief）事件经
 * backendEventsStore 落点（A1 接入层）；后端在每日 07:00 日报生成后
 * 双通道投递（backend/weekly_report_scheduler.py：收件箱 kind=daily_report
 * + proactive.triggered）。本卡片负责实时气泡展示。
 *
 * i18n 说明：按用户确认不做英文界面，本组件直接使用中文文案。
 */
import React from 'react'
import { useBackendEventsStore } from '@desk-notes/shared'

const MorningBriefCard: React.FC = () => {
  const brief = useBackendEventsStore((s) => s.morningBrief)
  const clearMorningBrief = useBackendEventsStore((s) => s.clearMorningBrief)

  if (!brief) return null

  return (
    <div
      className="absolute bottom-4 right-4 z-50 w-[300px] max-w-[calc(100%-32px)] p-3 rounded-xl border shadow-lg"
      style={{ borderColor: 'rgba(96,165,250,0.3)', background: 'var(--panel-bg)', backdropFilter: 'blur(20px)' }}
    >
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-1.5">
          <span style={{ fontSize: 12 }}>🌅</span>
          <span className="text-xs font-medium text-text-primary">
            {brief.title || '今日晨报'}
          </span>
          {brief.date && (
            <span className="text-2xs text-text-muted">{brief.date}</span>
          )}
        </div>
        <button
          onClick={clearMorningBrief}
          className="shrink-0 transition-all hover:opacity-70"
          style={{ fontSize: 11, color: 'var(--text-muted)' }}
          title="关闭晨报"
        >
          ✕
        </button>
      </div>
      <div
        className="text-xs leading-relaxed text-text-secondary overflow-y-auto max-h-40"
        style={{ whiteSpace: 'pre-line' }}
      >
        {brief.content}
      </div>
    </div>
  )
}

export default MorningBriefCard
