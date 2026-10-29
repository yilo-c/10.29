import React, { useState, useEffect, useCallback, useRef } from 'react'
import Icon from '../Common/Icon'

import { getWsClient, getAIProvider, DEFAULT_LOCAL_API_URL, DEFAULT_LOCAL_MODEL } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'
import { errorStore } from '../../store/errorStore'
import { useTranslation } from '../../i18n'

interface TransferEntry {
  text: string
  target: string
  from_device: string
  timestamp: number
  _uid?: string
  file?: string
  fileName?: string
  file_path?: string
  category?: string
  action?: string
}

const MAX_HISTORY = 100

function formatTime(ts: number, t: (key: string, params?: Record<string, string | number>) => string): string {
  const diff = Date.now() - ts * 1000
  if (diff < 60000) return t('transfer.timeJustNow')
  if (diff < 3600000) return t('transfer.timeMinAgoFmt', { value: Math.floor(diff / 60000) })
  if (diff < 86400000) return t('transfer.timeHourAgoFmt', { value: Math.floor(diff / 3600000) })
  return new Date(ts * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

const TransferView: React.FC = () => {
  const { t } = useTranslation()
  const [input, setInput] = useState(() => sessionStorage.getItem('siyi-transfer-draft') || '')
  const [sending, setSending] = useState(false)
  const [history, setHistory] = useState<TransferEntry[]>([])
  const [pastedImage, setPastedImage] = useState<string | null>(null)
  const [detailEntry, setDetailEntry] = useState<TransferEntry | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const listRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const wsClient = getWsClient()

  const toggleSelect = useCallback((key: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const clearSelection = useCallback(() => setSelectedIds(new Set()), [])

  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (listRef.current && !userScrolledUp.current) listRef.current.scrollTop = listRef.current.scrollHeight
    })
  }, [])

  const userScrolledUp = useRef(false)
  const handleListScroll = useCallback(() => {
    if (!listRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = listRef.current
    userScrolledUp.current = scrollTop + clientHeight < scrollHeight - 40
  }, [])

  const loadHistory = useCallback(async () => {
    if (wsClient.status !== 'connected') return
    try {
      const result = await wsClient.call<TransferEntry[]>('transfer.history', { limit: 50 })
      if (result) { setHistory([...result].reverse()); scrollToBottom() }
    } catch (e) { /* ignore */ swallowError('TransferView:69', e) }
  }, [wsClient, scrollToBottom])

  useEffect(() => { loadHistory() }, [loadHistory])

  useEffect(() => {
    // A-3：transfer.received 事件已实时推送（见下方订阅），轮询仅作漏事件兜底；
    // 旧值 3s 常驻烧 20 msg/min 配额，是后端共享限流桶的主要压力源之一。
    const timer = setInterval(() => { loadHistory() }, 30_000)
    return () => clearInterval(timer)
  }, [loadHistory])

  useEffect(() => { scrollToBottom() }, [history, scrollToBottom])

  useEffect(() => {
    const unsub = wsClient.on('transfer.received', (event) => {
      const payload = event.payload as unknown as TransferEntry | undefined
      if (payload) {
        setHistory(prev => [...prev, payload].slice(-MAX_HISTORY))
        errorStore.info(t('transfer.receivedMessage'))
        setTimeout(() => scrollToBottom(), 100)
      }
    })
    return unsub
  }, [wsClient, scrollToBottom, t])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if ((!text && !pastedImage) || sending) return
    setSending(true)
    const uid = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
    try {
      const params: Record<string, unknown> = { text, target: 'mobile', _uid: uid }
      if (pastedImage) { params.file = pastedImage; params.fileName = 'clipboard.png' }
      try {
        await wsClient.call('transfer.send', params)
      } catch {
        // Retry once after short delay (connection may have been stale)
        await new Promise(r => setTimeout(r, 800))
        await wsClient.call('transfer.send', params)
      }
      const entry: TransferEntry = { text, target: 'mobile', from_device: 'desktop', timestamp: Date.now() / 1000, _uid: uid, file: pastedImage || undefined, fileName: pastedImage ? 'clipboard.png' : undefined }
      setHistory(prev => [...prev, entry].slice(-MAX_HISTORY))
      setTimeout(() => scrollToBottom(), 50)
      setInput(''); sessionStorage.removeItem('siyi-transfer-draft')
      setPastedImage(null); sessionStorage.removeItem('siyi-transfer-last-image')
      if (textareaRef.current) textareaRef.current.style.height = 'auto'
      errorStore.success(t('transfer.sentToMobile'))
    } catch { errorStore.error(t('transfer.sendFailed')) }
    finally { setSending(false) }
  }, [input, pastedImage, sending, wsClient, scrollToBottom, t])

  return (
    <div className="flex-1 flex flex-col min-h-0 p-4 gap-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium" style={{ color: 'var(--text-primary)' }}>
          <Icon icon="fa-solid fa-paper-plane mr-2 text-xs" style={{ color: 'var(--brand)' }} />{t('transfer.title')}
        </span>
        </div>

      {selectedIds.size > 0 && (
        <div className="flex items-center gap-2 py-2 px-3 rounded-lg" style={{ background: 'rgba(96,165,250,0.1)' }}>
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('transfer.selectedCountFmt', { count: selectedIds.size })}</span>
          <button onClick={() => {
            const selected = history.filter((_, i) => selectedIds.has(`${history[i].timestamp}-${i}`))
            const allText = selected.map(e => e.text).filter(Boolean).join('\n---\n')
            if (allText) navigator.clipboard?.writeText(allText)
          }} className="text-xs px-2 py-1 rounded" style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}>
            {t('transfer.copyAll')}
          </button>
          <button onClick={clearSelection} className="text-xs px-2 py-1 rounded ml-auto" style={{ color: 'var(--text-muted)' }}>
            {t('transfer.clearSelection')}
          </button>
        </div>
      )}

      {/* Message list */}
      <div ref={listRef} onScroll={handleListScroll} className="flex-1 overflow-y-auto min-h-0">
        {history.length === 0 && <div className="text-center py-8 text-xs" style={{ color: 'var(--text-muted)' }}>{t('transfer.emptyHistory')}</div>}
        {history.map((entry, i) => {
          const isOutgoing = entry.from_device === 'desktop'
          const key = `${entry.timestamp}-${i}`
          const isSelected = selectedIds.has(key)
          return (
            <div key={key} style={{ display: 'flex', justifyContent: isOutgoing ? 'flex-end' : 'flex-start', marginBottom: 6 }}>
              <div onClick={() => { if (selectedIds.size > 0) { toggleSelect(key) } else { setDetailEntry(entry) } }}
                onContextMenu={e => { e.preventDefault(); toggleSelect(key) }}
                style={{ cursor: 'pointer', maxWidth: '70%', opacity: isSelected ? 0.6 : 1, outline: isSelected ? '2px solid var(--brand)' : 'none', outlineOffset: 2, borderRadius: 10 }}>
                {/* Image preview */}
                {entry.file?.startsWith('data:image/') && (
                  <img src={entry.file} alt={entry.fileName || t('transfer.imageAlt')} loading="lazy" style={{ maxWidth: 240, maxHeight: 180, borderRadius: 10, objectFit: 'cover', marginBottom: entry.text ? 4 : 0 }} />
                )}
                {entry.text && (
                  <div style={{ padding: '8px 12px', borderRadius: 14, background: isOutgoing ? 'rgba(96,165,250,0.15)' : 'rgba(255,255,255,0.06)', color: 'var(--text-primary)', fontSize: '12px', lineHeight: 1.5, wordBreak: 'break-word', whiteSpace: 'pre-wrap' }}>
                    {entry.text}
                  </div>
                )}
                {entry.category && (
                  <div style={{ display: "flex", gap: 4, marginTop: 3 }}>
                    <span className="text-[8px] px-1.5 py-0.5 rounded" style={{ background: entry.category === "link" ? "rgba(96,165,250,0.12)" : entry.category === "image" ? "rgba(52,211,153,0.12)" : entry.category === "task" ? "rgba(251,146,60,0.12)" : "var(--bg-tertiary)", color: entry.category === "link" ? "var(--brand)" : entry.category === "image" ? "#34d399" : entry.category === "task" ? "#fb923c" : "var(--text-muted)" }}>
                      {entry.category === "link" ? "🔗" : entry.category === "image" ? "🖼" : entry.category === "task" ? "📋" : "⚡"}
                    </span>
                    {entry.action && <span className="text-[8px]" style={{ color: "#34d399" }}>{entry.action}</span>}
                  </div>
                )}
                <div style={{ textAlign: isOutgoing ? 'right' : 'left', marginTop: 2, padding: '0 4px' }}>
                  <span className="text-2xs" style={{ color: 'var(--text-muted)' }}>{formatTime(entry.timestamp, t)} {isOutgoing ? t('transfer.sentLabel') : ''}</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Pasted image preview */}
      {pastedImage && (
        <div className="flex-shrink-0 flex items-center gap-2" style={{ padding: '4px 0' }}>
          <img src={pastedImage} alt={t('transfer.pastedImageAlt')} loading="lazy" style={{ maxWidth: 80, maxHeight: 60, borderRadius: 6, objectFit: 'cover' }} />
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('transfer.pastedImageHint')}</span>
          <button onClick={() => { setPastedImage(null); sessionStorage.removeItem('siyi-transfer-last-image') }}
            className="ml-auto text-xs px-2 py-1 rounded"
            style={{ background: 'rgba(239,68,68,0.15)', color: 'var(--danger)', border: 'none', cursor: 'pointer' }}>
            {t('transfer.remove')}
          </button>
        </div>
      )}

      {/* Bottom input bar */}
      <div className="flex gap-2 flex-shrink-0" style={{ paddingTop: 4 }}>
        {/* File picker button */}
        <label style={{ display: 'flex', alignItems: 'center', cursor: 'pointer', flexShrink: 0 }}>
          <input type="file" accept="image/*,video/*,*/*" style={{ display: 'none' }}
            onChange={e => {
              const file = e.target.files?.[0]
              if (file) {
                if (file.size > 20 * 1024 * 1024) { errorStore.error(t('transfer.fileTooLarge')); return }
                const reader = new FileReader()
                reader.onload = (ev) => {
                  const dataUrl = ev.target?.result as string
                  setPastedImage(dataUrl)
                  sessionStorage.setItem('siyi-transfer-last-image', dataUrl)
                }
                reader.readAsDataURL(file)
              }
              e.target.value = ''
            }}
          />
          <span className="flex items-center justify-center text-xs w-[26px] h-[26px] rounded-lg select-none"
            style={{ background: 'rgba(96,165,250,0.12)', color: 'var(--brand)', cursor: 'pointer' }}>
            <Icon icon="fa-solid fa-paperclip" />
          </span>
        </label>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={e => {
            setInput(e.target.value)
            e.target.style.height = 'auto'
            e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'; sessionStorage.setItem('siyi-transfer-draft', e.target.value)
          }}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
          }}
          onPaste={(e) => {
            const items = e.clipboardData?.items
            if (items) {
              for (const item of items) {
                if (item.type.startsWith('image/')) {
                  e.preventDefault()
                  const file = item.getAsFile()
                  if (file) {
                    const reader = new FileReader()
                    reader.onload = (ev) => {
                      const dataUrl = ev.target?.result as string
                      setPastedImage(dataUrl)
                      sessionStorage.setItem('siyi-transfer-last-image', dataUrl)
                    }
                    reader.readAsDataURL(file)
                  }
                }
              }
            }
          }}
          placeholder={t('transfer.inputPlaceholder')}
          rows={1}
          className="flex-1 px-3 py-2 rounded-lg text-sm bg-white/[0.04] border border-white/[0.08] outline-none resize-none"
          style={{ color: 'var(--text-primary)', fontFamily: 'inherit', lineHeight: 1.4, maxHeight: 120 }}
        />
        <button onClick={handleSend} disabled={(!input.trim() && !pastedImage) || sending}
          className="px-4 py-2 rounded-lg text-xs font-medium disabled:opacity-30 self-end"
          style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}>
          {sending ? '…' : t('transfer.send')}
        </button>
      </div>

      {detailEntry && <DetailSheetDesktop entry={detailEntry} onClose={() => setDetailEntry(null)} />}
    </div>
  )
}

