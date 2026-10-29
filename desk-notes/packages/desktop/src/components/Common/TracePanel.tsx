import React, { useState, useEffect, useCallback, useMemo } from 'react'
import Icon from '../Common/Icon'
import Skeleton from './Skeleton'

import { getWsClient } from '../../utils/ai'
import { formatDateTime, formatMessageTimestamp } from '@desk-notes/shared'
import { describeToolError } from '../../utils/toolErrorHints'
import { swallowError } from '../../utils/swallowError'

/**
 * D2（前后端对齐规划）：运行观测面板。
 *
 * 数据源：trace.list / trace.get / trace.stats（assistant/trace_recorder.py
 * → backend/handlers/_trace.py）。exec.step 实时事件经 A1 层入 store，
 * 本面板以 RPC 拉取为主（历史可查），运行中任务通过刷新跟进。
 *
 * i18n 说明：按用户确认不做英文界面，本组件直接使用中文文案。
 */

interface TraceRun {
  task_id: string
  task_type: string
  title: string
  status: string
  source: string
  started_at: number
  finished_at?: number | null
  cost_tokens: number
  error: string
  steps?: TraceStep[]
}

interface TraceStep {
  step_no: number
  step_type: string
  tool: string
  summary: string
  ts: number
}

interface TraceStats {
  days: number
  total_runs: number
  by_type: Record<string, number>
  by_status: Record<string, number>
  success_rate: number | null
}

const STATUS_META: Record<string, { label: string; color: string }> = {
  running: { label: '运行中', color: 'var(--brand)' },
  completed: { label: '已完成', color: 'var(--success)' },
  failed: { label: '失败', color: 'var(--danger)' },
  interrupted: { label: '已中断', color: 'var(--warning)' },
}

function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: status || '未知', color: 'var(--text-muted)' }
}

