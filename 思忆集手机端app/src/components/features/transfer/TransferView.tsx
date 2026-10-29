import { useState, useEffect, useCallback, useRef } from 'react'
import { Keyboard, KeyboardInfo } from '@capacitor/keyboard'
import { haptic } from '../../../lib/haptic'
import { callRpc, UnauthorizedError } from '../../../lib/rpcClient'
import { tokenManager } from '../../../lib/tokenManager'
import { isAiConfigured, translateText, summarizeText } from '../../../lib/mobileAiUtils'
import { useStore } from '@desk-notes/shared'
import { swallowError } from '../../../lib/swallowError'

interface TransferEntry { text: string; target: string; from_device: string; timestamp: number; _uid?: string; file?: string; fileName?: string; file_path?: string }
interface WsEvent { type?: string; event_type?: string; payload?: TransferEntry }

// Files larger than this (base64 length) will be uploaded via transfer.upload instead of inline
const FILE_INLINE_MAX = 500 * 1024 // 500KB

/** Convert http(s) URL to ws(s) URL */
function toWsUrl(httpUrl: string): string {
  return httpUrl.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:')
}

/** P0.2: 统一走带认证的 callRpc。无 token 时抛 UnauthorizedError，由 UI 提示重新配对。 */
function requirePaired(): void {
  if (!tokenManager.isPaired || !tokenManager.apiToken) {
    throw new UnauthorizedError('Pair device before transfer')
  }
}

/** Upload a file and return the server path */
async function uploadFile(fileData: string, fileName: string): Promise<string | null> {
  const params = { name: fileName, data: fileData }
  try {
    requirePaired()
    const result = await callRpc<{ success: boolean; path: string }>('transfer.upload', params)
    if (result?.success && result.path) return result.path
  } catch (e) { console.warn('[transfer] upload failed', e) }
  return null
}

/** P0.2: 删除匿名 LAN 回退，统一走 callRpc（带 Authorization Bearer） */
async function trySend(text: string, _uid: string, file?: string, fileName?: string, filePath?: string): Promise<void> {
  const params: Record<string, unknown> = { text, target: 'desktop', _uid }
  if (file && file.length < FILE_INLINE_MAX) { params.file = file; params.fileName = fileName }
  if (filePath) { params.file_path = filePath; if (!params.fileName) params.fileName = fileName }
  requirePaired()
  await callRpc('transfer.send', params)
}

async function tryHistory(): Promise<TransferEntry[]> {
  if (!tokenManager.isPaired || !tokenManager.apiToken) return []
  try {
    return await callRpc<TransferEntry[]>('transfer.history', { limit: 20 })
  } catch (e) {
    if (e instanceof UnauthorizedError) {
      console.warn('[transfer] history unauthorized — device needs re-pairing')
    } else {
      console.warn('[transfer] history failed', e)
    }
    return []
  }
}

const STORAGE_KEY = 'siyi-transfer-history'
function loadHistory(): TransferEntry[] {
  try { const raw = localStorage.getItem(STORAGE_KEY); return raw ? JSON.parse(raw) : [] }
  catch { return [] }
}
function saveHistory(entries: TransferEntry[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(entries.slice(0, 100)))
}

