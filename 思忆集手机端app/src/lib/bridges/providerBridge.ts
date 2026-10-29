/**
 * Provider / API Key / Model 桥 — 桌面 `provider.*` / `api_keys.*` / `model.list`。
 *
 * 手机端可以"读 + 改"Provider（设置面板里维护一份镜像），但 API Key 管理仍保持
 * 桌面专属（避免泄露），这里只提供"列表/测试"两个只读操作；create/update/delete
 * 仍然提供，但 UI 应限制为有 PIN 解锁后才能调用。
 */
import { callRpc } from '../rpcClient'

/* ── Provider ── */
export interface ProviderInfo {
  id?: string | number
  name?: string
  base_url?: string
  api_key?: string  // masked
  model?: string
  enabled?: boolean
  [k: string]: unknown
}

export async function listProviders(): Promise<ProviderInfo[]> {
  try {
    const res = await callRpc<unknown>('provider.list', {})
    return Array.isArray(res) ? (res as ProviderInfo[]) : []
  } catch { return [] }
}

export async function addProvider(args: {
  name: string
  base_url: string
  api_key: string
  model: string
}): Promise<ProviderInfo | null> {
  try {
    return await callRpc<ProviderInfo>('provider.add', args as Record<string, unknown>)
  } catch { return null }
}

export async function updateProvider(providerId: string | number, patch: Partial<ProviderInfo>): Promise<boolean> {
  try {
    await callRpc('provider.update', { provider_id: providerId, ...patch })
    return true
  } catch { return false }
}

export async function removeProvider(providerId: string | number): Promise<boolean> {
  try {
    await callRpc('provider.remove', { provider_id: providerId })
    return true
  } catch { return false }
}

export async function providerHealth(providerId: string | number): Promise<{ ok?: boolean; latency?: number; error?: string } | null> {
  try {
    return await callRpc<{ ok?: boolean; latency?: number; error?: string }>('provider.health', { provider_id: providerId })
  } catch { return null }
}

/* ── API Keys ── */
export interface ApiKeyInfo {
  id?: string | number
  name?: string
  key?: string  // masked
  created_at?: string | number
  last_used_at?: string | number
  [k: string]: unknown
}

export async function listApiKeys(): Promise<ApiKeyInfo[]> {
  try {
    const res = await callRpc<unknown>('api_keys.list', {})
    return Array.isArray(res) ? (res as ApiKeyInfo[]) : []
  } catch { return [] }
}
export async function createApiKey(name: string, key: string): Promise<ApiKeyInfo | null> {
  try { return await callRpc<ApiKeyInfo>('api_keys.create', { name, key }) } catch { return null }
}
export async function updateApiKey(keyId: string | number, patch: Partial<ApiKeyInfo>): Promise<boolean> {
  try { await callRpc('api_keys.update', { key_id: keyId, ...patch }); return true } catch { return false }
}
export async function deleteApiKey(keyId: string | number): Promise<boolean> {
  try { await callRpc('api_keys.delete', { key_id: keyId }); return true } catch { return false }
}
export async function testApiKey(keyId: string | number): Promise<{ ok?: boolean; error?: string } | null> {
  try { return await callRpc<{ ok?: boolean; error?: string }>('api_keys.test', { key_id: keyId }) } catch { return null }
}

/* ── Models ── */
export interface ModelInfoBrief {
  id?: string
  name?: string
  provider?: string
  context_length?: number
  [k: string]: unknown
}

export async function listModels(): Promise<ModelInfoBrief[]> {
  try {
    const res = await callRpc<unknown>('model.list', {})
    return Array.isArray(res) ? (res as ModelInfoBrief[]) : []
  } catch { return [] }
}
