import { useState, useRef, useEffect, useCallback } from 'react'
import Icon from '../Common/Icon'
import { getWsClient } from '../../utils/ai'
import { swallowError } from '../../utils/swallowError'
import { useTranslation } from '../../i18n'
import type { StreamChunkType } from '@desk-notes/shared'
import {
  CHAT_KERNEL_SELECTOR_OPTIONS,
  DEFAULT_KERNEL_SELECTION,
  KERNEL_AUTO,
  kernelDisplayLabel,
  type KernelSelectorValue,
} from '@desk-notes/shared/utils/kernelVocabulary'
import {
  collectAttachments,
  formatSize,
  toWireParam,
  makeTextAttachment,
  type PendingAttachment,
  type WireAttachment,
} from './chatAttachments'
import { useBackendEvent } from '../../FloatingBubble/useBackendEvents'


export interface ComposerModelOption {
  provider: string
  model: string
  label?: string
}

interface ChatComposerProps {
  /** Task #9：attachments 为本轮待发附件（wire 形态，无附件时 undefined） */
  onSend: (text: string, attachments?: WireAttachment[]) => void
  onStop: () => void
  streaming: boolean
  models: ComposerModelOption[]
  selection: { provider?: string | null; model?: string | null }
  onModelChange: (selection: { provider?: string; model?: string }) => void
  searchMode: boolean
  onToggleSearch: () => void
  multiAgentMode: boolean
  onToggleMultiAgent: () => void
  /** 当前会话最近消息（用于润色结合上下文；只读，不用于发送） */
  history?: Array<{ role: string; content: string }>
  /** 执行内核选择（auto 不指定 / dsh / claude_code / codex） */
  kernel?: string
  onKernelChange?: (kernel: string) => void
}

/** 内核词汇单一来源：kernel-contract §2.5.1（保留既有导出名以兼容引用方） */
export type ComposerKernelOption = KernelSelectorValue

export const COMPOSER_KERNEL_OPTIONS = CHAT_KERNEL_SELECTOR_OPTIONS

const POLISH_PROMPT = (text: string) =>
  `请把下面这段内容润色为专业、正式的表达，结合当前对话语境，保持原意，不要添加原文没有的信息，直接输出润色结果，不要任何解释或前缀：\n\n${text}`

function isEventType(t: StreamChunkType): boolean {
  return t !== 'chunk' && t !== 'done' && t !== 'error'
}

