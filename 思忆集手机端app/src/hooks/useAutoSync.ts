import { useState, useCallback, useEffect } from 'react'
import { useStore } from '@desk-notes/shared'
import { fullContentSync } from '../lib/contentSync'
import { tokenManager } from '../lib/tokenManager'

export type SyncStatus = 'idle' | 'loading' | 'success' | 'error'

export function useAutoSync() {
  const [syncStatus, setSyncStatus] = useState<SyncStatus>('idle')
  const [syncMessage, setSyncMessage] = useState('')

  const triggerSync = useCallback(async (silent = false) => {
    if (!tokenManager.isPaired) {
      setSyncStatus('error')
      setSyncMessage('请先在设置中配对桌面端')
      return
    }

    if (!silent) {
      setSyncStatus('loading')
      setSyncMessage('正在同步...')
    }

    try {
      const result = await fullContentSync()

      if (silent) {
        // Silent background sync — just rehydrate from localStorage
        useStore.persist.rehydrate()
      } else {
        setSyncStatus('success')
        setSyncMessage(`同步完成：笔记 ${result.notes} / 任务 ${result.tasks} / 翻译 ${result.translations} / 词本 ${result.wordbooks}`)
        setTimeout(() => { setSyncStatus('idle'); setSyncMessage('') }, 3000)
      }
    } catch (err) {
      if (!silent) {
        setSyncStatus('error')
        setSyncMessage(err instanceof Error ? err.message : '同步失败，请检查网络连接')
      }
    }
  }, [])

  return { syncStatus, syncMessage, triggerSync }
}

/** On foreground, do one silent sync if never done */
export function useAutoSyncOnForeground() {
  const { triggerSync } = useAutoSync()

  useEffect(() => {
    if (!tokenManager.isPaired) return
    const key = 'siyi_auto_sync_done'
    if (sessionStorage.getItem(key)) return
    sessionStorage.setItem(key, 'true')

    const doSync = () => {
      if (!document.hidden) triggerSync(true)
    }

    if (!document.hidden) {
      doSync()
    } else {
      document.addEventListener('visibilitychange', doSync, { once: true })
    }

    return () => document.removeEventListener('visibilitychange', doSync)
  }, [triggerSync])
}
