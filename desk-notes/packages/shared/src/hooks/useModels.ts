/* Hook to fetch available AI models from the backend via model.list RPC. */
import { useState, useEffect, useCallback, useRef } from 'react'
import { getWsClient } from '../utils/ai'
import type { ModelInfo } from '../types'

interface BackendProviderEntry {
  provider: string
  api_base: string
  models: string[]
  is_local?: boolean
  is_fallback?: boolean
  enabled?: boolean
}

export interface UseModelsResult {
  models: ModelInfo[]
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useModels(): UseModelsResult {
  const [models, setModels] = useState<ModelInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const mountedRef = useRef(true)

  const refresh = useCallback(() => {
    setLoading(true)
    setError(null)
    const client = getWsClient()
    client.call<BackendProviderEntry[]>('model.list')
      .then((result) => {
        if (!mountedRef.current) return
        const list: ModelInfo[] = []
        // Backend returns an array, not an object with .providers
        const entries = Array.isArray(result) ? result : (result as any)?.providers ?? []
        for (const entry of entries) {
          for (const model of (entry as BackendProviderEntry).models ?? []) {
            list.push({ provider: entry.provider, model, api_base: entry.api_base })
          }
        }
        setModels(list)
        setLoading(false)
      })
      .catch((err: Error) => {
        if (!mountedRef.current) return
        setError(err.message ?? 'Failed to load models')
        setLoading(false)
      })
  }, [])

  useEffect(() => {
    mountedRef.current = true
    refresh()
    return () => { mountedRef.current = false }
  }, [refresh])

  // 断线重连后自动重试：连接被心跳强杀/网络抖动时，在途的 model.list
  // 会被 reject（'WebSocket closed'），重连成功后重新拉取而不是停在错误态。
  const stateRef = useRef({ error: null as string | null, count: 0 })
  stateRef.current = { error, count: models.length }
  useEffect(() => {
    const client = getWsClient()
    const off = client.onStatusChange((s) => {
      if (s !== 'connected' || !mountedRef.current) return
      const { error: prevError, count } = stateRef.current
      if (prevError || count === 0) refresh()
    })
    return off
  }, [refresh])

  return { models, loading, error, refresh }
}
