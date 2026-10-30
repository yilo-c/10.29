/**
 * 系统/Agent/Capability/Usage/Sandbox 桥 — 后端管理面板能力。
 *
 * 手机端用于：
 *   - 设置面板里显示后端版本/状态/Tailscale 状态/健康分；
 *   - 概览页面显示活跃任务、能力提案、Agent 摘要；
 *   - 触发能力批准/忽略；
 *   - 沙箱审批已经有 listPendingApprovals 等，这里只补 `sandbox.preview`。
 */
import { callRpc } from '../rpcClient'

/* ── System ── */
export interface SystemStatus {
  running?: boolean
  data_dir?: string
  startup_time?: string | number
  [k: string]: unknown
}
export interface SystemInfo {
  version?: string
  python?: string
  electron?: boolean
  [k: string]: unknown
}
export interface SystemHealth {
  status?: string
  subsystems?: Record<string, { status?: string; detail?: string }>
  generated_at?: string | number
  [k: string]: unknown
}
export interface TailscaleStatus {
  connected?: boolean
  ip?: string
  hostname?: string
  [k: string]: unknown
}

export async function getSystemStatus(): Promise<SystemStatus | null> {
  try { return await callRpc<SystemStatus>('system.status', {}) } catch { return null }
}
export async function getSystemInfo(): Promise<SystemInfo | null> {
  try { return await callRpc<SystemInfo>('system.info', {}) } catch { return null }
}
export async function getSystemHealth(forceRefresh = false): Promise<SystemHealth | null> {
  try { return await callRpc<SystemHealth>('system.health', { force_refresh: forceRefresh }) } catch { return null }
}
export async function getActiveTasks(): Promise<unknown[]> {
  try {
    const res = await callRpc<unknown>('system.active_tasks', {})
    return Array.isArray(res) ? res : []
  } catch { return [] }
}
export async function getTailscaleStatus(): Promise<TailscaleStatus | null> {
  try { return await callRpc<TailscaleStatus>('system.tailscale_status', {}) } catch { return null }
}

/* ── Agent ── */
export interface AgentStateSummary {
  mood?: string
  busy?: boolean
  current_task?: string
  [k: string]: unknown
}
export async function getAgentStateSummary(): Promise<AgentStateSummary | null> {
  try { return await callRpc<AgentStateSummary>('agent.state.summary', {}) } catch { return null }
}

/* ── Capability ── */
export interface CapabilityProposal {
  id?: string | number
  name?: string
  description?: string
  status?: string
  [k: string]: unknown
}
export async function listCapabilities(): Promise<CapabilityProposal[]> {
  try {
    const res = await callRpc<unknown>('capability.list', {})
    return Array.isArray(res) ? (res as CapabilityProposal[]) : []
  } catch { return [] }
}
/*
 * 这两个函数此前有两个问题，都会让界面「看起来成功、其实没生效」：
 *
 * 1. 参数名错：传的是 `capability_id`，后端签名是 `cap_id`，
 *    且签名末尾有 `**kwargs` —— 写错的参数名不会报错，直接落进 kwargs 被吞。
 *    后端因 `cap_id` 为空立即返回 `{"error": "cap_id required"}`。
 *
 * 2. 假成功：后端约定「失败返回 {error} 而不抛异常」，
 *    原代码只 try/catch 就 `return true`，于是任何失败都被当成成功。
 *    必须检查信封里的 error/success 字段。
 */
function okEnvelope(res: unknown): boolean {
  if (res && typeof res === 'object') {
    const o = res as { error?: unknown; success?: unknown }
    if (o.error) return false
    if (o.success === false) return false
  }
  return true
}

export async function approveCapability(capabilityId: string | number): Promise<boolean> {
  try {
    const res = await callRpc<unknown>('capability.approve', { cap_id: String(capabilityId) })
    return okEnvelope(res)
  } catch { return false }
}
export async function dismissCapability(capabilityId: string | number): Promise<boolean> {
  try {
    const res = await callRpc<unknown>('capability.dismiss', { cap_id: String(capabilityId) })
    return okEnvelope(res)
  } catch { return false }
}

/* ── Usage ── */
export interface UsageStats {
  total?: number
  by_provider?: Record<string, number>
  by_day?: Array<{ day: string; count: number }>
  [k: string]: unknown
}
/**
 * 记录一次用量。
 *
 * 后端签名是 `usage_record(action, domain)`，此前传 `{event, data}` ——
 * 两个字段都不匹配，落进 `**kwargs` 被吞，后端因 `action` 为空
 * 直接返回 `{"error": "action required"}`；而原代码只 try/catch 就返回 true，
 * 于是**用量统计从来没记录成功过，界面上却显示成功**。
 */
export async function recordUsage(event: string, data?: Record<string, unknown>): Promise<boolean> {
  try {
    const res = await callRpc<unknown>('usage.record', {
      action: event,
      // 后端 domain 是字符串字段；把调用方传来的结构化数据里最像分类的那项映射过去
      domain: typeof data?.domain === 'string' ? data.domain : '',
    })
    return okEnvelope(res)
  } catch { return false }
}
export async function getUsageStats(): Promise<UsageStats | null> {
  try { return await callRpc<UsageStats>('usage.stats', {}) } catch { return null }
}

/* ── Sandbox preview（list/approve/reject 在 rpcClient.ts 已有便捷函数） ── */
export async function previewSandboxOperation(operation: Record<string, unknown>): Promise<unknown> {
  try { return await callRpc('sandbox.preview', { operation }) } catch { return null }
}

/* ── Ping / 健康检查 ── */
export async function pingBackend(): Promise<{ pong?: string; ok?: boolean } | null> {
  try {
    return await callRpc<{ pong?: string }>('siyi.ping', {})
  } catch {
    return null
  }
}

/* ── Kiro 状态（桌面端 AI 编排器） ── */
export async function getKiroStatus(): Promise<Record<string, unknown> | null> {
  try {
    return await callRpc<Record<string, unknown>>('kiro.status', {})
  } catch {
    return null
  }
}
