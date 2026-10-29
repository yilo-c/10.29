import React, { useEffect, useState } from 'react'
import { useStore } from '../../store/useStore'
import { initFileStore } from '../../utils/fileStore'
import { useTranslation } from '../../i18n'
import HeaderBar from '../MainWindow/HeaderBar'
import KnowledgeGraphModule from './KnowledgeGraphModule'

/** 知识图谱独立窗口视图（P4c）：暗夜玻璃沉浸式探索 */
const StandaloneKnowledgeGraphView: React.FC = () => {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        await initFileStore()
        await useStore.getState().loadNotesFromDisk()
        if (!cancelled) setLoading(false)
      } catch (e: unknown) {
        console.error('[StandaloneKnowledgeGraphView] init failed:', e)
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [])

  if (loading) {
    return (
      <div className="w-screen h-screen flex items-center justify-center select-none"
        style={{ background: '#0a0c14' }}>
        <span className="text-xs text-white/48">{t('common.loading')}</span>
      </div>
    )
  }

  return (
    <div
      className="fixed inset-0 overflow-hidden flex flex-col"
      style={{ background: '#0a0c14' }}
    >
      <HeaderBar />
      <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
        <KnowledgeGraphModule />
      </div>
    </div>
  )
}

export default StandaloneKnowledgeGraphView