/** Read a file as base64 data URL */
function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (file.size > 20 * 1024 * 1024) { reject(new Error('文件不能超过 2MB')); return }
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('读取文件失败'))
    reader.readAsDataURL(file)
  })
}
function formatTime(ts: number): string {
  const diff = Date.now() - ts * 1000
  if (diff < 60000) return '刚刚'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`
  return new Date(ts * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

export default function TransferView() {
  const [input, setInput] = useState(() => sessionStorage.getItem('siyi-transfer-draft') || '')
  const [sending, setSending] = useState(false)
  const [pickedFile, setPickedFile] = useState<File | null>(null)
  const [history, setHistory] = useState<TransferEntry[]>(() => loadHistory())
  const [status, setStatus] = useState('')
  const [detailEntry, setDetailEntry] = useState<TransferEntry | null>(null)
  const [showAttachment, setShowAttachment] = useState(false)
  const [fullImage, setFullImage] = useState<string | null>(null)
  const [fileCache, setFileCache] = useState<Record<string, string>>({})
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  // Download files referenced by file_path
  const downloadFile = useCallback(async (filePath: string): Promise<string | null> => {
    if (fileCache[filePath]) return fileCache[filePath]
    // P0.2: 未配对时不尝试下载 —— 后端会返回 401
    if (!tokenManager.isPaired || !tokenManager.apiToken) return null
    try {
      const result = await callRpc<{ success: boolean; data: string }>('transfer.download', { path: filePath })
      if (result?.success && result.data) {
        setFileCache(prev => ({ ...prev, [filePath]: result.data }))
        return result.data
      }
    } catch (e) {
      if (e instanceof UnauthorizedError) {
        console.warn('[transfer] download unauthorized — device needs re-pairing')
      } else {
        console.warn('[transfer] download failed', e)
      }
    }
    return null
  }, [fileCache])

  // Keyboard avoidance — use Capacitor Keyboard plugin (works on all Android devices)
  // H12: 同 ChatView 的修复 — Promise.then 句柄塞进 handles 数组前要先看
  // 是否已 unmount，否则 cleanup 漏 remove → 幽灵监听
  useEffect(() => {
    const handles: { remove: () => void }[] = []
    let cancelled = false
    const trackHandle = (h: { remove: () => void }) => {
      if (cancelled) {
        try { h.remove() } catch (e) { /* ignore */ swallowError('TransferView:150', e) }
      } else {
        handles.push(h)
      }
    }
    Keyboard.addListener('keyboardWillShow', (info: KeyboardInfo) => {
      if (rootRef.current) {
        // keyboardHeight is the full keyboard + safe area on some devices
        const pad = Math.round(info.keyboardHeight * 0.75)
        rootRef.current.style.paddingBottom = `${pad}px`
      }
      setTimeout(() => {
        inputRef.current?.scrollIntoView({ block: 'end', behavior: 'smooth' })
      }, 100)
    }).then(trackHandle).catch(() => { /* plugin unavailable on web */ })
    Keyboard.addListener('keyboardWillHide', () => {
      if (rootRef.current) rootRef.current.style.paddingBottom = '0px'
    }).then(trackHandle).catch(() => { /* plugin unavailable on web */ })

    return () => {
      cancelled = true
      handles.forEach(h => { try { h.remove() } catch (e) { /* ignore */ swallowError('TransferView:171', e) } })
    }
  }, [])

  // Auto-scroll to bottom when new messages arrive
  const scrollToBottom = useCallback(() => {
    requestAnimationFrame(() => {
      if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight
    })
  }, [])

  useEffect(() => { scrollToBottom() }, [history, scrollToBottom])

  // ── WebSocket 实时接收（配对模式）──
  useEffect(() => {
    if (!tokenManager.isPaired) return
    const wsUrl = toWsUrl(tokenManager.baseUrl) + '/ws'
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let closed = false

    const connect = () => {
      if (closed) return
      ws = new WebSocket(wsUrl)
      ws.onopen = () => { console.log('[transfer] WS connected') }
      ws.onmessage = (event) => {
        try {
          const msg: WsEvent = JSON.parse(event.data)
          if (msg.type === 'event' && msg.event_type === 'transfer.received' && msg.payload) {
            const entry = msg.payload
            // Desktop→mobile: only add if from_device !== 'mobile'
            if (entry.from_device === 'mobile') return
            setHistory(prev => {
              const exists = prev.some(e => e._uid && e._uid === entry._uid) || prev.some(e => e.timestamp === entry.timestamp && e.text === entry.text)
              if (exists) return prev
              const updated = [...prev, entry].sort((a, b) => a.timestamp - b.timestamp).slice(-100)
              saveHistory(updated)
              return updated
            })
            setStatus('📥 收到消息')
            setTimeout(() => setStatus(''), 3000)
          }
        } catch (e) { /* ignore parse errors */ swallowError('TransferView:213', e) }
      }
      ws.onclose = () => {
        ws = null
        if (!closed) reconnectTimer = setTimeout(connect, 5000)
      }
      ws.onerror = () => { ws?.close() }
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [])

  // Poll — 每 3s 拉取远程历史（作为 WebSocket 的兜底，非配对时全时运行）
  useEffect(() => {
    const poll = async () => {
      const remote = await tryHistory()
      if (remote.length > 0) {
        const local = loadHistory()
        const localIds = new Set(local.map(e => e._uid || '').filter(Boolean))
        // Also check by text+tstamp for entries without _uid
        const localTextKeys = new Set(local.filter(e => !e._uid).map(e => e.text.slice(0, 40) + Math.floor(e.timestamp / 2)))
        const merged = [...local]
        for (const entry of remote) {
          if (entry._uid && localIds.has(entry._uid)) continue
          const key = entry.text.slice(0, 40) + Math.floor(entry.timestamp / 2)
          if (!entry._uid && localTextKeys.has(key)) continue
          merged.unshift(entry)
        }
        merged.sort((a, b) => a.timestamp - b.timestamp)
        saveHistory(merged); setHistory(merged.slice(0, 100))
      }
    }
    poll(); const timer = setInterval(poll, 3000)
    return () => clearInterval(timer)
  }, [])

  // Auto-download files from file_path references
  useEffect(() => {
    for (const entry of history) {
      if (entry.file_path && !entry.file && !fileCache[entry.file_path]) {
        downloadFile(entry.file_path)
      }
    }
  }, [history, fileCache, downloadFile])

  // 发送后立即刷新一次历史，不等轮询间隔
  const refreshHistoryAfterSend = useCallback(async () => {
    const remote = await tryHistory()
    if (remote.length > 0) {
      setHistory(prev => {
        const localIds = new Set(prev.map(e => e._uid || '').filter(Boolean))
        const localTextKeys = new Set(prev.filter(e => !e._uid).map(e => e.text.slice(0, 40) + Math.floor(e.timestamp / 2)))
        const merged = [...prev]
        for (const entry of remote) {
          if (entry._uid && localIds.has(entry._uid)) continue
          const key = entry.text.slice(0, 40) + Math.floor(entry.timestamp / 2)
          if (!entry._uid && localTextKeys.has(key)) continue
          merged.unshift(entry)
        }
        merged.sort((a, b) => a.timestamp - b.timestamp)
        saveHistory(merged)
        return merged.slice(0, 100)
      })
    }
  }, [])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if ((!text && !pickedFile) || sending) return
    haptic.light(); setSending(true)
    const uid = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`
    let fileData = ''
    let fileName = ''
    let filePath = ''
    if (pickedFile) {
      try {
        fileData = await readFileBase64(pickedFile)
        fileName = pickedFile.name
        // Large files: upload first, send reference instead of inline base64
        if (fileData.length >= FILE_INLINE_MAX) {
          const uploaded = await uploadFile(fileData, fileName)
          if (uploaded) { filePath = uploaded; fileData = '' } // clear inline data, use reference
          // If upload fails, still try inline (may be rejected by backend size limit)
        }
      } catch (err) { setStatus(`❌ ${err instanceof Error ? err.message : '文件读取失败'}`); setSending(false); return }
    }
    const entry: TransferEntry = { text, target: 'desktop', from_device: 'mobile', timestamp: Date.now() / 1000, _uid: uid, file: fileData || undefined, fileName: fileName || undefined, file_path: filePath || undefined }
    const updated = [...history, entry]; saveHistory(updated); setHistory(updated); setInput(''); setPickedFile(null); sessionStorage.removeItem('siyi-transfer-draft')
    try {
      await trySend(text, uid, fileData || undefined, fileName || undefined, filePath || undefined)
      setStatus('✅ 已发送')
      // 立即刷新历史，不等轮询
      refreshHistoryAfterSend()
    } catch (e) {
      console.warn('[transfer] send failed', e)
      // P0.2: 未配对 / token 失效时给用户明确的"重新配对"提示，而非泛化的发送失败
      if (e instanceof UnauthorizedError) {
        setStatus('❌ 未配对或令牌已失效，请重新配对设备')
      } else {
        setStatus(`❌ ${e instanceof Error ? e.message : '发送失败'}`)
      }
    }

  }, [input, pickedFile, history, refreshHistoryAfterSend])

  return (
    <div ref={rootRef} data-keyboard-aware style={{ display: 'flex', flexDirection: 'column', height: '100%', transition: 'padding-bottom 0.2s ease' }}>
      <div style={{ padding: 'var(--safe-top) var(--space-4) var(--space-2)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h1 style={{ fontSize: 'var(--text-large-title)', fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>
            传送
            {status && <span style={{ fontSize: 'var(--text-footnote)', fontWeight: 400, marginLeft: 12, color: status.startsWith('✅') ? 'var(--tone-smooth)' : status.startsWith('⚠️') ? 'var(--tone-caution)' : 'var(--danger)' }}>{status}</span>}
          </h1>
        </div>
      </div>

      <div ref={listRef} style={{ flex: 1, overflowY: 'auto', padding: '0 var(--space-4)', paddingBottom: 8 }}>
        {history.length === 0 && <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-tertiary)', fontSize: 'var(--text-footnote)' }}>暂无传送记录</div>}
        {history.map((entry, i) => {
          const isOutgoing = entry.from_device === 'mobile'
          return (
            <div key={`${entry.timestamp}-${i}`}
              style={{ display: 'flex', justifyContent: isOutgoing ? 'flex-end' : 'flex-start', marginBottom: 6, padding: '0' }}>
              <div onClick={() => setDetailEntry(entry)} style={{ cursor: 'pointer', maxWidth: '80%' }}>
                {/* File preview — inline base64 or downloaded from file_path */}
                {(entry.file || entry.file_path) && (
                  <div style={{ marginBottom: 4 }}>
                    {(() => {
                      const src = entry.file || (entry.file_path && fileCache[entry.file_path])
                      if (src) {
                        if (src.startsWith('data:image/')) {
                          return <img src={src} onClick={(e) => { e.stopPropagation(); setFullImage(src) }} style={{ cursor: 'zoom-in', maxWidth: 200, maxHeight: 200, borderRadius: 10, objectFit: 'cover' }} alt={entry.fileName || '图片'} />
                        }
                        if (src.startsWith('data:video/')) {
                          return <div style={{ padding: '20px 16px', borderRadius: 10, background: 'var(--bg-tertiary)', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 'var(--text-caption-1)' }}>🎬 {entry.fileName || '视频文件'}</div>
                        }
                      }
                      return <div style={{ padding: '12px 16px', borderRadius: 10, background: 'var(--bg-tertiary)', textAlign: 'center', color: 'var(--text-tertiary)', fontSize: 'var(--text-caption-1)' }}>📎 {entry.fileName || '文件'}{entry.file_path ? ' (点击加载)' : ''}</div>
                    })()}
                  </div>
                )}
                {entry.text && (
                  <div style={{
                    padding: '8px 12px', borderRadius: 14,
                    background: isOutgoing ? 'var(--accent)' : 'var(--bg-tertiary)',
                    color: isOutgoing ? '#fff' : 'var(--text-primary)',
                    fontSize: 'var(--text-footnote)', lineHeight: 1.5,
                    wordBreak: 'break-word', whiteSpace: 'pre-wrap',
                  }}>
                    {entry.text.length > 150 ? entry.text.slice(0, 150) + '…' : entry.text}
                  </div>
                )}
                <div style={{ textAlign: isOutgoing ? 'right' : 'left', marginTop: 2, padding: '0 4px' }}>
                  <span style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
                    {formatTime(entry.timestamp)} {isOutgoing ? '已发送' : ''}
                  </span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Bottom input bar */}
      {/* Spacer to push content above input when scrolled */}
      <div style={{ height: 1, flexShrink: 0 }} />

      {/* Bottom input bar — sticky so keyboard pushes it up naturally */}
      <div style={{ flexShrink: 0, display: 'flex', alignItems: 'flex-end', gap: 8, padding: '8px var(--space-4) calc(8px + env(safe-area-inset-bottom))', borderTop: '0.5px solid var(--border-subtle)', background: 'var(--bg-primary)' }}>
        {/* Attachment button */}
        <button onClick={() => setShowAttachment(true)}
          style={{ width: 36, height: 36, borderRadius: '50%', border: '0.5px solid var(--border-default)', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0, fontSize: 18, background: 'none' }}>
          📎
        </button>

        {/* Picked file preview */}
        {pickedFile && (
          <div style={{ position: 'absolute', bottom: '100%', left: 0, right: 0, padding: '8px var(--space-4)', marginBottom: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 'var(--radius-sm)', background: 'var(--accent-surface)', border: '0.5px solid var(--accent-border)' }}>
              <span style={{ fontSize: 24 }}>
                {pickedFile.type.startsWith('image/') ? '🖼' : pickedFile.type.startsWith('video/') ? '🎬' : '📄'}
              </span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pickedFile.name}</div>
                <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
                  {pickedFile.size > 1024 * 1024 ? (pickedFile.size / 1024 / 1024).toFixed(1) + ' MB' : (pickedFile.size / 1024).toFixed(0) + ' KB'}
                </div>
              </div>
              <button onClick={() => setPickedFile(null)}
                style={{ background: 'none', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontSize: 16, padding: 4 }}>✕</button>
            </div>
          </div>
        )}
        {/* Textarea */}
        <textarea ref={inputRef as any} value={input} onChange={e => { setInput(e.target.value); sessionStorage.setItem('siyi-transfer-draft', e.target.value); e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px' }}
          onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() } }}
          placeholder="输入文本..."
          rows={1}
          style={{ flex: 1, padding: '8px 14px', borderRadius: 20, border: '0.5px solid var(--border-default)', outline: 'none', resize: 'none', fontSize: 'var(--text-body)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontFamily: 'inherit', lineHeight: 1.4, maxHeight: 120 }} />
        {/* Send button */}
        <button onClick={handleSend} disabled={(!input.trim() && !pickedFile) || sending}
          style={{ width: 38, height: 38, borderRadius: '50%', border: 'none', background: (!input.trim() && !pickedFile) || sending ? 'var(--bg-tertiary)' : 'var(--accent)', color: '#fff', fontSize: 16, cursor: 'pointer', flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: (!input.trim() && !pickedFile) || sending ? 0.5 : 1 }}>
          ↑
        </button>
      </div>


      {/* Attachment picker sheet */}
      {showAttachment && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 2000, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', background: 'rgba(0,0,0,0.4)' }}
          onClick={() => setShowAttachment(false)}>
          <div style={{ width: '100%', maxWidth: 400, padding: 'var(--space-4)', paddingBottom: 'calc(var(--space-4) + env(safe-area-inset-bottom))', background: 'var(--bg-secondary)', borderTopLeftRadius: 'var(--radius-lg)', borderTopRightRadius: 'var(--radius-lg)' }}
            onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 'var(--text-headline)', fontWeight: 600, marginBottom: 16, color: 'var(--text-primary)', textAlign: 'center' }}>
              选择文件
            </div>
            <div style={{ display: 'flex', gap: 16, justifyContent: 'center', marginBottom: 20 }}>
              {/* 拍照 */}
              <label style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <div style={{ width: 56, height: 56, borderRadius: 'var(--radius-md)', background: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24 }}>
                  📷
                </div>
                <span style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-secondary)' }}>拍照</span>
                <input type="file" accept="image/*" capture="environment" style={{ display: 'none' }}
                  onChange={e => { const f = e.target.files?.[0]; if (f) { setPickedFile(f); setShowAttachment(false) } }} />
              </label>
              {/* 相册 */}
              <label style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <div style={{ width: 56, height: 56, borderRadius: 'var(--radius-md)', background: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24 }}>
                  🖼️
                </div>
                <span style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-secondary)' }}>相册</span>
                <input type="file" accept="image/*,video/*" style={{ display: 'none' }}
                  onChange={e => { const f = e.target.files?.[0]; if (f) { setPickedFile(f); setShowAttachment(false) } }} />
              </label>
              {/* 文件 */}
              <label style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                <div style={{ width: 56, height: 56, borderRadius: 'var(--radius-md)', background: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 24 }}>
                  📄
                </div>
                <span style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-secondary)' }}>文件</span>
                <input type="file" accept="*/*" style={{ display: 'none' }}
                  onChange={e => { const f = e.target.files?.[0]; if (f) { setPickedFile(f); setShowAttachment(false) } }} />
              </label>
            </div>
            <button onClick={() => setShowAttachment(false)}
              style={{ width: '100%', padding: '12px 0', borderRadius: 'var(--radius-sm)', border: 'none', background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', fontSize: 'var(--text-body)', cursor: 'pointer' }}>
              取消
            </button>
          </div>
        </div>
      )}

      {fullImage && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 3000, background: 'rgba(0,0,0,0.92)', display: 'flex', alignItems: 'center', justifyContent: 'center' }} onClick={() => setFullImage(null)}>
          <img src={fullImage} alt="" style={{ maxWidth: '95%', maxHeight: '95%', objectFit: 'contain', borderRadius: 8 }} />
          <button onClick={() => setFullImage(null)} style={{ position: 'absolute', top: 16, right: 16, background: 'rgba(255,255,255,0.15)', border: 'none', color: '#fff', fontSize: 20, width: 36, height: 36, borderRadius: '50%', cursor: 'pointer' }}>X</button>
        </div>
      )}

      {detailEntry && <DetailSheet entry={detailEntry} onClose={() => setDetailEntry(null)} onViewImage={setFullImage} />}
    </div>
  )
}


