import { create } from 'zustand'
import { getTranslations, type RawSourceItem } from '@desk-notes/shared'
import { getWsClient } from '../utils/ai'
import { useStore } from './useStore'

/**
 * P6b：资讯抓取全局状态层。
 * 组件卸载（切换模块）不影响抓取；结果统一写入 store，回到页面直接恢复显示。
 */
interface InfoFetchState {
  status: 'idle' | 'fetching' | 'done' | 'error'
  kind: 'brief' | 'feed' | null
  startedAt: number | null
  rawItems: RawSourceItem[]
  fetchErrors: Array<{ source: string; error: string }>
  cooldownUntil: number
}

export const useInfoFetchStore = create<InfoFetchState>(() => ({
  status: 'idle',
  kind: null,
  startedAt: null,
  rawItems: [],
  fetchErrors: [],
  cooldownUntil: 0,
}))

/** 剩余冷却秒数（0 = 可抓取） */
export function infoCooldownRemaining(): number {
  return Math.max(0, Math.ceil((useInfoFetchStore.getState().cooldownUntil - Date.now()) / 1000))
}

let running = false

/** 模块级抓取运行器：组件卸载不中断，防重复发起 */
export async function runInfoFetch(kind: 'brief' | 'feed'): Promise<void> {
  if (running) return
  const tr = getTranslations()
  const ws = getWsClient()
  const shared = useStore.getState()

  if (!ws || ws.status !== 'connected') {
    if (kind === 'brief') shared.setInfoError(tr.info.errBackendDisconnected)
    useInfoFetchStore.setState({
      status: 'error',
      kind,
      fetchErrors: [{ source: 'system', error: tr.info.errBackendOffline }],
    })
    return
  }

  running = true
  useInfoFetchStore.setState({ status: 'fetching', kind, startedAt: Date.now(), fetchErrors: [] })
  if (kind === 'brief') {
    shared.setInfoLoading(true)
    shared.setInfoError(null)
    shared.setInfoBrief('')
  }

  try {
    const topics = kind === 'brief' ? useStore.getState().infoTopics : []
    const result = await ws.call<{ brief?: string; items: RawSourceItem[]; errors: { source: string; error: string }[] }>(
      'info.brief', { topics, no_ai: kind === 'feed' },
    )
    useInfoFetchStore.setState({
      status: 'done',
      rawItems: result.items || [],
      fetchErrors: result.errors || [],
      cooldownUntil: Date.now() + 180_000,
    })
    if (kind === 'brief') {
      const s = useStore.getState()
      if (result.brief) {
        s.setInfoBrief(result.brief)
        s.setInfoLastFetch(Date.now())
      } else if ((result.items || []).length === 0) {
        s.setInfoError(tr.info.errNoSources)
      } else {
        s.setInfoBrief(tr.info.aiNoBrief)
        s.setInfoLastFetch(Date.now())
      }
    }
  } catch {
    if (kind === 'brief') {
      try {
        const cached = await ws.call<{ items: RawSourceItem[]; cached_at: number }>('info.cached_items', {})
        if (cached?.items?.length) {
          useInfoFetchStore.setState({ status: 'done', rawItems: cached.items })
          useStore.getState().setInfoError(tr.info.refreshFailedCached)
        } else {
          useInfoFetchStore.setState({ status: 'error' })
          useStore.getState().setInfoError(tr.info.refreshFailedNoCache)
        }
      } catch {
        useInfoFetchStore.setState({ status: 'error' })
        useStore.getState().setInfoError(tr.info.refreshFailed)
      }
    } else {
      useInfoFetchStore.setState({ status: 'error', rawItems: [] })
    }
  } finally {
    running = false
    if (kind === 'brief') useStore.getState().setInfoLoading(false)
  }
}

/** 后端 info.collected 推送事件的结果写入（保持与原逻辑一致） */
export function applyInfoCollected(payload: { brief?: string; items?: RawSourceItem[] }): void {
  if (payload.items) {
    useInfoFetchStore.setState({ rawItems: payload.items, status: 'done' })
  }
  // 后台 AI 完成后推送的 brief 也要写入，覆盖"AI 未生成简报"占位，
  // 否则组件若在抓取期间卸载，回挂后仍停留在占位文本。
  if (payload.brief) {
    const s = useStore.getState()
    s.setInfoBrief(payload.brief)
    s.setInfoLastFetch(Date.now())
  }
}
