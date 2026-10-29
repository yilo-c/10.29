import { useState, useRef, useEffect } from 'react'
import Icon from '../Common/Icon'

import type { ChatHistoryItem } from './useChatStream'
import { ArtifactRenderer } from './ArtifactRenderer'
import { AiAvatar } from './AiAvatar'
import type { PetMood } from '../../types/pet'
import { formatMessageTimestamp } from '@desk-notes/shared'

// C3：消息时间戳统一走 datetime 工具层（跨天消息补日期：昨天 HH:mm / MM-DD HH:mm）
function formatMessageTime(ts?: number): string {
  if (!ts) return ''
  return formatMessageTimestamp(ts)
}

const actionBtnCls = 'w-6 h-6 flex items-center justify-center rounded-md hover:bg-hover-bg text-text-muted hover:text-text-primary transition-colors text-2xs'

export interface ChatMessageProps {
  message: ChatHistoryItem
  streaming?: boolean
  /** 反馈（仅 AI 回复）：写入后端学习闭环 */
  onRate?: (rating: 'up' | 'down') => void
  /** 重新生成（仅最后一条 AI 回复传入） */
  onRegenerate?: () => void
  /** 编辑重发（仅用户消息传入） */
  onEdit?: (newText: string) => void
  /** 删除单条消息 */
  onDelete?: () => void
  /** AI 身份元素：当前模型徽章（如 dsh / claude / cod×…） */
  aiModel?: string
  /** AI 身份元素：当前内核标签 */
  aiKernel?: string
  /** AI 身份元素：执行上下文剥离后展示的进度耗时 */
  elapsedSec?: number
  /** AI 身份元素：小问头像 mood（流式 thinking / 工具调用 alert / 完成 done） */
  aiMood?: PetMood
}

