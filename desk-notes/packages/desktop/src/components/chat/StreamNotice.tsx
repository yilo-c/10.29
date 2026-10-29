import { useState } from 'react'
import Icon from '../Common/Icon'

/**
 * 聊天流 notice 分型渲染（M2.2 第1步 bug④）。
 *
 * 后端在 chat 流里用 `type=notice` 帧承载**结构化 JSON 状态事件**（不是正文 delta），
 * 目前四类：
 * - `chat_error`（分型错误，双字段）：`思忆集app/backend/handlers/chat_paths/helpers.py:469-477`
 *   `{event, error_kind, friendly, technical, status, transient, retry_after_seconds}`
 *   —— friendly 给用户看、technical 给排障（异常类名 + 截断消息，绝不含 traceback）。
 * - `chat_cancelled`：`helpers.py:347-362`（含已产出量）
 * - `kernel_degraded`：`_chat_memory.py:57-74`（显式要求内核却降级）
 * - `kernel_waiting` / 其它纯文本：`chat_paths/gateway_path.py`
 *
 * 此前前端只有一条「内核降级」灰字（`ChatView` 的 kernelNotice 布尔），
 * 分型错误的 friendly/technical 双字段全被吞掉——用户只看到「生成失败请重试」，
 * 无从知道是限流、欠费还是没配 Key。本模块补一个行内可操作显示：
 * 友好文案常驻、技术细节按需展开。
 */

export type NoticeKind = 'chat_error' | 'chat_cancelled' | 'kernel_degraded' | 'kernel_waiting' | 'other'

export interface StreamNoticeInfo {
  kind: NoticeKind
  /** 用户可见文案（chat_error 的 friendly / 其它事件的摘要）。 */
  friendly: string
  /** 技术细节（chat_error 的 technical），空串表示无可展开内容。 */
  technical: string
  errorKind: string
  transient: boolean
  /** 限流等场景后端给的建议等待秒数。 */
  retryAfterSeconds: number | null
  /** U1 等待状态收口：内核 notice 的阶段字段（starting/started/running/waiting），无则空串。 */
  phase: string
}

const FRIENDLY_BY_KIND: Record<string, string> = {
  rate_limit: '模型服务请求过于频繁（限流）',
  billing_error: '模型账户额度或余额不足',
  auth_error: '模型服务鉴权失败（API Key 无效或过期）',
  bad_request: '请求参数有误，模型无法处理',
  server_error: '模型服务暂时不可用',
  network_error: '网络连接异常，无法访问模型服务',
  timeout: '模型响应超时',
  content_moderation: '内容未通过安全审核',
  not_configured: 'AI 服务尚未配置（缺少 API Key）',
  unknown_error: '生成失败',
}