function DetailSheet({ entry, onClose, onViewImage }: {
  entry: TransferEntry; onClose: () => void; onViewImage?: (s: string) => void
}) {
  const [ar, setAr] = useState<{ type: string; text: string } | null>(null)
  const [al, setAl] = useState('')
  const [sc, setSc] = useState(1)
  const o = entry.from_device === 'mobile'
  const ai = isAiConfigured()

  function fmt(ts: number): string {
    const d = Date.now() - ts * 1000
    if (d < 60000) return '刚刚'
    if (d < 3600000) return Math.floor(d / 60000) + '分钟前'
    if (d < 86400000) return Math.floor(d / 3600000) + '小时前'
    return new Date(ts * 1000).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  }

  const btn = { height: 28, padding: '0 10px', borderRadius: 6, border: 'none', fontSize: 11, cursor: 'pointer', whiteSpace: 'nowrap' as const }

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 2000, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'flex-end', justifyContent: 'center' }} onClick={onClose}>
      <div style={{ width: '100%', maxWidth: 400, maxHeight: '80vh', background: 'var(--bg-secondary)', borderTopLeftRadius: 16, borderTopRightRadius: 16, overflow: 'hidden', display: 'flex', flexDirection: 'column' }} onClick={e => e.stopPropagation()}>

        <div style={{ padding: '14px 16px 10px', borderBottom: '0.5px solid var(--border-subtle)', display: 'flex', justifyContent: 'space-between' }}>
          <span style={{ fontSize: 17, fontWeight: 600, color: 'var(--text-primary)' }}>{o ? '发送到桌面' : '来自桌面'}</span>
          <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>{fmt(entry.timestamp)}</span>
        </div>

        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          {entry.file?.startsWith('data:image/') ? (
            <img src={entry.file} alt="" style={{ width: '100%', maxHeight: 300, borderRadius: 8, objectFit: 'contain', transform: `scale(${sc})`, transformOrigin: 'top center', marginBottom: 10 }} />
          ) : null}
          {entry.text ? (
            <div style={{ fontSize: 13, color: 'var(--text-primary)', lineHeight: 1.6, wordBreak: 'break-word', whiteSpace: 'pre-wrap', marginBottom: 12 }}>{entry.text}</div>
          ) : null}

          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, paddingBottom: 40 }}>
            {entry.file?.startsWith('data:image/') ? (
              <>
                <button onClick={() => setSc(v => Math.max(0.5, v - 0.25))} style={{ ...btn, background: 'none', border: '0.5px solid var(--border-default)', color: 'var(--accent)', width: 28, textAlign: 'center' }}>-</button>
                <span style={{ fontSize: 10, color: 'var(--text-tertiary)', padding: '6px 2px' }}>{Math.round(sc * 100)}%</span>
                <button onClick={() => setSc(v => Math.min(5, v + 0.25))} style={{ ...btn, background: 'none', border: '0.5px solid var(--border-default)', color: 'var(--accent)', width: 28, textAlign: 'center' }}>+</button>
                <button onClick={() => setSc(1)} style={{ ...btn, background: 'none', border: '0.5px solid var(--border-default)', color: 'var(--text-tertiary)' }}>原</button>
                {onViewImage ? <button onClick={() => onViewImage(entry.file!)} style={{ ...btn, background: 'rgba(96,165,250,0.12)', color: '#60a5fa' }}>原图</button> : null}
              </>
            ) : null}
            <button onClick={() => { navigator.clipboard?.writeText(entry.text); haptic.light(); setAr({ type: 'c', text: '✅ 已复制' }) }} style={{ ...btn, background: 'rgba(96,165,250,0.12)', color: '#60a5fa' }}>复制</button>
            {ai ? (
              <>
                <button onClick={async () => { setAl('s'); try { const t = await summarizeText(entry.text); setAr({ type: 's', text: t }) } catch { setAr({ type: 's', text: '摘要失败' }) }; setAl('') }} disabled={!!al} style={{ ...btn, background: 'rgba(168,85,247,0.12)', color: '#a855f7', opacity: al ? 0.4 : 1 }}>{al === 's' ? '摘要中…' : 'AI 摘要'}</button>
                <button onClick={async () => { setAl('t'); try { const t = await translateText(entry.text); setAr({ type: 't', text: t }) } catch { setAr({ type: 't', text: '翻译失败' }) }; setAl('') }} disabled={!!al} style={{ ...btn, background: 'rgba(168,85,247,0.12)', color: '#a855f7', opacity: al ? 0.4 : 1 }}>{al === 't' ? '翻译中…' : 'AI 翻译'}</button>
                <button onClick={() => { useStore.getState().addFloatingNote({ id: Date.now() + '-' + Math.random().toString(36).slice(2, 6), title: entry.text.slice(0, 40), content: entry.text, type: 'text', x: 0, y: 0, zIndex: 1, width: 320, height: 200, createdAt: Date.now(), updatedAt: Date.now(), tags: ['传送'], editMode: 'markdown' }); setAr({ type: 'n', text: '✅ 已保存' }) }} style={{ ...btn, background: 'rgba(52,211,153,0.12)', color: '#34d399' }}>存为笔记</button>
              </>
            ) : null}
            <button onClick={onClose} style={{ ...btn, background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', marginLeft: 'auto' }}>关闭</button>
          </div>
          {ar ? <div style={{ fontSize: 10, color: 'var(--text-secondary)', paddingBottom: 8 }}>{ar.text}</div> : null}
        </div>
      </div>
    </div>
  )
}
