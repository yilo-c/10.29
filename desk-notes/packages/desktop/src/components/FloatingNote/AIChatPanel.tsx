import React, { useState, useRef, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { getAIProvider } from '../../utils/ai'
import { useTranslation } from '../../i18n'
import { ModelSelector } from '../Modules/ModelSelector'
import { DEFAULT_LOCAL_API_URL, DEFAULT_LOCAL_MODEL, WsAIProvider, type ModelInfo } from '@desk-notes/shared'
import { swallowError } from '../../utils/swallowError'
import { buildSystemPrompt } from './AIChatSystemPrompt'

// ── Types ──

interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  action?: EditorAction | null
}

export interface EditorAction {
  action: 'replace_selection' | 'insert_at_cursor' | 'append' | 'prepend' | 'replace_full'
  text: string
}

interface AIChatPanelProps {
  noteId: string
  noteContent: string
  noteTitle: string
  onClose: () => void
  initialX?: number
  initialY?: number
  editorSelectedText?: string
  onApplyAction?: (action: EditorAction) => void
}

// ── Dimensions ──

const W = 360
const H = 460
const MIN_H = 320
const MAX_H = 620

// ── Parsing ──

/** Parse 【AI_ACTION】{json}【/AI_ACTION】marker from the end of AI response */
function parseActionMarker(rawText: string): { displayText: string; action: EditorAction | null } {
  const markerRegex = /【AI_ACTION】(.+?)【\/AI_ACTION】/;
  const match = rawText.match(markerRegex);
  if (match) {
    try {
      const action = JSON.parse(match[1]);
      if (typeof action === 'object' && action !== null && typeof action.action === 'string' && typeof action.text === 'string') {
        const displayText = rawText.replace(markerRegex, '').trim();
        return { displayText, action: { action: action.action, text: action.text } };
      }
    } catch (e) { /* invalid JSON, ignore */ swallowError('AIChatPanel:53', e) }
  }
  return { displayText: rawText, action: null };
}


// ── Component ──

