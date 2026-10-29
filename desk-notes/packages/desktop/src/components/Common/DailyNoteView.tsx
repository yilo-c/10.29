import { useState, useEffect, useCallback, useRef } from 'react'
import Icon from './Icon'

import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'
import { errorStore } from '../../store/errorStore'
import { useTranslation } from '../../i18n'

interface Props {
  onClose: () => void
}

export default function DailyNoteView({ onClose }: Props) {
  const { t } = useTranslation()
  const [content, setContent] = useState('')
  const [title, setTitle] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [quickInput, setQuickInput] = useState('')
  const [noteId, setNoteId] = useState('')
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const loadToday = useCallback(async () => {
    const client = getWsClient()
    // B6-M2: 未连接时也要复位 loading，避免永久转圈
    if (client.status !== 'connected') {
      setLoading(false)
      return
    }
    try {
      const result = await client.call<{ note: { id: string; title: string; content: string }; date: string }>('daily.get', {})
      if (result?.note) {
        setNoteId(result.note.id)
        setTitle(result.note.title || '')
        setContent(result.note.content || '')
      }
    } catch (e) {
      swallowError('DailyNoteView:27', e)
      // B6-M2: 失败也复位，不让用户面对无限加载
      errorStore.error(t('dailyNote.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadToday() }, [loadToday])

  // Auto-save content changes after 2s debounce
  const handleContentChange = (val: string) => {
    setContent(val)
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current)
    saveTimerRef.current = setTimeout(async () => {
      const client = getWsClient()
      if (client.status !== 'connected' || !noteId) return
      setSaving(true)
      try {
        await client.call('notes.update', { note_id: noteId, content: val, title })
      } catch (e) {
        swallowError('DailyNoteView:43', e)
        // B6-M2: 自动保存失败必须让用户知道（内容差异只在本地）
        errorStore.error(t('dailyNote.saveFailed'))
        // B6-M2: 断连期间的改动不会上行——提示重连后手动检查
        if (client.status !== 'connected') {
          errorStore.warn(t('dailyNote.offlineHint'))
        }
      } finally {
        setSaving(false)
      }
    }, 2000)
  }

  const handleQuickAdd = async () => {
    const text = quickInput.trim()
    if (!text) return
    const client = getWsClient()
    if (client.status !== 'connected') return
    setQuickInput('')
    try {
      const result = await client.call<{ success: boolean }>('daily.append', { content: text, source: 'manual' })
      if (result?.success) await loadToday()
    } catch (e) {
      swallowError('DailyNoteView:57', e)
      // B6-M2: 追加失败要提示，否则用户以为已记录
      errorStore.error(t('dailyNote.appendFailed'))
    }
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b flex-shrink-0" style={{ borderColor: 'var(--panel-border)' }}>
        <div className="flex items-center gap-2">
          <Icon icon="fa-solid fa-pen text-xs" style={{ color: 'var(--warning)' }} />
          <span className="text-base font-medium" style={{ color: 'var(--text-primary)' }}>{title || t('dailyNote.todayRecord')}</span>
          {saving && <span className="text-2xs" style={{ color: 'var(--text-dim)' }}>{t('dailyNote.saving')}</span>}
        </div>
        <button onClick={onClose} className="w-6 h-6 flex items-center justify-center rounded text-sm hover:bg-white/[0.06]" style={{ color: 'var(--text-muted)' }}>
          <Icon icon="fa-solid fa-xmark" />
        </button>
      </div>

      {loading ? (
        <div className="flex-1 flex items-center justify-center">
          <div className="w-4 h-4 border-2 border-fluent-blue/30 border-t-fluent-blue rounded-full animate-spin" />
        </div>
      ) : (
        <div className="flex-1 flex flex-col min-h-0">
          {/* Quick add input */}
          <div className="flex-shrink-0 px-4 py-2 border-b" style={{ borderColor: 'var(--panel-border)' }}>
            <div className="flex items-center gap-2">
              <input
                value={quickInput}
                onChange={e => setQuickInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleQuickAdd() }}
                placeholder={t('dailyNote.quickAdd')}
                className="flex-1 px-2.5 py-1.5 rounded-lg border text-xs outline-none"
                style={{ background: 'var(--input-bg)', borderColor: 'var(--panel-border)', color: 'var(--text-primary)' }}
              />
              <button onClick={handleQuickAdd} disabled={!quickInput.trim()}
                className="flex-shrink-0 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-30 disabled:cursor-not-allowed">
                <Icon icon="fa-solid fa-plus" />
              </button>
            </div>
          </div>

          {/* Content editor */}
          <div className="flex-1 overflow-y-auto p-4">
            <textarea
              value={content}
              onChange={e => handleContentChange(e.target.value)}
              placeholder={t('dailyNote.placeholder')}
              className="w-full h-full bg-transparent border-none outline-none resize-none text-sm leading-relaxed"
              style={{ color: 'var(--text-primary)' }}
            />
          </div>
        </div>
      )}
    </div>
  )
}