/** 解析一帧 notice 数据。非 JSON / 无 event 字段时降级为 other + 原文，绝不抛错。 */
export function parseStreamNotice(data: string): StreamNoticeInfo {
  const raw = (data ?? '').trim()
  const fallback: StreamNoticeInfo = {
    kind: 'other', friendly: raw, technical: '', errorKind: '', transient: false, retryAfterSeconds: null, phase: '',
  }
  if (!raw) return fallback
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return fallback
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return fallback
  const rec = parsed as Record<string, unknown>
  const event = String(rec.event ?? '')
  const technical = String(rec.technical ?? '')
  const phase = String(rec.phase ?? '')
  if (event === 'chat_error') {
    const errorKind = String(rec.error_kind ?? '')
    const friendly = String(rec.friendly ?? '') || FRIENDLY_BY_KIND[errorKind] || '生成失败，请重试'
    const retry = Number(rec.retry_after_seconds)
    return {
      kind: 'chat_error',
      friendly,
      technical,
      errorKind,
      transient: rec.transient === true,
      retryAfterSeconds: Number.isFinite(retry) && retry > 0 ? retry : null,
      phase,
    }
  }
  if (event === 'chat_cancelled') {
    const produced = Number(rec.chunks ?? rec.produced ?? 0)
    return {
      kind: 'chat_cancelled',
      friendly: produced > 0 ? `本轮已取消（已产出 ${produced} 段）` : '本轮已取消',
      technical: technical || raw,
      errorKind: '', transient: false, retryAfterSeconds: null,
      phase,
    }
  }
  if (event === 'kernel_degraded') {
    return {
      kind: 'kernel_degraded',
      friendly: String(rec.reason ? `内核暂不可用，已降级处理（${rec.reason}）` : '内核暂不可用，已降级处理'),
      technical: technical || raw,
      errorKind: '', transient: true, retryAfterSeconds: null,
      phase,
    }
  }
  if (event === 'kernel_waiting' || event === 'kernel_error') {
    return {
      kind: 'kernel_waiting',
      friendly: String(rec.friendly ?? rec.reason ?? raw),
      technical: technical || raw,
      errorKind: '', transient: true, retryAfterSeconds: null,
      phase: phase || 'waiting',
    }
  }
  return {
    kind: 'other',
    friendly: String(rec.friendly ?? rec.message ?? rec.reason ?? '') || raw,
    technical: technical || raw,
    errorKind: '', transient: false, retryAfterSeconds: null,
    phase,
  }
}

export interface ChatNoticeRowProps {
  notice: StreamNoticeInfo
  /** 仅对可重试（transient）的错误展示；不传则不出重试按钮。 */
  onRetry?: () => void
}

/** 技术细节折叠块（自带展开态）：空串时不渲染任何东西。 */
export function NoticeTechnical({ technical }: { technical: string }) {
  const [expanded, setExpanded] = useState(false)
  if (!technical) return null
  return (
    <div className="mt-1">
      <button
        type="button"
        data-testid="chat-notice-toggle"
        onClick={() => setExpanded((v) => !v)}
        className="text-2xs text-text-muted hover:text-text-secondary transition-colors"
      >
        {expanded ? '▾ 收起技术细节' : '▸ 展开技术细节'}
      </button>
      {expanded && (
        <pre
          data-testid="chat-notice-technical"
          className="mt-1 max-h-28 overflow-y-auto whitespace-pre-wrap break-all rounded bg-hover-bg p-2 text-2xs text-text-secondary"
        >
          {technical}
        </pre>
      )}
    </div>
  )
}

/** 行内可操作通知条：友好文案常驻 + 「展开技术细节」+ 瞬时错误的重试。 */
export function ChatNoticeRow({ notice, onRetry }: ChatNoticeRowProps) {
  const isError = notice.kind === 'chat_error'
  const tone = isError
    ? 'border-danger/40 text-danger'
    : notice.kind === 'chat_cancelled'
      ? 'border-border text-text-muted'
      : 'border-warning/40 text-warning'
  const icon = isError ? 'fa-solid fa-triangle-exclamation'
    : notice.kind === 'chat_cancelled' ? 'fa-solid fa-ban'
      : 'fa-solid fa-circle-info'
  return (
    <div
      data-testid="chat-notice-row"
      data-notice-kind={notice.kind}
      className={`max-w-[72%] rounded-md border bg-surface px-3 py-2 text-2xs ${tone}`}
    >
      <div className="flex items-start gap-2">
        <Icon icon={icon} />
        <span className="text-text-primary leading-relaxed">
          {notice.friendly}
          {notice.retryAfterSeconds ? <span className="ml-1 text-text-muted">（建议 {notice.retryAfterSeconds}s 后重试）</span> : null}
        </span>
      </div>
      <NoticeTechnical technical={notice.technical} />
      {isError && notice.transient && onRetry && (
        <button
          type="button"
          data-testid="chat-notice-retry"
          onClick={onRetry}
          className="mt-1.5 px-2 py-1 rounded-md text-2xs font-medium bg-brand text-white hover:opacity-90 transition-opacity"
        >
          重试上一条
        </button>
      )}
    </div>
  )
}