const AIChatPanel: React.FC<AIChatPanelProps> = ({
  noteId: _noteId, noteContent, noteTitle, onClose, initialX, initialY,
  editorSelectedText, onApplyAction,
}) => {
  const aiEnabled = useStore(s => s.aiEnabled)
  const preferredModelProvider = useStore(s => s.preferredModelProvider)
  const preferredModelName = useStore(s => s.preferredModelName)
  const setPreferredModel = useStore(s => s.setPreferredModel)
  const { t } = useTranslation()

  // Initialize selectedModel from persistent default once on mount
  const modelInitRef = useRef(false)
  useEffect(() => {
    if (!modelInitRef.current && preferredModelProvider && preferredModelName) {
      setSelectedModel({ provider: preferredModelProvider, model: preferredModelName, api_base: '' })
      modelInitRef.current = true
    }
  }, [preferredModelProvider, preferredModelName])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [appliedSet, setAppliedSet] = useState<Set<number>>(new Set())
  const [pos, setPos] = useState(() => ({
    x: initialX ?? Math.max(16, (window.innerWidth - W) / 2),
    y: initialY ?? Math.max(16, (window.innerHeight - H) / 2),
  }))
  const [dragging, setDragging] = useState(false)
  const dragRef = useRef({ startX: 0, startY: 0, origX: 0, origY: 0 })
  const panelRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const lastUserInputRef = useRef('')
  const dragCleanupRef = useRef<(() => void) | null>(null)
  const resizeCleanupRef = useRef<(() => void) | null>(null)

  // Cleanup drag/resize listeners on unmount
  useEffect(() => {
    return () => {
      dragCleanupRef.current?.()
      resizeCleanupRef.current?.()
    }
  }, [])
  const [selectedModel, setSelectedModel] = useState<ModelInfo | null>(null)
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null)
  // deepMode 强制本次发送走 AI 网关内核（params.kernel=true，无内核时后端自动降级）
  const [deepMode, setDeepMode] = useState(false)

  const handleModelSelect = useCallback((model: ModelInfo | null) => {
    setSelectedModel(model)
    setPreferredModel(model?.provider ?? null, model?.model ?? null)
  }, [setPreferredModel])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    return () => { abortRef.current?.abort() }
  }, [])

  // Auto-resize textarea
  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 120) + 'px'
  }

  const handleAbort = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(false)
  }, [])

  const handleCopy = useCallback(async (text: string, idx: number) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopiedIdx(idx)
      setTimeout(() => setCopiedIdx(null), 1500)
    } catch (e) { /* ignore */ swallowError('AIChatPanel:143', e) }
  }, [])

  // ── Send ──

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || loading || !aiEnabled) return
    setInput('')

    const userMsg: ChatMessage = { role: 'user', content: text }
    const updatedMessages = [...messages, userMsg]
    setMessages(updatedMessages)
    lastUserInputRef.current = text

    setLoading(true)
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    // ── Build prompt with action instructions ──
    const prompt = buildSystemPrompt({
      userLabel: t('ai.chat.userLabel'),
      assistantLabel: t('ai.chat.assistantLabel'),
      emptyContent: t('ai.chat.emptyContent'),
      userSelected: t('ai.chat.userSelected'),
      noteContent,
      editorSelectedText,
      messages,
      lastUserInput: text,
    })

    setMessages(prev => [...prev, { role: 'assistant', content: '' }])

    try {
      const provider = getAIProvider({ enabled: true, apiUrl: DEFAULT_LOCAL_API_URL, model: DEFAULT_LOCAL_MODEL, protocol: 'ollama' as const, autoTagging: false, strategy: 'ws' as const })
      const effectiveModel = selectedModel ?? (preferredModelProvider && preferredModelName ? { provider: preferredModelProvider, model: preferredModelName, api_base: '' } : null)
      if (provider instanceof WsAIProvider && effectiveModel) {
        provider.preferredProvider = effectiveModel.provider
        provider.preferredModel = effectiveModel.model
      }

      // ── Deep kernel mode ──
      // 强制本次 chat.send 走 AI 网关内核（L1 路由 params.kernel 为真即网关）；
      // 无网关内核时后端自动降级到 butler。
      if (provider instanceof WsAIProvider && deepMode) {
        provider.extraParams = {
          ...(provider.extraParams || {}),
          kernel: true,
        }
      }

      await provider.processText(
        'chat',
        prompt,
        (chunk) => {
          // Strip action markers from streaming display
          const clean = chunk.replace(/【AI_ACTION】[\s\S]*?(【\/AI_ACTION】|$)/g, '').trim()
          setMessages(prev => {
            const copy = [...prev]
            copy[copy.length - 1] = { role: 'assistant', content: clean || chunk }
            return copy
          })
        },
        controller.signal,
      )

      // After streaming completes, parse action marker from the full response
      setMessages(prev => {
        const copy = [...prev]
        const last = copy[copy.length - 1]
        if (last && last.role === 'assistant' && last.content) {
          const parsed = parseActionMarker(last.content)
          copy[copy.length - 1] = { role: 'assistant', content: parsed.displayText, action: parsed.action }
        }
        return copy
      })
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') return
      setMessages(prev => {
        const copy = [...prev]
        const errMsg = err instanceof Error ? err.message : t('ai.askFailed')
        copy[copy.length - 1] = { role: 'assistant', content: `[${t('common.error')}] ${errMsg}` }
        return copy
      })
    } finally {
      setLoading(false)
      abortRef.current = null
    }
  }, [input, messages, loading, aiEnabled, noteContent, t, editorSelectedText, selectedModel, deepMode])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleApply = useCallback((idx: number, action: EditorAction) => {
    onApplyAction?.(action)
    setAppliedSet(prev => new Set([...prev, idx]))
  }, [onApplyAction])

  const handleRetry = useCallback(() => {
    const lastInput = lastUserInputRef.current
    if (!lastInput) return
    setInput(lastInput)
  }, [])

  // ── Drag ──

  const handleHeaderMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    dragRef.current = { startX: e.clientX, startY: e.clientY, origX: pos.x, origY: pos.y }
    setDragging(true)

    const onMove = (ev: MouseEvent) => {
      setPos({
        x: dragRef.current.origX + ev.clientX - dragRef.current.startX,
        y: dragRef.current.origY + ev.clientY - dragRef.current.startY,
      })
    }
    const onUp = () => {
      setDragging(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      dragCleanupRef.current = null
    }
    if (dragCleanupRef.current) dragCleanupRef.current()
    dragCleanupRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [pos])

  // ── Height resizing ──

  const resizerRef = useRef<{ startY: number; origH: number } | null>(null)
  const [resizing, setResizing] = useState(false)
  const [panelHeight, setPanelHeight] = useState(H)

  const handleResizeStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    resizerRef.current = { startY: e.clientY, origH: panelHeight }
    setResizing(true)

    const onMove = (ev: MouseEvent) => {
      if (!resizerRef.current) return
      const dh = ev.clientY - resizerRef.current.startY
      const h = Math.max(MIN_H, Math.min(MAX_H, resizerRef.current.origH + dh))
      setPanelHeight(h)
    }
    const onUp = () => {
      resizerRef.current = null
      setResizing(false)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      resizeCleanupRef.current = null
    }
    if (resizeCleanupRef.current) resizeCleanupRef.current()
    resizeCleanupRef.current = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [panelHeight])

  // ── Render ──

  return (
    <div
      ref={panelRef}
      className="fixed z-[99999] flex flex-col rounded-xl shadow-2xl border overflow-hidden"
      style={{
        width: W,
        maxWidth: 360,
        height: panelHeight,
        minHeight: MIN_H,
        maxHeight: MAX_H,
        left: pos.x,
        top: pos.y,
        background: 'var(--panel-bg-solid)',
        borderColor: 'var(--panel-border)',
      }}
      onMouseDown={e => e.stopPropagation()}
    >
      {/* ── Header (matches HeaderBar style) ── */}
      <div
        className="flex items-center justify-between px-3 h-9 flex-shrink-0 border-b select-none"
        style={{ borderColor: 'var(--separator)', cursor: dragging ? 'grabbing' : 'grab' }}
        onMouseDown={handleHeaderMouseDown}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Icon icon="fa-solid fa-comment-dots text-fluent-blue/70 text-xs flex-shrink-0" />
          <span className="text-xs font-medium whitespace-nowrap" style={{ color: 'var(--text-primary)' }}>
            {t('ai.chat.title')}
          </span>
          <span className="text-xs truncate max-w-[120px]" style={{ color: 'var(--text-muted)' }}>
            {noteTitle}
          </span>
        </div>

        {/* Model selector + Close */}
        <div className="flex items-center gap-0.5" style={{ WebkitAppRegion: 'no-drag' } as React.CSSProperties}>
          <ModelSelector selected={selectedModel} onSelect={handleModelSelect} disabled={loading} />

          <button
            onClick={onClose}
            className="w-6 h-6 flex items-center justify-center rounded-md text-text-secondary hover:text-text-secondary hover:bg-white/[0.06] transition-all text-xs"
          >
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>
      </div>

      {/* ── Messages ── */}
      <div className="flex-1 overflow-y-auto px-3 py-2.5 space-y-2 min-h-0 select-text">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full text-xs text-center px-4 select-none" style={{ color: 'var(--text-muted)' }}>
            {t('ai.chat.placeholder')}
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className="group relative max-w-[85%]">
              <div
                className={`rounded-xl px-3 py-1.5 text-xs leading-relaxed whitespace-pre-wrap break-words ${
                  msg.role === 'user'
                    ? 'bg-fluent-blue/15 text-text-primary rounded-br-md'
                    : 'bg-white/[0.04] text-text-secondary rounded-bl-md'
                }`}
              >
                {msg.content || (loading && i === messages.length - 1 ? (
                  <span className="inline-flex items-center gap-0.5">
                    <span className="w-1 h-1 bg-white/40 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                    <span className="w-1 h-1 bg-white/40 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                    <span className="w-1 h-1 bg-white/40 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                  </span>
                ) : null)}
              </div>
              {/* Copy button — appears on hover */}
              {msg.content && !(loading && i === messages.length - 1) && (
                <button
                  onClick={() => handleCopy(msg.content, i)}
                  className="absolute -top-1.5 -right-1.5 w-5 h-5 flex items-center justify-center rounded-full text-text-muted hover:text-white hover:bg-white/[0.1] transition-all opacity-0 group-hover:opacity-100"
                  title={t('note.copyContent')}
                >
                  <Icon icon={`fa-solid ${copiedIdx === i ? 'fa-check' : 'fa-copy'} text-[8px]`} />
                </button>
              )}
              {/* Apply button — for assistant messages with pending action */}
              {msg.role === 'assistant' && msg.action && onApplyAction && !appliedSet.has(i) && (
                <button
                  onClick={() => handleApply(i, msg.action!)}
                  className="mt-1 px-2 py-0.5 rounded text-xs text-fluent-blue/70 hover:text-fluent-blue hover:bg-fluent-blue/[0.08] transition-all"
                >
                  <Icon icon="fa-solid fa-arrow-right-to-bracket mr-1" />
                  {t('ai.resultPreview.replace')}
                </button>
              )}
              {msg.role === 'assistant' && msg.action && appliedSet.has(i) && (
                <span className="mt-1 text-2xs text-green-400/50 block">{t('ai.actionApplied')}</span>
              )}
              {msg.role === 'assistant' && msg.content?.startsWith(`[${t('common.error')}]`) && !loading && (
                <button
                  onClick={handleRetry}
                  className="mt-1 px-2 py-0.5 rounded text-xs text-amber-400/70 hover:text-amber-400 hover:bg-amber-400/[0.08] transition-all"
                >
                  <Icon icon="fa-solid fa-rotate mr-1" />
                  {t('common.retry')}
                </button>
              )}
            </div>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* ── Input bar ── */}
      <div className="flex-shrink-0 px-3 pb-3 pt-2 border-t" style={{ borderColor: 'var(--separator)' }}>
        <div className="flex items-end gap-2 rounded-xl border px-3 py-2 transition-all"
          style={{
            background: 'var(--input-bg)',
            borderColor: 'var(--panel-border)',
          }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder={t('ai.chat.inputPlaceholder')}
            rows={1}
            disabled={loading}
            className="flex-1 bg-transparent border-none outline-none resize-none text-xs leading-relaxed"
            style={{ color: 'var(--text-primary)', maxHeight: 120, opacity: loading ? 0.4 : 1 }}
          />
          <div className="flex items-center gap-1 flex-shrink-0">
            <button
              onClick={() => setDeepMode(v => !v)}
              className={`w-7 h-7 flex items-center justify-center rounded-lg transition-all text-xs ${
                deepMode
                  ? 'bg-indigo-500/20 text-indigo-300'
                  : 'text-text-dim hover:text-text-secondary hover:bg-white/[0.06]'
              }`}
              title={t(deepMode ? 'chatModule.deepModeOn' : 'chatModule.deepModeOff')}
              disabled={loading}
            >
              <Icon icon="fa-solid fa-brain" />
            </button>
            {loading ? (
              <button
                onClick={handleAbort}
                className="w-7 h-7 flex items-center justify-center rounded-lg bg-fluent-blue/15 text-fluent-blue hover:bg-fluent-blue/25 transition-all text-xs"
                title={t('common.cancel')}
              >
                <Icon icon="fa-solid fa-stop" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!input.trim() || !aiEnabled}
                className="w-7 h-7 flex items-center justify-center rounded-lg bg-fluent-blue/20 text-fluent-blue hover:bg-fluent-blue/30 disabled:opacity-30 transition-all text-xs"
                title={t('ai.chat.send')}
              >
                <Icon icon="fa-solid fa-arrow-up" />
              </button>
            )}
          </div>
        </div>
      </div>

      {/* ── Height resize handle ── */}
      <div
        onMouseDown={handleResizeStart}
        className="absolute bottom-0 left-0 right-0 h-2 cursor-s-resize z-10 flex items-center justify-center"
      >
        <div className={`w-8 h-0.5 rounded-full transition-all ${resizing ? 'bg-white/30' : 'opacity-0 group-hover:opacity-100'}`}
          style={{ background: resizing ? undefined : 'var(--text-dim)' }} />
      </div>
    </div>
  )
}

export default AIChatPanel