export function ChatMessage({ message, streaming = false, onRate, onRegenerate, onEdit, onDelete, aiModel, aiKernel, elapsedSec, aiMood }: ChatMessageProps) {
  const [copied, setCopied] = useState(false)
  const [rating, setRating] = useState<'up' | 'down' | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(message.content)
  const timerRef = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => () => clearTimeout(timerRef.current), [])

  const copy = () => {
    const text = message.content
    const done = () => { setCopied(true); clearTimeout(timerRef.current); timerRef.current = setTimeout(() => setCopied(false), 1500) }
    const fail = () => { /* silent */ }

    // Try Electron clipboard first
    const ei = window.electronAPI as { clipboard?: { writeText?: (t: string) => void } } | undefined
    if (ei?.clipboard?.writeText) {
      ei.clipboard.writeText(text); done(); return
    }
    // Try Web Clipboard API
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(fail); return
    }
    // Fallback: execCommand
    try {
      const ta = document.createElement('textarea')
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'
      document.body.appendChild(ta); ta.select()
      document.execCommand('copy'); document.body.removeChild(ta)
      done()
    } catch { fail() }
  }

  const rate = (r: 'up' | 'down') => {
    if (rating) return
    setRating(r)
    onRate?.(r)
  }

  const timeLabel = formatMessageTime(message.timestamp)

  // ── 用户消息：编辑重发 + 删除 ──
  if (message.role === 'user') {
    if (editing) return (
      <div className="flex flex-col items-end gap-1.5">
        <textarea
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (draft.trim()) onEdit?.(draft.trim()) }
            if (e.key === 'Escape') { setDraft(message.content); setEditing(false) }
          }}
          rows={Math.min(6, Math.max(2, draft.split('\n').length))}
          className="w-full max-w-[72%] px-3 py-2 rounded-md bg-surface border border-brand/40 text-[15px] leading-[1.6] text-text-primary outline-none resize-y"
        />
        <div className="flex gap-1.5">
          <button
            onClick={() => { setDraft(message.content); setEditing(false) }}
            className="px-2.5 py-1 rounded-lg text-2xs text-text-muted hover:bg-hover-bg transition-colors"
          >取消</button>
          <button
            onClick={() => { if (draft.trim()) onEdit?.(draft.trim()) }}
            disabled={!draft.trim()}
            className="px-2.5 py-1 rounded-lg text-2xs font-medium bg-brand text-white hover:opacity-90 transition-opacity disabled:opacity-40"
          >保存并发送</button>
        </div>
      </div>
    )
    return (
      <div className="flex flex-col items-end gap-0.5 group group-user">
        <div className="max-w-[72%] px-4 py-2.5 rounded-md bg-brand text-white text-[15px] leading-[1.7] whitespace-pre-wrap">
          {message.content}
        </div>
        <div className="flex items-center gap-1 pr-1">
          {timeLabel && <span className="text-2xs text-text-muted">{timeLabel}</span>}
          <span className="flex items-center gap-0.5 opacity-0 group-hover/user:opacity-100 transition-opacity">
            {onEdit && (
              <button onClick={() => setEditing(true)} className={actionBtnCls} aria-label="编辑消息" title="编辑重发">
                <Icon icon="fa-solid fa-pen" />
              </button>
            )}
            {onDelete && (
              <button onClick={onDelete} className={`${actionBtnCls} hover:text-danger`} aria-label="删除消息" title="删除消息">
                <Icon icon="fa-regular fa-trash-can" />
              </button>
            )}
          </span>
        </div>
      </div>
    )
  }

  // ── AI 回复：复制 / 反馈 / 重新生成 / 删除 ──
  const showBadge = Boolean(aiModel || aiKernel || elapsedSec)
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center gap-1 flex-shrink-0 w-7">
        {/* AI 身份：小问 orb 表情头像（流式→thinking / 工具调用→alert / 完成→done）+ 内核状态灯 */}
        <AiAvatar mood={aiMood ?? (streaming ? 'thinking' : 'done')} streaming={streaming} />
        {showBadge && (
          <div className="flex flex-col items-center gap-0.5">
            {aiModel && <span className="text-[9px] px-1 py-0.5 rounded font-medium text-white/90 max-w-full truncate" style={{ background: 'var(--brand)', fontSize: 9 }} title={aiModel}>{aiModel}</span>}
            {aiKernel && <span className="text-[9px] px-1 py-0.5 rounded text-[var(--text-muted)]" style={{ fontSize: 9 }}>{aiKernel}</span>}
            {elapsedSec !== undefined && elapsedSec > 0 && <span className="text-[9px] tabular-nums text-[var(--text-muted)]" style={{ fontSize: 9 }}>{elapsedSec}s</span>}
          </div>
        )}
      </div>
      <div className="max-w-[72%] min-w-0 group group-ai">
        <div className="relative">
          <div className="rounded-md px-4 py-2.5 bg-surface border border-border text-[15px] leading-[1.7] chat-markdown">
            {streaming && <span className="inline-block w-2 h-4 bg-brand animate-pulse ml-0.5 align-middle" />}
            <ArtifactRenderer content={message.content} artifacts={message.artifacts} />
          </div>
          {!streaming && (
            <div className="absolute -top-2.5 right-2 flex items-center gap-0.5 px-1 py-0.5 rounded-lg bg-surface-elevated border border-border opacity-0 group-hover/ai:opacity-100 focus-within:opacity-100 transition-opacity">
              <button onClick={copy} aria-label="复制回复" title={copied ? '已复制' : '复制'} className={actionBtnCls}>
                <Icon icon={copied ? 'fa-solid fa-check' : 'fa-regular fa-copy'} />
              </button>
              {onRate && (
                <>
                  <button
                    onClick={() => rate('up')}
                    aria-label="回复有帮助"
                    title="有帮助"
                    className={`${actionBtnCls} ${rating === 'up' ? 'text-success' : ''}`}
                  >
                    <Icon icon={rating === 'up' ? 'fa-solid fa-thumbs-up' : 'fa-regular fa-thumbs-up'} />
                  </button>
                  <button
                    onClick={() => rate('down')}
                    aria-label="回复需改进"
                    title="需改进"
                    className={`${actionBtnCls} ${rating === 'down' ? 'text-danger' : ''}`}
                  >
                    <Icon icon={rating === 'down' ? 'fa-solid fa-thumbs-down' : 'fa-regular fa-thumbs-down'} />
                  </button>
                </>
              )}
              {onRegenerate && (
                <button onClick={onRegenerate} aria-label="重新生成" title="重新生成" className={actionBtnCls}>
                  <Icon icon="fa-solid fa-rotate-right" />
                </button>
              )}
              {onDelete && (
                <button onClick={onDelete} aria-label="删除消息" title="删除消息" className={`${actionBtnCls} hover:text-danger`}>
                  <Icon icon="fa-regular fa-trash-can" />
                </button>
              )}
            </div>
          )}
        </div>
        {timeLabel && !streaming && <div className="text-2xs text-text-muted mt-0.5 pl-1">{timeLabel}</div>}
      </div>
    </div>
  )
}
