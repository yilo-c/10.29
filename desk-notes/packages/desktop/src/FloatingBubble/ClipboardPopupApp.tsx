import React, { useEffect, useState, useCallback, useRef } from 'react'
import Icon from '../components/Common/Icon'

import { getWsClient } from '@desk-notes/shared'
import { useStore } from '../store/useStore'
import WordCards, { type WordEntry } from './WordCards'
import SpeakButton from '../components/Common/SpeakButton'
import { classifyClipboardIntent } from './clipboardIntent'
import { swallowError } from '../utils/swallowError'

/** Call backend via renderer-side WebSocket first (confirmed working),
 *  fall back to IPC relay through main process. The IPC relay can have stale
 *  connections if the backend was restarted, so we prefer the direct WS. */
async function callWithRetry<T>(
  method: string,
  params: Record<string, unknown>,
  ei?: ElectronAPI,
  timeoutMs = 5000,
): Promise<T> {
  const attempt = async (): Promise<T> => {
    // Prefer renderer-side WebSocket if already connected
    const client = getWsClient()
    if (client.status === 'connected') {
      try {
        return await client.call<T>(method, params, timeoutMs > 0 ? timeoutMs : undefined)
      } catch (e) {
        // WebSocket call failed, fall through to IPC
        swallowError('ClipboardPopupApp:23', e)
      }
    }

    // IPC relay through main process (may have stale connection after restart)
    if (ei?.rpcCall) {
      const resp = await ei.rpcCall(method, params)
      if (resp.success) return resp.data as T
      throw new Error(resp.error || 'RPC failed')
    }

    // Last resort: direct WebSocket (might not be ready yet)
    if (client.status === 'connected') {
      return client.call<T>(method, params, timeoutMs > 0 ? timeoutMs : undefined)
    }
    throw new Error('Not connected')
  }

  if (!timeoutMs || timeoutMs <= 0) return attempt()
  // V1-7：前端侧超时防御（本次不动后端超时）
  return Promise.race([
    attempt(),
    new Promise<never>((_, reject) => {
      setTimeout(() => reject(new Error(`${method} 超时（${Math.round(timeoutMs / 1000)}s）`)), timeoutMs)
    }),
  ])
}

/** Actions that require the backend WebSocket connection. */
const RPC_ACTIONS = new Set(['translate', 'summarize', 'polish', 'fetch_url', 'add_word'])

/** Count characters, words, lines locally. */
function localWordCount(text: string): string {
  const chars = text.length
  const words = text.split(/[\s\u3000]+/).filter(Boolean).length
  const lines = text.split('\n').length
  return `字符：${chars}\n词语：${words}\n行数：${lines}`
}

/** Get or create the default wordbook ID. Used by both the add_word action
 *  and the silent post-translate auto-add path, so we keep one source of truth. */
async function ensureDefaultWordbookId(ei?: ElectronAPI): Promise<number> {
  const wordbooks = await callWithRetry<Array<{ id: number; name: string }>>('wordbook.list', {}, ei)
  if (wordbooks && wordbooks.length > 0) return wordbooks[0].id
  const created = await callWithRetry<{ id: number; name: string }>('wordbook.create', { name: '默认单词本' }, ei)
  return created.id
}

/** Silently add a (word, meaning) pair to the default wordbook. */
async function addToWordbook(word: string, meaning: string, source = 'clipboard_translate'): Promise<void> {
  try {
    const wbId = await ensureDefaultWordbookId()
    await callWithRetry('word.add', {
      wordbook_id: wbId,
      word,
      meaning,
      example: '',
      source,
    })
  } catch (e) {
    // Silently fail — don't disturb the user
    swallowError('ClipboardPopupApp:73', e)
  }
}

const FLOATING_CONV_ID = '__floating_bubble__'

