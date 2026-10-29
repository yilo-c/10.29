import Icon from './Icon'
/**
 * A3（前后端对齐规划）：断点续跑提示横幅。
 *
 * 数据来源：task.resumable 事件经 backendEventsStore 落点（A1 接入层）；
 * 后端启动时扫描 checkpoint（backend/headless_main.py），发现未完成任务
 * 即推送该事件。续跑调用 assistant.resume_task（backend/handlers/_trace.py
 * → assistant/checkpoint.py）。
 *
 * i18n 说明：按用户确认不做英文界面，本组件直接使用中文文案。
 */
import React, { useState } from 'react'
import { useBackendEventsStore } from '@desk-notes/shared'
import { getWsClient } from '../../utils/ai'

interface ResumeResult {
  success: boolean
  error?: string
}

const ResumableTasksBanner: React.FC = () => {
  const tasks = useBackendEventsStore((s) => s.resumableTasks)
  const dismissed = useBackendEventsStore((s) => s.resumableDismissed)
  const dismissResumable = useBackendEventsStore((s) => s.dismissResumable)
  const [expanded, setExpanded] = useState(false)
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null)
  const [resumedIds, setResumedIds] = useState<Set<string>>(new Set())
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const pending = tasks.filter((t) => !resumedIds.has(t.task_id))
  if (dismissed || (pending.length === 0 && !message)) return null

  const resume = async (taskId: string) => {
    setBusyTaskId(taskId)
    setMessage(null)
    try {
      const client = getWsClient()
      const result = await client.call<ResumeResult>('assistant.resume_task', { task_id: taskId })
      if (result?.success) {
        setResumedIds((prev) => new Set(prev).add(taskId))
        setMessage({ kind: 'ok', text: '任务已从断点恢复执行' })
      } else {
        setMessage({ kind: 'err', text: result?.error ?? '续跑失败' })
      }
      setTimeout(() => setMessage(null), 4000)
    } catch (err) {
      setMessage({ kind: 'err', text: (err as Error).message })
      setTimeout(() => setMessage(null), 4000)
    } finally {
      setBusyTaskId(null)
    }
  }

  return (
    <div
      className="absolute top-3 left-1/2 -translate-x-1/2 z-50 max-w-[440px] w-[calc(100%-32px)] p-3 rounded-xl border shadow-lg"
      style={{ borderColor: 'rgba(96,165,250,0.35)', background: 'var(--panel-bg)', backdropFilter: 'blur(20px)' }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <Icon icon="fa-solid fa-clock-rotate-left text-fluent-blue text-xs" />
          <span className="text-xs text-text-primary truncate">
            上次退出时有 {pending.length} 个任务未完成，可断点续跑
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="px-2 py-1 rounded-lg text-xs font-medium transition-all bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30"
          >
            {expanded ? '收起' : '查看'}
          </button>
          <button
            onClick={dismissResumable}
            className="px-2 py-1 rounded-lg text-xs transition-all bg-white/[0.04] text-text-muted hover:bg-white/[0.08]"
          >
            忽略
          </button>
        </div>
      </div>

      {expanded && pending.length > 0 && (
        <div className="mt-2 space-y-1.5 max-h-48 overflow-y-auto">
          {pending.map((t) => (
            <div
              key={t.task_id}
              className="flex items-center justify-between gap-2 p-2 rounded-lg"
              style={{ background: 'var(--input-bg)', border: '1px solid var(--panel-border)' }}
            >
              <div className="min-w-0 flex-1">
                <div className="text-xs text-text-primary truncate">{t.task_id}</div>
                <div className="text-2xs text-text-muted">状态：{t.state || '未知'}</div>
              </div>
              <button
                onClick={() => resume(t.task_id)}
                disabled={busyTaskId === t.task_id}
                className="px-2 py-1 rounded-lg text-xs font-medium transition-all bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-40 shrink-0"
              >
                {busyTaskId === t.task_id ? '恢复中…' : '续跑'}
              </button>
            </div>
          ))}
        </div>
      )}

      {message && (
        <div className={`mt-2 text-xs ${message.kind === 'ok' ? 'text-fluent-green' : 'text-red-400'}`}>
          {message.text}
        </div>
      )}
    </div>
  )
}

export default ResumableTasksBanner