export function ChatComposer({ onSend, onStop, streaming, models, selection, onModelChange, searchMode, onToggleSearch, multiAgentMode, onToggleMultiAgent, history, kernel = DEFAULT_KERNEL_SELECTION, onKernelChange }: ChatComposerProps) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const [modelOpen, setModelOpen] = useState(false)
  const [kernelOpen, setKernelOpen] = useState(false)
  const [polishing, setPolishing] = useState(false)
  // Task #9 对话附件：待发附件条 + 拒绝原因（一次性提示，不静默丢）
  const [attachments, setAttachments] = useState<PendingAttachment[]>([])
  const [attachNotice, setAttachNotice] = useState<string>('')
  // 第二档 #4：后端 clipboard.changed 推送 → 可点击"附为上下文"提示；seq 去重避免重复弹出
  const [clipboardHint, setClipboardHint] = useState<{ text: string; seq: number } | null>(null)
  const lastClipSeqRef = useRef(0)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const modelRef = useRef<HTMLDivElement>(null)
  const kernelRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const polishAbortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!modelOpen) return
    const handler = (e: MouseEvent) => {
      if (modelRef.current && !modelRef.current.contains(e.target as Node)) setModelOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [modelOpen])

  useEffect(() => {
    if (!kernelOpen) return
    const handler = (e: MouseEvent) => {
      if (kernelRef.current && !kernelRef.current.contains(e.target as Node)) setKernelOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [kernelOpen])

  // 组件卸载时中止未完成的润色流
  useEffect(() => {
    return () => polishAbortRef.current?.abort()
  }, [])

  /* B2（2026-09-01 速记栏升级）：监听速记"以此开聊"事件，注入为输入框草稿并聚焦。
     不自动发送——用户可继续编辑后再回车。 */
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<string>).detail
      if (typeof detail !== 'string' || !detail.trim()) return
      setValue(detail.trim())
      const ta = composerRef.current
      if (ta) {
        ta.focus()
        ta.style.height = 'auto'
        ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
      }
    }
    window.addEventListener('siyi:chat-draft', handler)
    return () => window.removeEventListener('siyi:chat-draft', handler)
  }, [])

  // 第二档 #4：消费后端 server-push 的 clipboard.changed（EventBridge 已广播）。
  // 出现"新"复制文本（seq 比上次见过的大）且非空，弹出"附为上下文"提示。
  const clipboardEvent = useBackendEvent('clipboard.changed')
  useEffect(() => {
    if (clipboardEvent.seq <= 0) return
    if (clipboardEvent.seq <= lastClipSeqRef.current) return
    lastClipSeqRef.current = clipboardEvent.seq
    const text = String((clipboardEvent.payload as Record<string, unknown> | null)?.text ?? '').trim()
    if (text.length < 3) return
    setClipboardHint({ text, seq: clipboardEvent.seq })
  }, [clipboardEvent.seq])

  // 把提示里的剪贴板文本作为文本附件追加（复用 Task #9 附件管线 → 后端识别为正文）
  const attachClipboardAsContext = useCallback(() => {
    if (!clipboardHint) return
    setAttachments((prev) => [...prev, makeTextAttachment(clipboardHint.text)])
    setClipboardHint(null)
  }, [clipboardHint])

  const addFiles = async (files: File[]) => {
    if (!files.length) return
    const { accepted, rejected } = await collectAttachments(files, attachments)
    if (accepted.length) setAttachments((prev) => [...prev, ...accepted])
    if (rejected.length) setAttachNotice(rejected.map((r) => `${r.name}：${r.reason}`).join('；'))
    else setAttachNotice('')
  }

  const removeAttachment = (id: string) => setAttachments((prev) => prev.filter((a) => a.id !== id))

  const submit = () => {
    const text = value.trim()
    // 后端 chat_send 拒空 message（"消息不能为空"）→ 前端同口径：文本必带，附件只是加持
    if (!text || streaming || polishing) return
    const wire = toWireParam(attachments)
    setValue('')
    setAttachments([])
    setAttachNotice('')
    onSend(text, wire)
  }

  const handlePolish = async () => {
    const text = value.trim()
    if (!text || polishing) return
    setPolishing(true)
    const controller = new AbortController()
    polishAbortRef.current = controller
    let acc = ''
    try {
      const ws = getWsClient()
      if (!ws || controller.signal.aborted) throw new Error('backend_not_connected')
      const contextMessages = (history ?? [])
        .slice(-12)
        .filter((m) => m.content && m.content.trim())
        .map((m) => ({ role: m.role, content: m.content }))
      for await (const chunk of ws.streamTyped('chat.send', {
        message: POLISH_PROMPT(text),
        context_messages: contextMessages,
        stream: true,
      })) {
        if (controller.signal.aborted) break
        if (chunk.type === 'chunk' && chunk.data) {
          acc += chunk.data
          setValue(acc)
        } else if (isEventType(chunk.type)) {
          // 工具/计划等事件在润色场景下忽略
        }
      }
      // 流结束后保留结果（用户可继续编辑或直接发送）
    } catch (err) {
      if (!(err instanceof DOMException && err.name === 'AbortError')) {
        swallowError('ChatComposer:polish', err)
        // 保留已生成的部分，不丢用户原文
        if (!acc) setValue(text)
      }
    } finally {
      setPolishing(false)
      polishAbortRef.current = null
    }
  }

  const selectedLabel = selection.provider && selection.model
    ? `${selection.model}`
    : '自动'

  const grouped = models.reduce<Record<string, ComposerModelOption[]>>((acc, m) => {
    (acc[m.provider] ??= []).push(m)
    return acc
  }, {})

  return (
    <div
      className="flex flex-col rounded-md border border-border bg-surface"
      onDragOver={(e) => { e.preventDefault() }}
      onDrop={(e) => {
        e.preventDefault()
        const files = Array.from(e.dataTransfer?.files || [])
        if (files.length) void addFiles(files)
      }}
    >
      {/* Task #9 待发送附件 chips + 拒绝提示 */}
      {(attachments.length > 0 || attachNotice) && (
        <div className="px-3 pt-2 flex flex-col gap-1" data-testid="attachment-strip">
          {attachments.map((a) => (
            <span key={a.id} className="inline-flex items-center gap-1.5 self-start rounded-full border border-border bg-hover-bg px-2 py-0.5 text-2xs text-text-secondary max-w-full">
              <Icon icon={a.isImage ? 'fa-solid fa-image' : 'fa-solid fa-file-lines'} />
              <span className="truncate max-w-[160px]">{a.name}</span>
              <span className="text-text-muted">{formatSize(a.sizeBytes)}</span>
              <button type="button" onClick={() => removeAttachment(a.id)} aria-label={`移除附件 ${a.name}`} className="text-text-muted hover:text-danger">
                <Icon icon="fa-solid fa-xmark" />
              </button>
            </span>
          ))}
          {attachNotice && <span className="text-2xs text-danger" role="alert">{attachNotice}</span>}
        </div>
      )}
      {/* 第二档 #4：复制即候选上下文 —— 新复制文本可一键附为上下文（比弹卡片等点击进一格） */}
      {clipboardHint && (
        <div data-testid="clipboard-hint" className="px-3 pt-2 flex items-center gap-1.5 self-start rounded-full border border-border bg-hover-bg max-w-full">
          <Icon icon="fa-solid fa-clipboard" />
          <span className="truncate max-w-[200px] text-2xs text-text-secondary">{clipboardHint.text.slice(0, 60)}</span>
          <button
            type="button"
            onClick={attachClipboardAsContext}
            className="text-2xs text-brand hover:underline"
          >
            附为上下文
          </button>
          <button
            type="button"
            onClick={() => setClipboardHint(null)}
            aria-label="忽略剪贴板提示"
            className="text-text-muted hover:text-text-secondary"
          >
            <Icon icon="fa-solid fa-xmark" />
          </button>
        </div>
      )}
      {/* Input row: textarea + send */}
      <div className="flex items-end gap-2 px-3 py-2">
        <textarea
          ref={composerRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() } }}
          onPaste={(event) => {
            // Task #9：粘贴文件（截图/资源管理器复制的文件）→ 附件；纯文本照常粘贴
            const files = Array.from(event.clipboardData?.files || [])
            if (files.length) {
              event.preventDefault()
              void addFiles(files)
            }
          }}
          placeholder={t('ux.composerSendHint')}
          rows={1}
          className="flex-1 resize-none bg-transparent px-1 py-2 text-sm text-text-primary outline-none placeholder:text-text-muted min-h-[36px] max-h-[160px]"
          aria-label="聊天输入框"
          onInput={(e) => {
            const t = e.currentTarget
            t.style.height = 'auto'
            t.style.height = Math.min(t.scrollHeight, 160) + 'px'
          }}
        />
        {streaming || polishing ? (
          <button onClick={() => { if (polishing) { polishAbortRef.current?.abort(); setPolishing(false) } else { onStop() } }} className="rounded-md bg-danger-soft px-4 py-2 text-xs text-danger flex-shrink-0" aria-label={polishing ? '停止润色' : '停止生成'}>
            <Icon icon="fa-solid fa-stop mr-1" />{polishing ? '停止' : '停止'}
          </button>
        ) : (
          <button onClick={submit} className="w-9 h-9 flex items-center justify-center rounded-full bg-brand text-white flex-shrink-0 hover:bg-brand/80 transition-colors" aria-label="发送消息" disabled={!value.trim()}>
            <Icon icon="fa-solid fa-arrow-up text-sm" />
          </button>
        )}
      </div>

      {/* Toolbar row: model selector + feature toggles */}
      <div className="flex items-center gap-1 px-3 pb-2">
        {/* Task #9 附件按钮（文本类/图片；与后端守卫同口径） */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          data-testid="attachment-file-input"
          onChange={(e) => {
            const files = Array.from(e.target.files || [])
            e.target.value = ''
            if (files.length) void addFiles(files)
          }}
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="flex items-center gap-1 px-2 py-1 rounded-lg text-2xs text-text-muted hover:text-text-secondary hover:bg-hover-bg transition-colors"
          title="添加附件（文本/图片；也可粘贴或拖拽文件）"
          aria-label="添加附件"
        >
          <Icon icon="fa-solid fa-paperclip" />
        </button>

        {/* Model selector */}
        <div className="relative" ref={modelRef}>
          <button
            type="button"
            onClick={() => setModelOpen(!modelOpen)}
            className="flex items-center gap-1 px-2 py-1 rounded-lg text-2xs text-text-muted hover:text-text-secondary hover:bg-hover-bg transition-colors"
          >
            <Icon icon="fa-solid fa-microchip text-2xs" />
            <span className="max-w-[80px] truncate">{selectedLabel}</span>
            <Icon icon="fa-solid fa-chevron-down text-2xs" />
          </button>
          {modelOpen && (
            <div className="absolute bottom-full left-0 mb-1 z-50 bg-surface border border-border rounded-lg shadow-lg min-w-[200px] max-h-[300px] overflow-y-auto py-1">
              <button type="button" onClick={() => { onModelChange({}); setModelOpen(false) }}
                className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-hover-bg ${!selection.provider ? 'text-brand bg-brand-soft' : 'text-text-secondary'}`}>
                {!selection.provider ? <Icon icon="fa-solid fa-check text-2xs w-3" /> : <span className="w-3" />}
                自动选择
              </button>
              <div className="mx-3 my-1 border-t border-border" />
              {Object.entries(grouped).map(([provider, providerModels]) => (
                <div key={provider}>
                  <div className="px-3 py-1 text-2xs font-semibold uppercase tracking-wider text-text-muted">{provider}</div>
                  {providerModels.map((m) => {
                    const isSelected = selection.provider === m.provider && selection.model === m.model
                    return (
                      <button type="button" key={`${m.provider}:${m.model}`}
                        onClick={() => { onModelChange({ provider: m.provider, model: m.model }); setModelOpen(false) }}
                        className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-hover-bg ${isSelected ? 'text-brand bg-brand-soft' : 'text-text-secondary'}`}>
                        {isSelected ? <Icon icon="fa-solid fa-check text-2xs w-3" /> : <span className="w-3" />}
                        <span className="truncate">{m.label ?? m.model}</span>
                      </button>
                    )
                  })}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Kernel selector */}
        <div className="relative" ref={kernelRef}>
          <button
            type="button"
            onClick={() => setKernelOpen(!kernelOpen)}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg text-2xs transition-colors ${kernel !== KERNEL_AUTO ? 'bg-brand-soft text-brand' : 'text-text-muted hover:text-text-secondary hover:bg-hover-bg'}`}
            title={t('chat.kernelSelector')}
          >
            <Icon icon="fa-solid fa-terminal text-2xs" />
            <span className="max-w-[80px] truncate">{COMPOSER_KERNEL_OPTIONS.find((o) => o.value === kernel)?.label ?? kernelDisplayLabel(DEFAULT_KERNEL_SELECTION)}</span>
            <Icon icon="fa-solid fa-chevron-down text-2xs" />
          </button>
          {kernelOpen && (
            <div className="absolute bottom-full left-0 mb-1 z-50 bg-surface border border-border rounded-lg shadow-lg min-w-[180px] py-1">
              {COMPOSER_KERNEL_OPTIONS.map((option) => {
                const isSelected = kernel === option.value
                return (
                  <button type="button" key={option.value}
                    onClick={() => { onKernelChange?.(option.value); setKernelOpen(false) }}
                    className={`w-full flex items-center gap-2 px-3 py-2 text-xs transition-colors hover:bg-hover-bg ${isSelected ? 'text-brand bg-brand-soft' : 'text-text-secondary'}`}>
                    {isSelected ? <Icon icon="fa-solid fa-check text-2xs w-3" /> : <span className="w-3" />}
                    <span className="truncate">{option.label}</span>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Polish button */}
        <button type="button" onClick={() => void handlePolish()}
          disabled={!value.trim() || polishing || streaming}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-2xs transition-colors ${polishing ? 'bg-brand-soft text-brand' : 'text-text-muted hover:text-text-secondary hover:bg-hover-bg'} disabled:opacity-40 disabled:cursor-not-allowed`}
          title={t('chat.polishButton')}>
          <Icon icon={`fa-solid fa-wand-magic-sparkles ${polishing ? 'animate-pulse' : ''}`} /> {polishing ? t('chat.polishing') : t('chat.polishButton')}
        </button>

        {/* Feature toggles */}
        <button type="button" onClick={onToggleSearch}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-2xs transition-colors ${searchMode ? 'bg-brand-soft text-brand' : 'text-text-muted hover:text-text-secondary hover:bg-hover-bg'}`}
          title={searchMode ? '关闭联网搜索' : '联网搜索'}>
          <Icon icon="fa-solid fa-globe" /> 联网
        </button>
        <button type="button" onClick={onToggleMultiAgent}
          className={`flex items-center gap-1 px-2 py-1 rounded-lg text-2xs transition-colors ${multiAgentMode ? 'bg-brand-soft text-brand' : 'text-text-muted hover:text-text-secondary hover:bg-hover-bg'}`}
          title={multiAgentMode ? '关闭多Agent协作' : '多Agent协作'}>
          <Icon icon="fa-solid fa-robot" /> Agent
        </button>
      </div>
    </div>
  )
}
