/**
 * A1（前后端对齐规划）：后端事件统一接入层。
 *
 * 背景：后端阶段 1–4 新增的事件（task.resumable / sync.conflict /
 * sandbox.timeout* / cron.job_alert / exec.step / morning_brief）此前
 * 前端无人消费。本模块提供：
 * - `useBackendEventsStore`：独立 zustand store（不持久化，瞬态状态）
 * - `installBackendEvents(client)`：在 WsClient 上统一注册六类事件处理器，
 *   由 `getWsClient()` 懒初始化时自动安装（见 utils/ai）。
 *
 * 阶段 B3 会把本过渡层升级为 App 级统一订阅分发（Qoder 模式），
 * 届时 store 形状保持不变，只迁移注册位置。
 */
import { create } from 'zustand'
import type { ServerPushEvent, WsClient } from '../ws-client'
import { onBackendEvent, dispatchBackendEvent } from './backendEventBus'

// ── 事件 payload 类型 ─────────────────────────────────────────────

export interface ResumableTaskInfo {
  task_id: string
  state: string
}

export interface SyncConflictInfo {
  resource: string
  id: string
  title: string
  local_time: number
  remote_time: number
  receivedAt: number
}

export interface SandboxTimeoutInfo {
  pending_id: string
  action_id: string
  kind: 'timeout' | 'warning'
  policy?: string
  status?: string
  remaining_seconds?: number
  receivedAt: number
}

export interface JobAlertInfo {
  job_id: string
  job_name: string
  error: string
  consecutive_failures: number
  receivedAt: number
}

export interface MorningBriefInfo {
  title: string
  content: string
  date: string
  inbox_id?: string
  receivedAt: number
}

export interface ExecStepInfo {
  task_id: string
  step_type: string
  tool?: string
  summary?: string
  receivedAt: number
}

interface BackendEventsState {
  /** 启动扫描发现的可续跑任务（task.resumable） */
  resumableTasks: ResumableTaskInfo[]
  /** 用户是否已关闭续跑横幅（会话内有效） */
  resumableDismissed: boolean
  /** 未裁决的同步冲突（sync.conflict，按 resource+id 去重） */
  syncConflicts: SyncConflictInfo[]
  /** 审批超时/临期提醒（sandbox.timeout / timeout_warning） */
  sandboxTimeouts: SandboxTimeoutInfo[]
  /** cron 连续失败告警（cron.job_alert） */
  jobAlerts: JobAlertInfo[]
  /** 最新晨报（proactive.triggered trigger=morning_brief） */
  morningBrief: MorningBriefInfo | null
  /** exec.step 最近步骤环形缓冲（观测面板过渡数据源） */
  recentExecSteps: ExecStepInfo[]

  dismissResumable: () => void
  removeSyncConflict: (resource: string, id: string) => void
  dismissSandboxTimeout: (pending_id: string) => void
  dismissJobAlert: (job_id: string) => void
  clearMorningBrief: () => void
  clearExecSteps: () => void
}

const MAX_EXEC_STEPS = 300
const MAX_TIMEOUT_ENTRIES = 50
const MAX_JOB_ALERTS = 50