function formatDuration(started?: number, finished?: number | null): string {
  if (!started) return ''
  const end = finished ?? Date.now()
  const sec = Math.max(0, Math.round(end - started))
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m${sec % 60}s`
  return `${Math.floor(sec / 3600)}h${Math.floor((sec % 3600) / 60)}m`
}

const TracePanel: React.FC = () => {
  const [runs, setRuns] = useState<TraceRun[]>([])
  const [stats, setStats] = useState<TraceStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<TraceRun | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

  const fetchRuns = useCallback(async () => {
    try {
      const client = getWsClient()
      if (client.status !== 'connected') return
      const res = await client.call<{ success: boolean; runs?: TraceRun[]; count?: number }>('trace.list', { limit: 30 })
      if (res?.success && Array.isArray(res.runs)) {
        setRuns(res.runs)
      }
    } catch (e) {
      swallowError('TracePanel:list', e)
    }
  }, [])

  const fetchStats = useCallback(async () => {
    try {
      const client = getWsClient()
      if (client.status !== 'connected') return
      const res = await client.call<TraceStats & { success?: boolean }>('trace.stats', { days: 7 })
      if (res) setStats(res)
    } catch (e) {
      swallowError('TracePanel:stats', e)
    }
  }, [])

  const refresh = useCallback(async () => {
    setLoading(true)
    await Promise.all([fetchRuns(), fetchStats()])
    setLoading(false)
  }, [fetchRuns, fetchStats])

  useEffect(() => {
    refresh()
  }, [refresh])

  // 选中 run 时拉取详情（含步骤）
  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    ;(async () => {
      try {
        const client = getWsClient()
        const res = await client.call<{ success: boolean; run?: TraceRun; error?: string }>('trace.get', { task_id: selectedId })
        if (!cancelled) {
          setDetail(res?.success && res.run ? res.run : null)
          setDetailLoading(false)
        }
      } catch (e) {
        if (!cancelled) {
          setDetail(null)
          setDetailLoading(false)
        }
        swallowError('TracePanel:get', e)
      }
    })()
    return () => { cancelled = true }
  }, [selectedId])

  const successRateText = useMemo(() => {
    if (!stats || stats.success_rate === null) return '—'
    return `${Math.round(stats.success_rate * 100)}%`
  }, [stats])

  const selected = detail ?? runs.find(r => r.task_id === selectedId) ?? null
  const errorHint = selected?.error ? describeToolError(selected.error) : null

  if (loading) {
    return (
      <div className="space-y-2 p-3">
        {[0, 1, 2].map(i => (
          <div key={i} className="space-y-1">
            <Skeleton className="h-3 w-3/4 rounded" />
            <Skeleton className="h-2 w-1/2 rounded" />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* 统计条 */}
      <div className="flex items-center gap-3 px-3 py-2 border-b flex-shrink-0 text-xs" style={{ borderColor: 'var(--separator)', color: 'var(--text-muted)' }}>
        <span>近 7 天运行 <b style={{ color: 'var(--text-secondary)' }}>{stats?.total_runs ?? 0}</b> 次</span>
        <span>成功率 <b style={{ color: stats && stats.success_rate !== null && stats.success_rate < 0.8 ? 'var(--warning)' : 'var(--success)' }}>{successRateText}</b></span>
        <button
          onClick={refresh}
          className="ml-auto flex items-center gap-1 px-1.5 py-0.5 rounded transition-all hover:bg-hover-bg"
          style={{ color: 'var(--text-secondary)' }}
          title="刷新"
        >
          <Icon icon="fa-solid fa-rotate text-2xs" />
          刷新
        </button>
      </div>

      <div className="flex flex-1 min-h-0">
        {/* 左：run 列表 */}
        <div className="w-[220px] flex-shrink-0 border-r overflow-y-auto" style={{ borderColor: 'var(--separator)' }}>
          {runs.length === 0 && (
            <div className="p-3 text-xs" style={{ color: 'var(--text-dim)' }}>
              暂无运行记录。发起一次 AI 工具任务后这里会记录全过程。
            </div>
          )}
          {runs.map(run => {
            const meta = statusMeta(run.status)
            const active = run.task_id === selectedId
            return (
              <button
                key={run.task_id}
                onClick={() => setSelectedId(run.task_id)}
                className="w-full text-left px-2.5 py-2 border-b transition-colors"
                style={{
                  borderColor: 'var(--separator)',
                  background: active ? 'var(--overlay-faint)' : 'transparent',
                }}
              >
                <div className="flex items-center gap-1.5">
                  <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: meta.color }} />
                  <span className="text-xs truncate flex-1" style={{ color: 'var(--text-primary)' }}>
                    {run.title || run.task_type || run.task_id.slice(0, 12)}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 mt-0.5 text-2xs" style={{ color: 'var(--text-dim)' }}>
                  <span>{meta.label}</span>
                  <span>·</span>
                  <span>{formatMessageTimestamp(run.started_at * 1000)}</span>
                </div>
              </button>
            )
          })}
        </div>

        {/* 右：详情时间线 */}
        <div className="flex-1 min-w-0 overflow-y-auto p-3">
          {!selected && (
            <div className="text-xs" style={{ color: 'var(--text-dim)' }}>
              选择左侧运行记录查看步骤时间线。
            </div>
          )}

          {selected && detailLoading && (
            <div className="space-y-2">
              <Skeleton className="h-3 w-2/3 rounded" />
              <Skeleton className="h-3 w-4/5 rounded" />
            </div>
          )}

          {selected && !detailLoading && (
            <div className="space-y-3">
              {/* 头部信息 */}
              <div>
                <div className="text-xs font-medium truncate" style={{ color: 'var(--text-primary)' }}>
                  {selected.title || selected.task_id}
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-2xs" style={{ color: 'var(--text-muted)' }}>
                  <span>类型：{selected.task_type || '—'}</span>
                  <span>状态：{statusMeta(selected.status).label}</span>
                  <span>开始：{formatDateTime(selected.started_at * 1000)}</span>
                  <span>耗时：{formatDuration(selected.started_at, selected.finished_at)}</span>
                  {selected.cost_tokens > 0 && <span>tokens：{selected.cost_tokens}</span>}
                </div>
              </div>

              {/* 失败原因（友好化） */}
              {selected.error && (
                <div className="p-2 rounded-lg border" style={{ borderColor: 'rgba(239,68,68,0.3)', background: 'rgba(239,68,68,0.06)' }}>
                  <div className="flex items-center gap-1.5 text-xs font-medium" style={{ color: 'var(--danger)' }}>
                    <Icon icon="fa-solid fa-triangle-exclamation text-2xs" />
                    {errorHint ? errorHint.title : '执行失败'}
                  </div>
                  {errorHint && (
                    <div className="mt-1 text-2xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                      {errorHint.advice}
                    </div>
                  )}
                  <div className="mt-1 text-2xs font-mono truncate" style={{ color: 'var(--text-dim)' }} title={selected.error}>
                    {selected.error}
                  </div>
                </div>
              )}

              {/* 步骤时间线 */}
              {selected.steps && selected.steps.length > 0 && (
                <div className="space-y-0">
                  {selected.steps.map(step => {
                    const stepHint = step.step_type === 'error' ? describeToolError(step.summary) : null
                    return (
                      <div key={step.step_no} className="flex gap-2">
                        <div className="flex flex-col items-center flex-shrink-0">
                          <span
                            className="w-2 h-2 rounded-full mt-1"
                            style={{ background: step.step_type === 'error' ? 'var(--danger)' : step.tool ? '#8b5cf6' : 'var(--text-dim)' }}
                          />
                          <span className="w-px flex-1" style={{ background: 'var(--separator)' }} />
                        </div>
                        <div className="pb-2.5 min-w-0 flex-1">
                          <div className="flex items-center gap-1.5 text-2xs" style={{ color: 'var(--text-dim)' }}>
                            <span>{step.step_type}</span>
                            {step.tool && (
                              <span className="px-1 rounded" style={{ background: 'var(--overlay-faint)', color: '#a78bfa' }}>
                                {step.tool}
                              </span>
                            )}
                            <span>{formatMessageTimestamp(step.ts * 1000)}</span>
                          </div>
                          {step.summary && (
                            <div className="text-xs mt-0.5 break-words" style={{ color: 'var(--text-secondary)' }}>
                              {stepHint ? `${stepHint.title} — ${stepHint.advice}` : step.summary}
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}

              {(!selected.steps || selected.steps.length === 0) && !selected.error && (
                <div className="text-2xs" style={{ color: 'var(--text-dim)' }}>
                  该运行未记录步骤。
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default TracePanel
