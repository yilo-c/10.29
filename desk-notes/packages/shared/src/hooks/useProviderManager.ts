import { useState, useEffect, useCallback, useRef } from 'react'
import { getWsClient } from '../utils/ai'
import { onBackendEvent } from '../store/backendEventBus'
import { useTranslation } from '../i18n'
import type { ProviderConfig, ProviderHealth, KiroStatus, RpcResult } from '../types/provider'

interface UseProviderManagerReturn {
  providers: ProviderConfig[]
  kiroStatus: KiroStatus | null
  loading: boolean
  error: string | null
  refresh: () => Promise<void>
  addProvider: (config: Record<string, unknown>) => Promise<RpcResult>
  removeProvider: (name: string) => Promise<RpcResult>
  updateProvider: (name: string, updates: Record<string, unknown>) => Promise<RpcResult>
  checkHealth: (name: string) => Promise<ProviderHealth>
  refreshAllHealth: () => Promise<void>
}

export function useProviderManager(): UseProviderManagerReturn {
  const { t } = useTranslation()
  const [providers, setProviders] = useState<ProviderConfig[]>([])
  const [kiroStatus, setKiroStatus] = useState<KiroStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const mountedRef = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const client = getWsClient()
      if (!client || client.status !== 'connected') return

      // provider.list 与 kiro.status 解耦：前者失败不应阻断 provider 列表刷新，
      // 后者（可选状态信息）失败也不应拖累列表更新。
      const providerList = await client.call<ProviderConfig[]>('provider.list')
      const kiro = await client.call<KiroStatus>('kiro.status').catch(() => null)
      if (!mountedRef.current) return
      setProviders(providerList)
      if (kiro) setKiroStatus(kiro)
      setError(null)
    } catch (e) {
      if (!mountedRef.current) return
      setError(e instanceof Error ? e.message : '获取数据失败')
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [])

  const checkHealth = useCallback(async (name: string): Promise<ProviderHealth> => {
    const client = getWsClient()
    if (!client) throw new Error('Not connected')
    const result = await client.call<ProviderHealth>('provider.health', { name, force: true })
    return result
  }, [])

  const refreshAllHealth = useCallback(async () => {
    const updated = await Promise.all(
      providers.filter(p => !p.is_local).map(async (p) => {
        try {
          const health = await checkHealth(p.name)
          return { ...p, health: { ...health, last_checked: Date.now() } }
        } catch {
          return p
        }
      })
    )
    // Local providers stay as-is
    const localOnes = providers.filter(p => p.is_local)
    setProviders([...localOnes, ...updated])
  }, [providers, checkHealth])

  const addProvider = useCallback(async (config: Record<string, unknown>): Promise<RpcResult> => {
    const client = getWsClient()
    if (!client) throw new Error('Not connected')
    const result = await client.call<RpcResult>('provider.add', config)
    // R4 审批门控：请求入队未执行——提示用户去右下角审批卡批准，而非静默关表单
    const pending = result as Partial<RpcResult> & { pending?: boolean; requires_approval?: boolean }
    if (pending.pending && pending.requires_approval) {
      throw new Error(t('ux.apiKeyPendingApproval'))
    }
    if (result && result.success === false) {
      // 后端校验失败（缺 key / 重名 / 字段非法）→ 抛出让 UI 显示原因，
      // 而不是静默关表单让用户误以为成功。
      throw new Error(result.message || '添加 Provider 失败')
    }
    await refresh()
    return result
  }, [refresh, t])

  const removeProvider = useCallback(async (name: string): Promise<RpcResult> => {
    const client = getWsClient()
    if (!client) throw new Error('Not connected')
    const result = await client.call<RpcResult>('provider.remove', { name })
    if (result && result.success === false) {
      throw new Error(result.message || '删除 Provider 失败')
    }
    await refresh()
    return result
  }, [refresh])

  const updateProvider = useCallback(async (name: string, updates: Record<string, unknown>): Promise<RpcResult> => {
    const client = getWsClient()
    if (!client) throw new Error('Not connected')
    const result = await client.call<RpcResult>('provider.update', { name, updates })
    if (result && result.success === false) {
      throw new Error(result.message || '更新 Provider 失败')
    }
    await refresh()
    return result
  }, [refresh])

  useEffect(() => {
    mountedRef.current = true

    // 刚打开页面时 WS 可能尚未就绪（status!=='connected'），refresh 会静默早退，
    // 导致列表停留为空（显示 0）。订阅连接事件：一旦 connected 立即补刷新。
    const client = getWsClient()
    const offStatus = client
      ? client.onStatusChange((s) => {
          if (s === 'connected') refresh()
        })
      : undefined
    // R4 审批（provider.add/remove/update）批准执行后立即刷新列表，
    // 不等 30s 轮询——用户批准后马上能看到新 provider。
    const offApproved = onBackendEvent('sandbox.approved', () => refresh())

    refresh()
    intervalRef.current = setInterval(refresh, 30000)
    return () => {
      mountedRef.current = false
      if (intervalRef.current) clearInterval(intervalRef.current)
      if (offStatus) offStatus()
      offApproved()
    }
  }, [refresh])

  return {
    providers,
    kiroStatus,
    loading,
    error,
    refresh,
    addProvider,
    removeProvider,
    updateProvider,
    checkHealth,
    refreshAllHealth,
  }
}