function asRecord(payload: unknown): Record<string, unknown> {
  return (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>
}

function asString(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback
}

function asNumber(v: unknown, fallback = 0): number {
  return typeof v === 'number' && Number.isFinite(v) ? v : fallback
}

export const useBackendEventsStore = create<BackendEventsState>()((set) => ({
  resumableTasks: [],
  resumableDismissed: false,
  syncConflicts: [],
  sandboxTimeouts: [],
  jobAlerts: [],
  morningBrief: null,
  recentExecSteps: [],

  dismissResumable: () => set({ resumableDismissed: true }),

  removeSyncConflict: (resource, id) =>
    set((s) => ({
      syncConflicts: s.syncConflicts.filter((c) => !(c.resource === resource && c.id === id)),
    })),

  dismissSandboxTimeout: (pending_id) =>
    set((s) => ({
      sandboxTimeouts: s.sandboxTimeouts.filter((t) => t.pending_id !== pending_id),
    })),

  dismissJobAlert: (job_id) =>
    set((s) => ({
      jobAlerts: s.jobAlerts.filter((a) => a.job_id !== job_id),
    })),

  clearMorningBrief: () => set({ morningBrief: null }),

  clearExecSteps: () => set({ recentExecSteps: [] }),
}))

// ── 事件处理器（供 installBackendEvents 使用，单独导出便于测试） ──

export function handleTaskResumable(event: ServerPushEvent): void {
  const p = asRecord(event.payload)
  const rawTasks = Array.isArray(p.tasks) ? p.tasks : []
  const tasks: ResumableTaskInfo[] = rawTasks
    .filter((t): t is Record<string, unknown> => !!t && typeof t === 'object')
    .map((t) => ({
      task_id: asString(t.task_id),
      state: asString(t.state),
    }))
    .filter((t) => t.task_id)
  useBackendEventsStore.setState({ resumableTasks: tasks, resumableDismissed: false })
}

export function handleSyncConflict(event: ServerPushEvent): void {
  const p = asRecord(event.payload)
  const id = asString(p.id)
  const resource = asString(p.resource)
  if (!id) return
  const info: SyncConflictInfo = {
    resource,
    id,
    title: asString(p.title),
    local_time: asNumber(p.local_time),
    remote_time: asNumber(p.remote_time),
    receivedAt: Date.now(),
  }
  useBackendEventsStore.setState((s) => ({
    syncConflicts: [info, ...s.syncConflicts.filter((c) => !(c.resource === resource && c.id === id))],
  }))
}

export function handleSandboxTimeout(event: ServerPushEvent, kind: 'timeout' | 'warning'): void {
  const p = asRecord(event.payload)
  const pending_id = asString(p.pending_id)
  if (!pending_id) return
  const info: SandboxTimeoutInfo = {
    pending_id,
    action_id: asString(p.action_id),
    kind,
    policy: typeof p.policy === 'string' ? p.policy : undefined,
    status: typeof p.status === 'string' ? p.status : undefined,
    remaining_seconds: typeof p.remaining_seconds === 'number' ? p.remaining_seconds : undefined,
    receivedAt: Date.now(),
  }
  useBackendEventsStore.setState((s) => ({
    sandboxTimeouts: [info, ...s.sandboxTimeouts.filter((t) => t.pending_id !== pending_id)].slice(0, MAX_TIMEOUT_ENTRIES),
  }))
}

export function handleJobAlert(event: ServerPushEvent): void {
  const p = asRecord(event.payload)
  const job_id = asString(p.job_id)
  if (!job_id) return
  const info: JobAlertInfo = {
    job_id,
    job_name: asString(p.job_name),
    error: asString(p.error),
    consecutive_failures: asNumber(p.consecutive_failures),
    receivedAt: Date.now(),
  }
  useBackendEventsStore.setState((s) => ({
    jobAlerts: [info, ...s.jobAlerts.filter((a) => a.job_id !== job_id)].slice(0, MAX_JOB_ALERTS),
  }))
}

export function handleProactiveTriggered(event: ServerPushEvent): void {
  const p = asRecord(event.payload)
  if (p.trigger !== 'morning_brief') return
  const meta = asRecord(p.metadata)
  useBackendEventsStore.setState({
    morningBrief: {
      title: asString(p.title, '今日晨报'),
      content: asString(p.content),
      date: asString(meta.date),
      inbox_id: typeof meta.inbox_id === 'number' ? String(meta.inbox_id) : asString(meta.inbox_id) || undefined,
      receivedAt: Date.now(),
    },
  })
}

export function handleExecStep(event: ServerPushEvent): void {
  const p = asRecord(event.payload)
  const task_id = asString(p.task_id)
  if (!task_id) return
  const info: ExecStepInfo = {
    task_id,
    step_type: asString(p.step_type, 'step'),
    tool: typeof p.tool === 'string' ? p.tool : undefined,
    summary: typeof p.summary === 'string' ? p.summary : undefined,
    receivedAt: Date.now(),
  }
  useBackendEventsStore.setState((s) => ({
    recentExecSteps: [info, ...s.recentExecSteps].slice(0, MAX_EXEC_STEPS),
  }))
}

// ── 统一安装入口 ─────────────────────────────────────────────────

/**
 * 在 WsClient 上安装后端事件管道（B3 转正）：所有事件经通配符汇入
 * backendEventBus 分发注册表；store 落点处理器作为首批订阅者注册。
 * 组件级事件反应请用 `onBackendEvent()`，禁止再直连 client.on('event')。
 * 返回卸载函数。由 getWsClient() 在客户端创建时自动调用。
 */
export function installBackendEvents(client: WsClient): () => void {
  // store 落点：六类新事件（A1 职责保留）
  const busOffs = [
    onBackendEvent('task.resumable', (_p, e) => handleTaskResumable(e)),
    onBackendEvent('sync.conflict', (_p, e) => handleSyncConflict(e)),
    onBackendEvent('sandbox.timeout', (_p, e) => handleSandboxTimeout(e, 'timeout')),
    onBackendEvent('sandbox.timeout_warning', (_p, e) => handleSandboxTimeout(e, 'warning')),
    onBackendEvent('cron.job_alert', (_p, e) => handleJobAlert(e)),
    onBackendEvent('proactive.triggered', (_p, e) => handleProactiveTriggered(e)),
    onBackendEvent('exec.step', (_p, e) => handleExecStep(e)),
  ]
  // 通配符订阅：全部事件入分发注册表（组件订阅在 bus 层按类型过滤）
  const offClient = client.on('*', (event) => dispatchBackendEvent(event))
  return () => {
    offClient()
    for (const off of busOffs) off()
  }
}
