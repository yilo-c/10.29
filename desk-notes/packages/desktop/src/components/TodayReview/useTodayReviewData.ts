import { useCallback, useEffect, useRef, useState } from 'react'
import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'
import type { InfoItemRaw, NoteRaw } from './todayReviewLogic'

/**
 * V1-6「今日回顾」数据源：只复用既有后端 RPC，不新增方法。
 *
 * - info.items.list { limit, sort_by:'collected_at', sort_desc:true } → { items, total }
 *   （backend/handlers/_info_items.py，服务端 limit 上限 500，且无时间范围参数）
 * - notes.list { limit, include_empty:false } → Note[]
 *   （backend/handlers/_notes.py，返回含 updated_at / created_at 的 ISO 串）
 *
 * 降级登记：info.items.list 缺 since/until 参数 → 取 top 500 后由前端按本地日过滤
 * （见 todayReviewLogic.filterByLocalDay）。单日捕获若超过 500 条会被截断。
 */

export const INFO_ITEMS_LIMIT = 500
export const NOTES_LIMIT = 500

export interface TodayReviewSource {
  infoItems: InfoItemRaw[]
  notes: NoteRaw[]
}

export interface TodayReviewData extends TodayReviewSource {
  loading: boolean
  error: string | null
  reload: () => Promise<void>
}

const EMPTY: TodayReviewSource = { infoItems: [], notes: [] }

export function useTodayReviewData(): TodayReviewData {
  const [source, setSource] = useState<TodayReviewSource>(EMPTY)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const aliveRef = useRef(true)

  useEffect(() => {
    aliveRef.current = true
    return () => { aliveRef.current = false }
  }, [])

  const reload = useCallback(async () => {
    setLoading(true)
    setError(null)
    const client = getWsClient()
    try {
      const [itemsRes, notesRes] = await Promise.all([
        client.call<{ items?: InfoItemRaw[]; total?: number; error?: string }>('info.items.list', {
          limit: INFO_ITEMS_LIMIT,
          sort_by: 'collected_at',
          sort_desc: true,
        }),
        client.call<NoteRaw[]>('notes.list', { limit: NOTES_LIMIT, include_empty: false }),
      ])
      if (!aliveRef.current) return
      if (itemsRes?.error) throw new Error(itemsRes.error)
      setSource({
        infoItems: Array.isArray(itemsRes?.items) ? itemsRes.items : [],
        notes: Array.isArray(notesRes) ? notesRes : [],
      })
    } catch (e: unknown) {
      swallowError('TodayReview:load', e)
      if (aliveRef.current) setError(e instanceof Error ? e.message : String(e))
    } finally {
      if (aliveRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  return { ...source, loading, error, reload }
}