const PATTERN_ACTIONS = [
  { id: 'translate', label: '翻译', icon: 'fa-language' },
  { id: 'summarize', label: '总结', icon: 'fa-compress' },
  { id: 'polish', label: '润色', icon: 'fa-wand-magic-sparkles' },
  { id: 'save_note', label: '保存笔记', icon: 'fa-note-sticky' },
  { id: 'fetch_url', label: '提取链接', icon: 'fa-link' },
  { id: 'word_count', label: '字数统计', icon: 'fa-calculator' },
  { id: 'add_word', label: '加入生词本', icon: 'fa-book' },
] as const

/**
 * ClipboardPopupApp — small popup window at cursor position.
 *
 * Shows detected clipboard text with pattern action buttons.
 * Selected action executes via WebSocket RPC; result is injected
 * into the floating chat conversation (visible when user opens chat).
 * Popup auto-closes on blur (click outside) or after action.
 */
const ClipboardPopupApp: React.FC = () => {
  const ei = window.electronAPI as ElectronAPI | undefined
  const [text, setText] = useState('')
  const [patternBusy, setPatternBusy] = useState('')
  const [status, setStatus] = useState('')
  const [actionOutput, setActionOutput] = useState('')
  const [resultLang, setResultLang] = useState<'en' | 'zh'>('zh')
  const [wsStatus, setWsStatus] = useState<'connected' | 'disconnected' | 'connecting'>('disconnected')
  const [words, setWords] = useState<WordEntry[]>([])
  const [suggestedAction, setSuggestedAction] = useState<string | null>(null)
  const actionRan = useRef(false)
  const dragRef = useRef({ dragging: false, startX: 0, startY: 0 })
  const dragActiveRef = useRef(false)
  const dragCooldownRef = useRef(false)
  const dragCooldownTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const userResizedRef = useRef(false)
  const prevActionOutput = useRef('')

  // Listen for clipboard text from main process
  useEffect(() => {
    if (!ei?.onClipboardPopupText) return
    const cleanup = ei.onClipboardPopupText((data: { text: string }) => {
      setText(data.text || '')
    })
    return cleanup
  }, [ei])

  // Classify intent when text changes
  useEffect(() => {
    if (text) {
      const intent = classifyClipboardIntent(text)
      setSuggestedAction(intent)
    } else {
      setSuggestedAction(null)
    }
  }, [text])

  // Pre-init WebSocket connection + listen for live status changes
  useEffect(() => {
    const client = getWsClient()
    // Force connect attempt
    if (client.status === 'disconnected') client.connect()
    const unsub = client.onStatusChange((s) => {
      setWsStatus(s === 'connected' ? 'connected' : s === 'connecting' ? 'connecting' : 'disconnected')
    })
    // Sync React state with current client status (handles Strict Mode double-mount
    // where the status change happened before the listener was registered)
    setWsStatus(client.status === 'connected' ? 'connected' : 'disconnected')
    return () => { unsub() }
  }, [])

  // Ensure floating chat conversation exists and is active (so addMessage works)
  useEffect(() => {
    const store = useStore.getState()
    const exists = store.conversations.find(c => c.id === FLOATING_CONV_ID)
    if (!exists) {
      useStore.setState(s => ({
        conversations: [
          ...s.conversations,
          { id: FLOATING_CONV_ID, title: '浮动助手', createdAt: Date.now(), updatedAt: Date.now(), messages: [] },
        ],
      }))
    }
    useStore.getState().setActiveConversation?.(FLOATING_CONV_ID)
  }, [])

  // Listen for manual resize from main process (user dragged window border)
  useEffect(() => {
    if (!ei?.onClipboardPopupResized) return
    const cleanup = ei.onClipboardPopupResized(() => {
      userResizedRef.current = true
    })
    return cleanup
  }, [ei])

  // Close on Escape
  useEffect(() => {
    return () => {
      // Reset action lock on unmount (prevents stale lock on next open)
      actionRan.current = false
      // Clean up drag cooldown timer on unmount
      if (dragCooldownTimerRef.current) {
        clearTimeout(dragCooldownTimerRef.current)
        dragCooldownTimerRef.current = null
      }
    }
  }, [])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        ei?.closeClipboardPopup?.()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [ei])

  // Drag-to-resize handler for bottom-right corner
  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    const startX = e.screenX
    const startY = e.screenY
    const startW = window.innerWidth
    const startH = window.innerHeight

    const handleResizeMove = (ev: MouseEvent) => {
      const newW = Math.max(240, Math.min(560, startW + ev.screenX - startX))
      const newH = Math.max(140, Math.min(520, startH + ev.screenY - startY))
      ei?.resizeClipboardPopup?.({ width: newW, height: newH })
    }
    const handleResizeUp = () => {
      window.removeEventListener('mousemove', handleResizeMove)
      window.removeEventListener('mouseup', handleResizeUp)
    }
    window.addEventListener('mousemove', handleResizeMove)
    window.addEventListener('mouseup', handleResizeUp)
  }, [ei])

  // Content-adaptive resize: grow when output appears, shrink when cleared.
  // Skips if user has manually resized the window, or during/after drag.
  useEffect(() => {
    if (dragActiveRef.current || dragCooldownRef.current) return
    if (userResizedRef.current) return

    const hasOutput = !!actionOutput
    const hadOutput = !!prevActionOutput.current

    // Only adapt when actionOutput transitions from empty → non-empty
    if (hasOutput && !hadOutput) {
      ei?.resizeClipboardPopup?.({ width: 340, height: 420 })
    }

    prevActionOutput.current = actionOutput
  }, [actionOutput, ei])

  // Drag handlers for title bar
  const handleDragStart = useCallback((e: React.MouseEvent) => {
    // Only left button
    if (e.button !== 0) return
    dragActiveRef.current = true
    dragRef.current.dragging = true
    dragRef.current.startX = e.screenX
    dragRef.current.startY = e.screenY

    const handleDragMove = (ev: MouseEvent) => {
      if (!dragRef.current.dragging) return
      const dx = ev.screenX - dragRef.current.startX
      const dy = ev.screenY - dragRef.current.startY
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
        // First meaningful move: send dragStart with offset so window stays under cursor
        ei?.dragStart?.(-(ev.screenX - window.screenX), -(ev.screenY - window.screenY))
        dragRef.current.dragging = false // dragStart takes over via main process polling
      }
    }
    const handleDragUp = () => {
      dragActiveRef.current = false
      dragRef.current.dragging = false
      dragCooldownRef.current = true
      ei?.dragEnd?.()
      // Cooldown: prevent resize effect from overriding user's drag position for 200ms
      if (dragCooldownTimerRef.current) clearTimeout(dragCooldownTimerRef.current)
      dragCooldownTimerRef.current = setTimeout(() => { dragCooldownRef.current = false; dragCooldownTimerRef.current = null }, 200)
      window.removeEventListener('mousemove', handleDragMove)
      window.removeEventListener('mouseup', handleDragUp)
    }

    window.addEventListener('mousemove', handleDragMove)
    window.addEventListener('mouseup', handleDragUp)
  }, [ei])

  // Execute pattern action: RPC for server-side, local for offline-capable
  const handleAction = useCallback(async (actionId: string) => {
    if (!text || patternBusy || actionRan.current) return
    actionRan.current = true
    setPatternBusy(actionId)
    setStatus('处理中…')
    setActionOutput('')
    setWords([])
    let producedOutput = false

    try {
      // Offline-capable actions
      if (actionId === 'word_count') {
        const output = localWordCount(text)
        setActionOutput(output)
        producedOutput = true
        setStatus('✅ 完成')
        const label = PATTERN_ACTIONS.find(a => a.id === actionId)?.label ?? actionId
        useStore.getState().addMessage(
          `✅ **${label}** 完成：\n\n${output}`,
          'assistant',
        )
        setTimeout(() => ei?.closeClipboardPopup?.(), 600)
        return
      }

      // Check connection before requiring RPC — only block if no IPC fallback
      if (wsStatus !== 'connected' && RPC_ACTIONS.has(actionId) && !ei?.rpcCall) {
        setStatus('🔌 后端未连接，请稍后重试')
        producedOutput = true // keep popup open to show error
        return
      }

      // save_note: connected → RPC, offline → local fileStore
      if (actionId === 'save_note') {
        try {
          if (wsStatus === 'connected') {
            await callWithRetry('notes.create', {
              title: text.slice(0, 40),
              content: text,
              tags: ['剪贴板'],
            }, ei)
          } else if (ei?.fileStore?.writeNote) {
            const noteId = 'clipboard-' + Date.now()
            await ei.fileStore.writeNote(noteId, {
              title: text.slice(0, 40),
              content: text,
              createdAt: Date.now(),
              updatedAt: Date.now(),
            })
          } else {
            setStatus('❌ 保存不可用')
            producedOutput = true // keep popup open to show error
            return
          }
          setActionOutput(`已保存到便签：${text.slice(0, 30)}…`)
          setStatus('✅ 已保存')
          producedOutput = true
        } catch {
          setStatus('❌ 保存失败')
        }
        return
      }

      // ── add_word: add clipboard text to word bank ──
      if (actionId === 'add_word') {
        const wordText = text.trim().slice(0, 200)
        if (!wordText) {
          setStatus('❌ 内容为空')
          return
        }
        try {
          // First, translate the word to get its meaning
          let meaning = ''
          try {
            const trans = await callWithRetry<{ translated: string }>('translate.text', { text: wordText, source: 'en', target: 'zh' }, ei)
            if (trans?.translated && trans.translated !== wordText) {
              meaning = trans.translated.slice(0, 200)
            }
          } catch (e) { /* translation is best-effort */ swallowError('ClipboardPopupApp:350', e) }
          const wbId = await ensureDefaultWordbookId(ei)
          await callWithRetry('word.add', {
            wordbook_id: wbId,
            word: wordText,
            meaning,
            example: '',
            source: 'clipboard',
          }, ei)
          setActionOutput(`✅ 已加入单词本：${wordText.length > 50 ? wordText.slice(0, 50) + '…' : wordText}${meaning ? `\n释义：${meaning}` : ''}`)
          setStatus('✅ 完成')
          producedOutput = true
          const label = PATTERN_ACTIONS.find(a => a.id === actionId)?.label ?? actionId
          useStore.getState().addMessage(
            `📚 **${label}**：${wordText}${meaning ? ` — ${meaning}` : ''}`,
            'assistant',
          )
        } catch (err: unknown) {
          setStatus('❌ 添加失败')
          useStore.getState().addMessage(
            `❌ **加入单词本** 失败：${err instanceof Error ? err.message : '未知错误'}`,
            'assistant',
          )
        }
        setTimeout(() => ei?.closeClipboardPopup?.(), 800)
        return
      }

      // Single combined call: translation + word extraction
      const source = actionId === 'translate_en' ? 'zh' : 'en'
      const target = actionId === 'translate_en' ? 'en' : 'zh'
      setResultLang(target === 'en' ? 'en' : 'zh')
      // Use translate.text (fast) for the main translation; word extraction
      // is handled separately when user clicks "add to wordbook"
      const resp = await callWithRetry<{ translated: string; words?: WordEntry[] }>(
        'translate.text',
        { text: text.trim(), source, target },
        ei,
      )
      const output = resp?.translated ?? ''

      setActionOutput(output)
      producedOutput = true
      setStatus('✅ 完成')

      const label = PATTERN_ACTIONS.find(a => a.id === actionId)?.label ?? actionId
      useStore.getState().addMessage(
        `✅ **${label}** 完成：\n\n${output}`,
        'assistant',
      )

      if (resp?.words?.length) {
        setWords(resp.words)
      }

      // Auto-add translation to default wordbook (silently, don't block UI)
      if ((actionId === 'translate' || actionId === 'translate_en') && text.trim().length <= 500) {
        addToWordbook(text.trim().slice(0, 200), output.trim().slice(0, 500))
      }
    } catch (err: unknown) {
      const errMsg = err instanceof Error ? err.message : '未知错误'
      setStatus(`❌ ${errMsg.slice(0, 60)}`)
      useStore.getState().addMessage(
        `❌ **${PATTERN_ACTIONS.find(a => a.id === actionId)?.label ?? actionId}** 失败：${err instanceof Error ? err.message : '未知错误'}`,
        'assistant',
      )
    } finally {
      // Always cleanup — handles all early-return paths (word_count, save_note, add_word, etc.)
      actionRan.current = false
      setPatternBusy('')
      if (!producedOutput) {
        setTimeout(() => ei?.closeClipboardPopup?.(), 600)
      }
    }
  }, [text, patternBusy, ei, actionOutput])

  // URL learning action
  const handleUrlLearn = useCallback(async (url: string) => {
    setPatternBusy('url_learn')
    setStatus('学习中…')
    setActionOutput('')
    try {
      const result = await callWithRetry<{ title: string; summary: string; key_points: string[] }>('url_learning.learn', { url, timeout: 30 }, ei)
      const output = [
        `**${result.title || url}**`,
        result.summary ? `\n${result.summary}` : '',
        result.key_points?.length ? `\n\n要点：\n${result.key_points.map((p: string) => `- ${p}`).join('\n')}` : '',
      ].filter(Boolean).join('')
      setActionOutput(output)
      setStatus('✅ 学习完成')
      useStore.getState().addMessage(
        `📖 **链接学习**：${url}\n\n${output}`,
        'assistant',
      )
    } catch {
      setStatus('❌ 学习失败')
    }
  }, [ei])

  // ── V1-7③：截图 OCR — captureScreen（preload→screen:capture）+ ocr.extract_base64 桥 ──
  const handleScreenshotOcr = useCallback(async () => {
    if (patternBusy) return
    setPatternBusy('screenshot_ocr')
    setStatus('截屏识别中…')
    setActionOutput('')
    try {
      if (!ei?.captureScreen) throw new Error('当前环境不支持桌面截图')
      const shot = await ei.captureScreen()
      if (!shot?.success || !shot.dataUrl) throw new Error(shot?.error || '截图失败')
      // ⑥ 空 payload 防御：OCR 无文字不写入任何下游
      const resp = await callWithRetry<{ success?: boolean; text?: string; error?: string }>(
        'ocr.extract_base64', { data: shot.dataUrl }, ei, 30_000,
      )
      const ocrText = String(resp?.text ?? '').trim()
      if (!resp?.success || !ocrText) throw new Error(resp?.error || '未识别到文字')
      setActionOutput(ocrText)
      setResultLang(/[一-鿿]/.test(ocrText) ? 'zh' : 'en')
      setStatus('✅ OCR 完成')
      useStore.getState().addMessage(`📸 **截图 OCR**：\n\n${ocrText.slice(0, 800)}`, 'assistant')
    } catch (err: unknown) {
      setStatus(`❌ ${err instanceof Error ? err.message.slice(0, 60) : '截图识别失败'}`)
    } finally {
      setPatternBusy('')
    }
  }, [patternBusy, ei])

  // Auto-close if text never arrives (safety)
  useEffect(() => {
    const timer = setTimeout(() => {
      if (!text) ei?.closeClipboardPopup?.()
    }, 5000)
    return () => clearTimeout(timer)
  }, [text, ei])

  return (
    <div
      className="flex flex-col"
      style={{
        width: '100vw',
        height: '100vh',
        position: 'relative',
        background: 'var(--surface)',
        borderRadius: 12,
        overflow: 'hidden',
        userSelect: 'none',
      }}
    >
      {/* Drag handle: text preview bar */}
      <div
        className="flex items-center gap-2 px-3 py-2 flex-shrink-0"
        style={{
          background: 'var(--surface-elevated)',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
          cursor: 'move',
        }}
        onMouseDown={handleDragStart}
      >
        <Icon icon="fa-solid fa-grip-lines text-xs" style={{ color: 'rgba(255,255,255,0.25)' }} />
        <Icon icon="fa-solid fa-paste text-xs" style={{ color: 'var(--brand)' }} />
        <span
          className="text-[11px] leading-tight truncate"
          style={{ color: 'rgba(255,255,255,0.55)' }}
        >
          {text.length > 80 ? text.slice(0, 80) + '…' : text}
        </span>
        <button
          onClick={() => ei?.closeClipboardPopup?.()}
          className="ml-auto flex-shrink-0 w-5 h-5 flex items-center justify-center rounded transition-colors"
          style={{ color: 'rgba(255,255,255,0.3)' }}
          onMouseEnter={e => { (e.target as HTMLElement).style.color = 'rgba(255,255,255,0.7)' }}
          onMouseLeave={e => { (e.target as HTMLElement).style.color = 'rgba(255,255,255,0.3)' }}
        >
          <Icon icon="fa-solid fa-xmark text-xs" />
        </button>
      </div>

      {/* Action buttons / result display */}
      <div className="flex-1 flex flex-col justify-center px-3 py-2 gap-1 overflow-hidden">
        {status ? (
          <div className="flex flex-col gap-2 py-2 overflow-hidden" style={{ maxHeight: '100%' }}>
            <div className="flex items-center justify-center gap-2 flex-shrink-0">
              <span className="text-[12px]" style={{ color: 'rgba(255,255,255,0.7)' }}>
                {status}
              </span>
              {actionOutput && (
                <SpeakButton text={actionOutput} lang={resultLang} variant="icon-sm" />
              )}
            </div>
            {actionOutput && (
              <>
                <div
                  className="text-xs leading-relaxed overflow-y-auto px-1"
                  style={{
                    color: 'rgba(255,255,255,0.8)',
                    maxHeight: words.length > 0 ? '120px' : '240px',
                    wordBreak: 'break-word',
                    whiteSpace: 'pre-wrap',
                  }}
                >
                  {actionOutput}
                </div>
                {words.length > 0 && (
                  <div className="overflow-y-auto px-1" style={{ maxHeight: '160px' }}>
                    <WordCards words={words} onSaved={() => {}} />
                  </div>
                )}
                <button
                  onClick={() => ei?.closeClipboardPopup?.()}
                  className="flex items-center justify-center gap-1 px-3 py-1.5 rounded-lg text-xs transition-all mt-1 flex-shrink-0"
                  style={{ background: 'rgba(96,165,250,0.15)', color: 'var(--brand)' }}
                >
                  <Icon icon="fa-solid fa-check text-[10px]" />
                  关闭
                </button>
              </>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-1">
            {/* V1-7③：截图 OCR 入口（captureScreen → ocr.extract_base64） */}
            {ei?.captureScreen && (
              <button
                onClick={() => void handleScreenshotOcr()}
                disabled={!!patternBusy}
                className="flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg text-[11px] transition-all disabled:opacity-30"
                style={{
                  background: patternBusy === 'screenshot_ocr' ? 'rgba(52,211,153,0.25)' : 'rgba(52,211,153,0.12)',
                  color: '#34d399',
                }}
              >
                <Icon icon={`fa-solid ${patternBusy === 'screenshot_ocr' ? 'fa-spinner animate-spin' : 'fa-camera'} text-[10px]`} />
                {patternBusy === 'screenshot_ocr' ? '识别中…' : '截图 OCR'}
              </button>
            )}
            {/* URL learn button — only when clipboard is a URL */}
            {/^https?:\/\/[^\s]+/.test(text.trim()) && (
              <button
                onClick={() => handleUrlLearn(text.trim())}
                disabled={!!patternBusy}
                className="flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg text-[11px] transition-all disabled:opacity-30"
                style={{
                  background: patternBusy === 'url_learn' ? 'rgba(168,85,247,0.25)' : 'rgba(168,85,247,0.15)',
                  color: '#a78bfa',
                }}
              >
                <Icon icon={`fa-solid ${patternBusy === 'url_learn' ? 'fa-spinner animate-spin' : 'fa-book-open'} text-[10px]`} />
                {patternBusy === 'url_learn' ? '学习中…' : '学习此链接'}
              </button>
            )}
            <div className="grid grid-cols-3 gap-1.5">
            {PATTERN_ACTIONS.map(a => {
              const needsRpc = RPC_ACTIONS.has(a.id)
              const isOffline = wsStatus !== 'connected' && needsRpc
              const isSuggested = suggestedAction === a.id && !patternBusy && !isOffline
              return (
              <button
                key={a.id}
                onClick={() => handleAction(a.id)}
                disabled={!!patternBusy || (wsStatus !== 'connected' && needsRpc && a.id !== 'word_count')}
                className="flex items-center justify-center gap-1.5 px-2 py-2 rounded-lg text-[11px] transition-all disabled:opacity-30"
                style={{
                  background: patternBusy === a.id
                    ? 'rgba(96, 165, 250, 0.25)'
                    : isSuggested
                      ? 'rgba(96, 165, 250, 0.12)'
                      : isOffline
                        ? 'rgba(255,255,255,0.015)'
                        : 'rgba(255,255,255,0.04)',
                  color: patternBusy === a.id
                    ? 'var(--brand)'
                    : isSuggested
                      ? '#93c5fd'
                      : isOffline
                        ? 'rgba(255,255,255,0.25)'
                        : 'rgba(255,255,255,0.7)',
                  border: isSuggested ? '1px solid rgba(96, 165, 250, 0.35)' : '1px solid transparent',
                  boxShadow: isSuggested ? '0 0 8px rgba(96, 165, 250, 0.15)' : 'none',
                }}
                title={(isSuggested ? '✨ 推荐 · ' : '') + (isOffline ? '需要后端连接' : a.label)}
                onMouseEnter={e => {
                  if (!patternBusy && !isOffline) (e.target as HTMLElement).style.background = 'rgba(255,255,255,0.08)'
                }}
                onMouseLeave={e => {
                  if (!patternBusy && !isOffline) (e.target as HTMLElement).style.background = 'rgba(255,255,255,0.04)'
                }}
              >
                <Icon icon={`fa-solid ${a.icon} text-xs`} />
                {a.label}
              </button>
            )})}
          </div>
            {wsStatus !== 'connected' && (
              <div className="flex items-center justify-center gap-1.5 text-[10px] mt-0.5" style={{ color: wsStatus === 'connecting' ? 'rgba(250,204,21,0.5)' : 'rgba(255,255,255,0.3)' }}>
                <Icon icon={`fa-solid ${wsStatus === 'connecting' ? 'fa-spinner fa-spin' : 'fa-plug'} text-[8px]`} />
                <span>{wsStatus === 'connecting' ? '正在连接后端…' : '后端未连接，仅字数统计和本地保存可用'}</span>
              </div>
            )}
          </div>
        )}
      </div>
      {/* Resize handle — bottom-right corner */}
      <div
        className="absolute"
        style={{ bottom: 0, right: 0, width: 16, height: 16, cursor: 'nwse-resize', zIndex: 10 }}
        onMouseDown={handleResizeStart}
      >
        <div style={{
          position: 'absolute', bottom: 3, right: 3,
          width: 8, height: 8,
          borderRight: '2px solid rgba(255,255,255,0.25)',
          borderBottom: '2px solid rgba(255,255,255,0.25)',
          pointerEvents: 'none',
        }} />
      </div>
    </div>
  )
}

export default ClipboardPopupApp
