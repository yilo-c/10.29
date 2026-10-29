import React, { useState, useEffect, useCallback } from 'react'
import Icon from './Icon'

import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'
import { errorStore } from '../../store/errorStore'
import { useTranslation } from '../../i18n'

interface TrashItem {
  id: string | number
  title: string
  deleted_at: string
}

interface Props {
  onClose: () => void
  onRestore: () => void
}

const TrashView: React.FC<Props> = ({ onClose, onRestore }) => {
  const { t } = useTranslation()
  const [items, setItems] = useState<TrashItem[]>([])
  const [loading, setLoading] = useState(true)
  const [offline, setOffline] = useState(false)
  const [pendingDeleteId, setPendingDeleteId] = useState<string | number | null>(null)

  const loadItems = useCallback(async () => {
    setLoading(true)
    try {
      const ws = getWsClient()
      if (ws.status !== 'connected') {
        setOffline(true)
        return
      }
      const result = await ws.call<TrashItem[]>('notes.list_trashed')
      setItems(result || [])
      setOffline(false)
    } catch (e) { swallowError('TrashView:26', e); errorStore.error(`${t('trashView.title')} ${t('ux.loadFailedSuffix')}`) }
    finally { setLoading(false) }
  }, [t])

  useEffect(() => { loadItems() }, [loadItems])

  const handleRestore = async (noteId: string | number) => {
    try {
      const ws = getWsClient()
      await ws.call('notes.restore', { note_id: noteId })
      setItems(prev => prev.filter(i => i.id !== noteId))
      onRestore()
    } catch (e) { swallowError('TrashView:38', e); errorStore.error(`${t('trashView.restore')} ${t('ux.restoreFailedSuffix')}`) }
  }

  const confirmPermanentDelete = async (noteId: string | number) => {
    setPendingDeleteId(null)
    // S0-2：后端 permanent_delete 现返回真实结果（True=已硬删 / False=未删 /
    // {pending:true}=转入 R4 审批）。旧实现丢弃返回值无条件抹条目——ghost 笔记
    // "删除成功"纯假象。现仅在 result===true 时移除；pending 提示去审批卡；
    // 其余情况报错保留条目（可重试）。
    let result: unknown = false
    try {
      const ws = getWsClient()
      result = await ws.call<unknown>('notes.permanent_delete', { note_id: noteId })
    } catch (e) {
      swallowError('TrashView:46', e)
      errorStore.error(`${t('trashView.permanentDelete')} ${t('ux.deleteFailedSuffix')}`)
      return
    }
    if (result === true) {
      setItems(prev => prev.filter(i => i.id !== noteId))
      return
    }
    if (result && typeof result === 'object' && (result as { pending?: boolean }).pending) {
      errorStore.info(t('ux.permDeletePendingHint'))
      return
    }
    errorStore.error(`${t('trashView.permanentDelete')} ${t('ux.deleteFailedSuffix')}`)
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-2 border-b shrink-0" style={{ borderColor: 'var(--panel-border)' }}>
        <h3 className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
          <Icon icon="fa-regular fa-trash-can mr-2 text-xs" style={{ color: 'var(--text-muted)' }} />
          {t('trashView.title')}
        </h3>
        <button
          onClick={onClose}
          className="text-xs px-2 py-1 rounded hover:bg-white/[0.06]"
          style={{ color: 'var(--text-muted)' }}
        >
          <Icon icon="fa-solid fa-xmark" /> {t('trashView.close')}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="w-4 h-4 border-2 border-white/20 border-t-white/70 rounded-full animate-spin" />
          </div>
        ) : offline ? (
          <div className="flex flex-col items-center justify-center py-8 gap-2">
            <Icon icon="fa-solid fa-plug-circle-bolt text-2xl" style={{ color: 'var(--text-dim)' }} />
            <div className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              {t('ux.offlineTitle')}
            </div>
            <div className="text-2xs text-center max-w-[240px]" style={{ color: 'var(--text-muted)' }}>
              {t('ux.offlineDesc')}
            </div>
            <button
              onClick={() => loadItems()}
              className="mt-1 text-xs px-3 py-1.5 rounded-lg border transition-all hover:bg-white/[0.06]"
              style={{ borderColor: 'var(--panel-border)', color: 'var(--text-secondary)' }}
            >
              {t('ux.offlineRetry')}
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="text-center py-8 text-xs" style={{ color: 'var(--text-muted)' }}>
            {t('trashView.empty')}
          </div>
        ) : (
          <div className="space-y-1">
            {items.map(item => (
              <React.Fragment key={String(item.id)}>
                <div
                  className="flex items-center gap-3 px-3 py-2 rounded-t-xl border"
                  style={{ borderColor: 'var(--panel-border)' }}
                >
                  <Icon icon="fa-regular fa-file-lines text-xs" style={{ color: 'var(--text-muted)' }} />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs truncate" style={{ color: 'var(--text-secondary)' }}>
                      {item.title || t('trashView.untitled')}
                    </div>
                    <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                      {t('trashView.deletedAt')} {new Date(item.deleted_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
                    </div>
                  </div>
                  <button
                    onClick={() => handleRestore(item.id)}
                    className="text-xs px-2 py-1 rounded hover:bg-white/[0.08]"
                    style={{ color: 'var(--success)' }}
                    title={t('trashView.restore')}
                  >
                    <Icon icon="fa-solid fa-rotate-left" /> {t('trashView.restore')}
                  </button>
                  <button
                    onClick={() => setPendingDeleteId(item.id)}
                    className="text-xs px-2 py-1 rounded hover:bg-white/[0.08]"
                    style={{ color: 'var(--danger)' }}
                    title={t('trashView.permanentDelete')}
                  >
                    <Icon icon="fa-solid fa-trash" />
                  </button>
                </div>
                {pendingDeleteId === item.id && (
                  <div
                    className="flex items-center justify-between gap-3 px-3 py-2 rounded-b-xl border border-t-0"
                    style={{ borderColor: 'var(--panel-border)', background: 'var(--hover-bg)' }}
                  >
                    <div className="min-w-0">
                      <div className="text-xs font-medium" style={{ color: 'var(--text-primary)' }}>
                        {t('ux.permanentDeleteConfirmTitle')}
                      </div>
                      <div className="text-2xs" style={{ color: 'var(--text-muted)' }}>
                        {t('ux.permanentDeleteConfirmDesc')}
                      </div>
                    </div>
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <button
                        onClick={() => setPendingDeleteId(null)}
                        className="text-xs px-2 py-1 rounded hover:bg-white/[0.08]"
                        style={{ color: 'var(--text-muted)' }}
                      >
                        {t('common.cancel')}
                      </button>
                      <button
                        onClick={() => confirmPermanentDelete(item.id)}
                        className="text-xs px-2 py-1 rounded hover:bg-red-500/20"
                        style={{ color: 'var(--danger)' }}
                      >
                        {t('ux.permanentDeleteConfirmOk')}
                      </button>
                    </div>
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default TrashView
