import React, { useState, useRef, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'

import { useStore } from '../../store/useStore'
import { streamAI, getWsClient, AIAction, AI_ACTION_ICONS } from '../../utils/ai'
import { getLocalAIConfig } from '../../utils/aiConfig'
import { useTranslation } from '../../i18n'
import { errorStore } from '../../store/errorStore'

/** Maps frontend AIAction → backend pattern.execute action_id. */
const ACTION_PATTERN_MAP: Partial<Record<AIAction, string>> = {
  summarize: 'summarize',
  translate_zh: 'translate',
  translate_en: 'translate_en',
  polish: 'polish',
  continue: 'continue',
}

interface AIToolbarProps {
  x: number
  y: number
  visible: boolean
  selectedText: string
  /** 笔记标题（用于 AI 上下文） */
  noteTitle?: string
  /** 笔记全文（用于 AI 上下文） */
  noteContent?: string
  containerEl: HTMLElement | null
  onClose: () => void
  onReplace: (text: string, action: AIAction) => void
  onOpenChat?: () => void
}

const ACTIONS: AIAction[] = ['continue', 'summarize', 'translate_zh', 'translate_en', 'polish']

const AIToolbar: React.FC<AIToolbarProps> = ({ x, y, visible, selectedText, noteTitle, containerEl, onClose, onReplace, onOpenChat }) => {
  const aiEnabled = useStore(s => s.aiEnabled)
  const { t } = useTranslation()
  const [loading, setLoading] = useState<AIAction | null>(null)
  const [streamText, setStreamText] = useState('')
  const [error, setError] = useState('')
  const [completedResult, setCompletedResult] = useState<{ text: string; action: AIAction } | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const resultRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!visible || !barRef.current) return
    const bar = barRef.current
    const barW = bar.offsetWidth
    const container = containerEl || document.body
    const cRect = container.getBoundingClientRect()
    let adjustedX = x - barW / 2
    if (adjustedX + barW > cRect.right - 8) adjustedX = cRect.right - barW - 8
    if (adjustedX < cRect.left + 8) adjustedX = cRect.left + 8
    bar.style.left = `${adjustedX}px`
    bar.style.top = `${y - 44}px`
  }, [x, y, visible, containerEl])

  useEffect(() => {
    if (!visible) return
    const handler = (e: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(e.target as Node) &&
          resultRef.current && !resultRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    const timer = setTimeout(() => document.addEventListener('mousedown', handler), 0)
    return () => { clearTimeout(timer); document.removeEventListener('mousedown', handler) }
  }, [visible, onClose])

  // Cleanup abort on unmount
  useEffect(() => {
    return () => { abortRef.current?.abort() }
  }, [])

  const handleAbort = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
    setLoading(null)
    setStreamText('')
    setCompletedResult(null)
  }, [])

  const handleAction = useCallback(async (action: AIAction) => {
    if (!aiEnabled || !selectedText.trim()) return
    setLoading(action)
    setError('')
    setStreamText('')
    setCompletedResult(null)

    const controller = new AbortController()
    abortRef.current = controller

    // Build context-augmented text: include note title for better AI understanding
    const contextText = noteTitle
      ? `${t('aiToolbar.noteTitlePrefix', { title: noteTitle })}\n\n${selectedText}`
      : selectedText

    // Try backend RPC when connected
    const client = getWsClient()
    if (client.status === 'connected') {
      const patternId = ACTION_PATTERN_MAP[action]
      if (patternId) {
        try {
          const result = await client.call<string>('pattern.execute', {
            action_id: patternId,
            text: contextText,
          })
          // Show result via streaming display (single chunk)
          setStreamText(result)
          await new Promise(r => setTimeout(r, 50)) // let streaming ui render
          if (action === 'continue') {
            setLoading(null)
            abortRef.current = null
            onReplace(result, action)
          } else {
            setLoading(null)
            abortRef.current = null
            setCompletedResult({ text: result, action })
          }
          return
        } catch (err) {
          // RPC failed, fall through to local AI
          console.warn('[AIToolbar] pattern.execute failed, falling back to local AI:', err)
        }
      }
    }

    await streamAI(
      getLocalAIConfig(),
      action,
      contextText,
      (text) => setStreamText(text),
      (fullText) => {
        if (action === 'continue') {
          setLoading(null)
          abortRef.current = null
          onReplace(fullText, action)
        } else {
          setLoading(null)
          abortRef.current = null
          setCompletedResult({ text: fullText, action })
        }
      },
      (err) => {
        const friendly = /fetch\s*failed|failed\s*to\s*fetch|networkerror|ERR_CONNECTION_REFUSED|ECONNREFUSED/i.test(err)
          ? t('aiToolbar.serviceNotResponding')
          : err
        setError(friendly)
        setLoading(null)
        abortRef.current = null
        errorStore.error(t('aiToolbar.requestFailed', { err }))
      },
    )
  }, [selectedText, noteTitle, onReplace, t, aiEnabled])

  const handleReplaceResult = useCallback(() => {
    if (completedResult) {
      onReplace(completedResult.text, completedResult.action)
      setCompletedResult(null)
    }
  }, [completedResult, onReplace])

  const handleDismissResult = useCallback(() => {
    setCompletedResult(null)
  }, [])

  const actionLabelKey = (action: AIAction): string => {
    const map: Record<AIAction, string> = {
      continue: 'ai.toolbar.continue',
      summarize: 'ai.toolbar.summarize',
      translate_zh: 'ai.toolbar.translate_zh',
      translate_en: 'ai.toolbar.translate_en',
      polish: 'ai.toolbar.polish',
      organize: 'ai.toolbar.organize',
      chat: 'ai.toolbar.chat',
    }
    return map[action]
  }

  if (!visible) return null
  if (!aiEnabled) return null

  return (
    <>
      {/* Action buttons */}
      <div
        ref={barRef}
        className="fixed z-[9999] flex items-center gap-0.5 rounded-lg px-1.5 py-1 shadow-2xl backdrop-blur-xl border"
        style={{ background: 'var(--panel-bg-solid)', borderColor: 'var(--panel-border-accent)' }}
        onMouseDown={e => e.preventDefault()}
      >
        {ACTIONS.map(action => (
          <button
            key={action}
            onClick={() => handleAction(action)}
            disabled={loading !== null || !!completedResult}
            className={`w-7 h-7 flex items-center justify-center rounded text-xs transition-all
              ${loading === action ? 'text-fluent-blue/70 animate-pulse' : 'hover:bg-white/[0.06]'}
              disabled:opacity-40`}
            style={{ color: loading === action ? undefined : 'var(--text-secondary)' }}
            title={t(actionLabelKey(action))}
          >
            {loading === action ? (
              <Icon icon="fa-solid fa-spinner fa-spin" />
            ) : (
              <Icon icon={`fa-solid ${AI_ACTION_ICONS[action]}`} />
            )}
          </button>
        ))}

        {/* Separator + Chat button */}
        <div className="w-px h-4" style={{ background: 'var(--separator)' }} />
        <button
          onClick={onOpenChat}
          className="w-7 h-7 flex items-center justify-center rounded text-xs transition-all hover:bg-white/[0.06]"
          style={{ color: 'var(--text-secondary)' }}
          title={t('ai.chat.title')}
        >
          <Icon icon="fa-solid fa-comment-dots" />
        </button>
      </div>

      {/* Streaming result popup */}
      {loading && streamText && (
        <div
          ref={resultRef}
          className="fixed z-[9999] border rounded-xl px-3 py-2.5 shadow-2xl backdrop-blur-xl max-w-[320px] min-w-[200px]"
          style={{ background: 'var(--panel-bg-solid)', borderColor: 'var(--panel-border-accent)', left: Math.max(8, x - 160), top: y + 10 }}
        >
          <div className="flex items-start justify-between gap-2">
            <div className="text-xs leading-relaxed max-h-[120px] overflow-y-auto whitespace-pre-wrap break-words flex-1"
              style={{ color: 'var(--text-primary)' }}>
              {streamText}
              <span className="inline-block w-[2px] h-[14px] bg-fluent-blue/60 ml-0.5 animate-pulse" />
            </div>
            <button
              onClick={handleAbort}
              className="shrink-0 w-5 h-5 flex items-center justify-center rounded text-text-muted hover:text-white hover:bg-white/[0.08] transition-all"
              title={t('common.cancel')}
            >
              <Icon icon="fa-solid fa-xmark text-xs" />
            </button>
          </div>
        </div>
      )}

      {/* Completed result preview (for non-continue actions) */}
      {completedResult && !loading && (
        <div
          ref={resultRef}
          className="fixed z-[9999] rounded-xl px-3 py-2.5 shadow-2xl backdrop-blur-xl max-w-[320px] min-w-[200px] border"
          style={{
            background: 'var(--panel-bg-solid)',
            borderColor: 'rgba(96,165,250,0.25)',
            left: Math.max(8, x - 160),
            top: y + 10,
          }}
        >
          <div className="flex items-start justify-between gap-2 mb-1.5">
            <span className="text-xs font-medium text-fluent-blue/70 flex items-center gap-1">
              <Icon icon="fa-solid fa-check-circle text-2xs" />
              {t(actionLabelKey(completedResult.action))}
            </span>
            <button
              onClick={handleDismissResult}
              className="shrink-0 w-5 h-5 flex items-center justify-center rounded text-text-muted hover:text-white hover:bg-white/[0.08] transition-all"
            >
              <Icon icon="fa-solid fa-xmark text-xs" />
            </button>
          </div>
          <div className="text-xs leading-relaxed max-h-[120px] overflow-y-auto whitespace-pre-wrap break-words mb-2"
            style={{ color: 'var(--text-primary)' }}>
            {completedResult.text}
          </div>
          <div className="flex items-center gap-1.5 justify-end border-t pt-1.5"
            style={{ borderColor: 'var(--separator)' }}>
            <button
              onClick={handleDismissResult}
              className="px-2 py-1 rounded text-xs transition-all"
              style={{ color: 'var(--text-muted)' }}
            >
              {t('common.cancel')}
            </button>
            <button
              onClick={handleReplaceResult}
              className="px-2 py-1 rounded text-xs text-text-primary bg-fluent-blue/20 hover:bg-fluent-blue/30 transition-all"
            >
              {t('ai.resultPreview.replace')}
            </button>
          </div>
        </div>
      )}

      {/* Error display */}
      {error && !loading && !completedResult && (
        <div
          ref={resultRef}
          className="fixed z-[9999] rounded-xl px-3 py-2.5 shadow-2xl backdrop-blur-xl max-w-[260px] border"
          style={{ background: 'var(--panel-bg-solid)', borderColor: 'rgba(248,113,113,0.2)', left: x - 130, top: y + 10 }}
        >
          <div className="flex items-center gap-2">
            <Icon icon="fa-solid fa-circle-exclamation text-red-400/60 text-xs" />
            <span className="text-xs" style={{ color: 'rgba(248,113,113,0.7)' }}>{error}</span>
          </div>
        </div>
      )}
    </>
  )
}

export default AIToolbar
