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
export async function approveCapability(capabilityId: string | number): Promise<boolean> {
  try { await callRpc('capability.approve', { capability_id: capabilityId }); return true } catch { return false }
}
export async function dismissCapability(capabilityId: string | number): Promise<boolean> {
  try { await callRpc('capability.dismiss', { capability_id: capabilityId }); return true } catch { return false }
}

/* ── Usage ── */
export interface UsageStats {
  total?: number
  by_provider?: Record<string, number>
  by_day?: Array<{ day: string; count: number }>
  [k: string]: unknown
}
export async function recordUsage(event: string, data?: Record<string, unknown>): Promise<boolean> {
  try { await callRpc('usage.record', { event, data: data ?? {} }); return true } catch { return false }
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