const DetailSheetDesktop: React.FC<{ entry: TransferEntry; onClose: () => void }> = ({ entry, onClose }) => {
  const { t } = useTranslation()
  const [aiResult, setAiResult] = useState<{ type: string; text: string } | null>(null)
  const [aiLoading, setAiLoading] = useState('')
  const [imgScale, setImgScale] = useState(1)
  const aiProviderRef = useRef(getAIProvider({ enabled: true, apiUrl: DEFAULT_LOCAL_API_URL, model: DEFAULT_LOCAL_MODEL, protocol: 'ollama' as const, autoTagging: false }))
  const isOutgoing = entry.from_device === 'desktop'

  const handleCopy = () => {
    navigator.clipboard?.writeText(entry.text)
    setAiResult({ type: 'copy', text: t('transfer.copiedToClipboard') })
  }

  const handleSummarize = async () => {
    setAiLoading('summarize')
    try {
      const text = await aiProviderRef.current.processText('summarize' as any, entry.text, () => {})
      setAiResult({ type: 'summarize', text })
    } catch { setAiResult({ type: 'summarize', text: t('transfer.aiSummaryFailed') }) }
    finally { setAiLoading('') }
  }

  return (
    <div className="fixed inset-0 z-[99999] flex items-center justify-center p-6"
      style={{ background: 'rgba(0,0,0,0.5)' }} onClick={onClose}>
      <div className="w-full max-w-md max-h-[70vh] flex flex-col rounded-xl border shadow-2xl"
        style={{ background: 'var(--panel-bg)', borderColor: 'var(--panel-border)' }}
        onClick={e => e.stopPropagation()}>

        <div className="flex items-center justify-between px-4 py-3 border-b shrink-0" style={{ borderColor: 'var(--panel-border)' }}>
          <span className="text-base font-semibold" style={{ color: 'var(--text-primary)' }}>
            {isOutgoing ? t('transfer.detailSendTitle') : t('transfer.detailFromTitle')}
          </span>
          <span className="text-xs" style={{ color: 'var(--text-muted)' }}>{formatTime(entry.timestamp, t)}</span>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {entry.file?.startsWith('data:image/') && (
            <div style={{ overflow: 'auto', textAlign: 'center' }}>
              <img src={entry.file} alt={entry.fileName || t('transfer.imageAlt')} loading="lazy"
                style={{ transform: `scale(${imgScale})`, transformOrigin: 'top center', transition: 'transform 0.15s ease', maxWidth: imgScale > 1 ? 'none' : '100%' }} />
            </div>
          )}
          {entry.text && (
            <div className="text-sm leading-relaxed whitespace-pre-wrap break-all mt-2" style={{ color: 'var(--text-secondary)' }}>
              {entry.text}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 px-4 py-3 border-t shrink-0 flex-wrap" style={{ borderColor: 'var(--panel-border)' }}>
          {entry.file?.startsWith('data:image/') && (
            <div className="flex items-center gap-1 mr-1">
              <button onClick={() => setImgScale(s => Math.max(0.5, s - 0.25))}
                className="text-xs w-6 h-6 rounded border flex items-center justify-center" style={{ borderColor: 'var(--panel-border)' }}>-</button>
              <span className="text-2xs" style={{ color: 'var(--text-muted)', minWidth: 28, textAlign: 'center' }}>{Math.round(imgScale * 100)}%</span>
              <button onClick={() => setImgScale(s => Math.min(5, s + 0.25))}
                className="text-xs w-6 h-6 rounded border flex items-center justify-center" style={{ borderColor: 'var(--panel-border)' }}>+</button>
              <button onClick={() => setImgScale(1)}
                className="text-2xs px-1.5 h-6 rounded border ml-1" style={{ borderColor: 'var(--panel-border)' }}>{t('transfer.imageOriginal')}</button>
            </div>
          )}
          <button onClick={handleCopy}
            className="px-3 py-1.5 rounded-lg text-xs"
            style={{ background: 'rgba(96,165,250,0.12)', color: 'var(--brand)' }}>
            {t('transfer.copy')}
          </button>
          <button onClick={handleSummarize} disabled={!!aiLoading}
            className="px-3 py-1.5 rounded-lg text-xs disabled:opacity-40"
            style={{ background: 'rgba(168,85,247,0.12)', color: '#a855f7' }}>
            {aiLoading === 'summarize' ? t('transfer.aiSummarizing') : t('transfer.aiSummary')}
          </button>
          <button onClick={async () => {
            try {
              const ws = getWsClient()
              await ws.call('notes.create', { title: entry.text.slice(0, 40), content: entry.text, tags: [t('transfer.noteTag')] })
              setAiResult({ type: 'note', text: t('transfer.savedToNote') })
            } catch { setAiResult({ type: 'note', text: t('transfer.saveFailed') }) }
          }}
            className="px-3 py-1.5 rounded-lg text-xs"
            style={{ background: 'rgba(52,211,153,0.12)', color: '#34d399' }}>
            {t('transfer.saveAsNote')}
          </button>
          <button onClick={onClose}
            className="ml-auto px-3 py-1.5 rounded-lg text-xs"
            style={{ color: 'var(--text-muted)' }}>
            {t('transfer.close')}
          </button>
        </div>

        {aiResult && (
          <div className="px-4 pb-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
            {aiResult.type === 'copy' ? '📋' : <Icon icon="fa-solid fa-wand-magic-sparkles mr-1 text-xs" style={{ color: '#a855f7' }} />}
            {' '}{aiResult.text}
          </div>
        )}
      </div>
    </div>
  )
}

export default TransferView