/**
 * B1（前后端对齐规划）：backgroundJobs 通用层。
 *
 * 背景：聊天流与资讯抓取已各有全局状态（chatSessionSlice / infoFetchStore），
 * 但翻译、URL 学习、OCR、自动化任务等长时操作仍是组件本地状态，切页即丢。
 * 本层提供统一的 job 登记/追踪/恢复能力：
 * - `runTrackedJob`：包装任意异步长操作，状态入 store，切页不丢
 * - `rehydrateBackgroundJobs`：重挂载时向 `system.active_tasks` 查询在途任务恢复显示
 *   （backend/handlers/_system_settings.py，返回 autonomy 未完成任务信息）
 *
 * 与 backendEventsStore 的关系：事件层负责"后端推送"，本层负责"前端发起的
 * 在途操作"。阶段 B3 转正时两层合并为统一 App 级状态分发。
 */
import { create } from 'zustand'

export type BackgroundJobType =
  | 'translate'
  | 'url_learn'
  | 'ocr'
  | 'info_fetch'
  | 'automation'
  | 'generic'

export type BackgroundJobStatus = 'running' | 'done' | 'error'

export interface BackgroundJob {
  id: string
  type: BackgroundJobType
  label: string
  status: BackgroundJobStatus
  startedAt: number
  finishedAt?: number
  result?: unknown
  error?: string
}

interface BackgroundJobsState {
  jobs: Record<string, BackgroundJob>
  startJob: (id: string, type: BackgroundJobType, label: string) => void
  finishJob: (id: string, result?: unknown) => void
  failJob: (id: string, error: string) => void
  clearJob: (id: string) => void
  clearFinished: () => void
}

/** 终态 job 保留上限（超出按 finishedAt 淘汰最旧） */
const MAX_FINISHED_JOBS = 50

function pruneFinished(jobs: Record<string, BackgroundJob>): Record<string, BackgroundJob> {
  const finished = Object.values(jobs).filter((j) => j.status !== 'running')
  if (finished.length <= MAX_FINISHED_JOBS) return jobs
  const overflow = finished
    .sort((a, b) => (a.finishedAt ?? 0) - (b.finishedAt ?? 0))
    .slice(0, finished.length - MAX_FINISHED_JOBS)
  const next = { ...jobs }
  for (const j of overflow) delete next[j.id]
  return next
}

export const useBackgroundJobsStore = create<BackgroundJobsState>()((set) => ({
  jobs: {},

  startJob: (id, type, label) =>
    set((s) => ({
      jobs: {
        ...s.jobs,
        [id]: { id, type, label, status: 'running', startedAt: Date.now() },
      },
    })),

  finishJob: (id, result) =>
    set((s) => {
      const job = s.jobs[id]
      if (!job) return s
      return {
        jobs: pruneFinished({
          ...s.jobs,
          [id]: { ...job, status: 'done', finishedAt: Date.now(), result },
        }),
      }
    }),

  failJob: (id, error) =>
    set((s) => {
      const job = s.jobs[id]
      if (!job) return s
      return {
        jobs: pruneFinished({
          ...s.jobs,
          [id]: { ...job, status: 'error', finishedAt: Date.now(), error },
        }),
      }
    }),

  clearJob: (id) =>
    set((s) => {
      if (!(id in s.jobs)) return s
      const next = { ...s.jobs }
      delete next[id]
      return { jobs: next }
    }),

  clearFinished: () =>
    set((s) => ({
      jobs: Object.fromEntries(Object.entries(s.jobs).filter(([, j]) => j.status === 'running')),
    })),
}))

// ── 便捷包装 ────────────────────────────────────────────────────

let jobSeq = 0

export function nextJobId(type: BackgroundJobType): string {
  jobSeq += 1
  return `${type}-${Date.now()}-${jobSeq}`
}

export interface TrackedJobOptions<T> {
  type: BackgroundJobType
  label: string
  id?: string
  run: () => Promise<T>
}

/**
 * 包装异步长操作为受追踪 job：状态入 store，组件卸载/切页不丢。
 * 成功/失败都会落终态；异常继续向上抛，调用方自行处理 UI 反馈。
 */
export async function runTrackedJob<T>(opts: TrackedJobOptions<T>): Promise<T> {
  const id = opts.id ?? nextJobId(opts.type)
  const store = useBackgroundJobsStore.getState()
  store.startJob(id, opts.type, opts.label)
  try {
    const result = await opts.run()
    useBackgroundJobsStore.getState().finishJob(id, result)
    return result
  } catch (err) {
    useBackgroundJobsStore.getState().failJob(id, err instanceof Error ? err.message : String(err))
    throw err
  }
}

// ── rehydration ─────────────────────────────────────────────────

export interface ActiveTaskInfo {
  task_id: string
  goal?: string
  state?: string
  progress?: string
}

/**
 * 重挂载恢复：查询后端在途任务（system.active_tasks）并入 store。
 * client 参数便于测试注入；默认懒取 getWsClient（避免循环依赖由调用方传入）。
 * 返回恢复的任务数。
 */
export async function rehydrateBackgroundJobs(
  call: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>,
): Promise<number> {
  try {
    const tasks = await call<ActiveTaskInfo[]>('system.active_tasks')
    if (!Array.isArray(tasks) || tasks.length === 0) return 0
    const store = useBackgroundJobsStore.getState()
    let restored = 0
    for (const t of tasks) {
      if (!t?.task_id) continue
      const id = `backend-${t.task_id}`
      if (store.jobs[id]) continue
      store.startJob(id, 'automation', t.goal || t.task_id)
      restored += 1
    }
    return restored
  } catch {
    return 0
  }
}

// ── 选择器辅助 ──────────────────────────────────────────────────

export function selectRunningJobs(jobs: Record<string, BackgroundJob>): BackgroundJob[] {
  return Object.values(jobs).filter((j) => j.status === 'running')
}
