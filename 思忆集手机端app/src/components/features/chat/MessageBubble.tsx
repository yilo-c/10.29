/**
 * 聊天气泡组件 / Chat message bubble
 *
 * 为什么单独抽出来并加 memo：
 *
 * 原实现把气泡渲染内联在 ChatView 的 `messages.map(...)` 里，
 * 且每次渲染都对**每一条**消息跑 4 个正则：
 *   1. 图片 data URL 匹配   4. stripAgentBlocks（2 个全局正则）
 *   2. 文件 data URL 匹配
 *   3. parseAgentSteps / parseAgentConfirm（含 /[\s\S]*?/ 惰性匹配）
 *
 * 而流式输出时 onChunk 每个 chunk 都会 set 整个 messages 数组
 * （mobileStore 里 `messages.map(m => ...m, content: m.content + chunk)`），
 * 于是 50 条历史消息 × 每秒几十个 chunk = 每秒数百次全量正则 + 全部气泡重渲染。
 * 表现为长会话打字卡顿、手机发烫、滚动掉帧。
 *
 * 修法：
 *   - 把气泡做成 memo 组件：只要 msg 对象引用没变就不重渲染；
 *   - 正则解析结果用 useMemo 按 (id, content) 缓存；
 *   - 流式时只有「正在变化的那一条」引用会变，其余历史气泡全部跳过。
 *
 * 注意：这里刻意用 `content` 参与 memo 比较，而不是只比 id ——
 * 流式中同一条消息的 content 在增长，必须让它重渲染。
 */
import React, { useMemo, useState } from 'react'
import AgentStepBubble, { type AgentStep } from './AgentStepBubble'
import AgentConfirmBubble from './AgentConfirmBubble'
import { swallowError } from '../../../lib/swallowError'
import { listPendingApprovals, approveApproval, rejectApproval } from '../../../lib/rpcClient'

export interface ChatMessageLike {
  id: string
  role: string
  content: string
}

/* ── 解析工具：与原 ChatView 内的实现保持一致 ── */

const IMG_RE = /!\[([^\]]*)\]\((data:image\/[^)]+)\)/
const FILE_RE = /\[([^\]]*)\]\((data:[^)]+)\)/

interface Parsed {
  imgMatch: RegExpMatchArray | null
  fileMatch: RegExpMatchArray | null
  textOnly: string
  agentSteps: ReturnType<typeof parseAgentSteps>
  agentConfirm: ReturnType<typeof parseAgentConfirm>
}

function parseAgentSteps(content: string): AgentStep[] {
  const match = content.match(/```agent-steps\n([\s\S]*?)\n```/)
  if (!match) return []
  const VALID = ['running', 'completed', 'failed', 'pending'] as const
  type Status = typeof VALID[number]
  try {
    const parsed = JSON.parse(match[1]) as unknown
    if (!Array.isArray(parsed)) return []
    // 与原 ChatView 内部实现保持一致：逐项校验字段，脏数据直接丢弃
    return parsed
      .filter((item): item is { icon: string; title: string; detail?: string; status: Status } => {
        if (!item || typeof item !== 'object') return false
        const v = item as Record<string, unknown>
        return typeof v.icon === 'string'
          && typeof v.title === 'string'
          && (VALID as readonly string[]).includes(String(v.status))
      })
      .map(item => ({
        icon: item.icon,
        title: item.title,
        detail: item.detail,
        status: item.status,
      }))
  } catch {
    return []
  }
}

function parseAgentConfirm(content: string) {
  const match = content.match(/```agent-confirm\n([\s\S]*?)\n```/)
  if (!match) return null
  try {
    const parsed = JSON.parse(match[1]) as Record<string, unknown>
    if (typeof parsed.title !== 'string') return null
    return {
      title: parsed.title,
      detail: typeof parsed.detail === 'string' ? parsed.detail : undefined,
    }
  } catch {
    return null
  }
}

function stripAgentBlocks(content: string) {
  return content
    .replace(/```agent-steps\n[\s\S]*?\n```/g, '')
    .replace(/```agent-confirm\n[\s\S]*?\n```/g, '')
    .trim()
}

function parseMessage(msg: ChatMessageLike): Parsed {
  const imgMatch = msg.content.match(IMG_RE)
  const fileMatch = !imgMatch ? msg.content.match(FILE_RE) : null
  const rawText = imgMatch
    ? msg.content.replace(IMG_RE, '').trim()
    : fileMatch
      ? msg.content.replace(FILE_RE, '').trim()
      : msg.content

  const isAssistant = msg.role === 'assistant'
  return {
    imgMatch,
    fileMatch,
    textOnly: isAssistant ? stripAgentBlocks(rawText) : rawText,
    agentSteps: isAssistant ? parseAgentSteps(rawText) : [],
    agentConfirm: isAssistant ? parseAgentConfirm(rawText) : null,
  }
}

/* ── 组件 ── */

interface Props {
  msg: ChatMessageLike
  streaming: boolean
  onStatus: (s: string) => void
}

/**
 * memo 比较：content 变（流式追加）或 role 变才重渲染。
 * 不比较函数 prop（onStatus 每次渲染都是新箭头函数，比了等于没 memo），
 * 因为它只用于设置状态提示，闭包旧值无害。
 */
function areEqual(prev: Props, next: Props) {
  return prev.msg.id === next.msg.id
    && prev.msg.content === next.msg.content
    && prev.msg.role === next.msg.role
    && prev.streaming === next.streaming
}

const MessageBubble = React.memo(function MessageBubble({ msg, streaming, onStatus }: Props) {
  const parsed = useMemo(() => parseMessage(msg), [msg.id, msg.content, msg.role])
  const [imgBroken, setImgBroken] = useState(false)

  const { imgMatch, fileMatch, textOnly, agentSteps, agentConfirm } = parsed
  const isUser = msg.role === 'user'

  return (
    <div style={{
      alignSelf: isUser ? 'flex-end' : 'flex-start',
      maxWidth: '80%',
      padding: imgMatch ? '6px 6px 10px 10px' : '10px 14px',
      borderRadius: 'var(--radius-lg)',
      background: isUser ? 'var(--accent)' : 'var(--bg-secondary)',
      color: isUser ? '#fff' : 'var(--text-primary)',
      fontSize: 'var(--text-footnote)',
      lineHeight: 1.5,
      whiteSpace: 'pre-wrap',
    }}>
      {!isUser && !msg.content && streaming ? (
        <>
          <span style={{ opacity: 0.5 }}>思考中</span>
          <span style={{ animation: 'ellipsis 1.2s infinite' }}>...</span>
        </>
      ) : (
        <>
          {imgMatch && !imgBroken && (
            <img
              src={imgMatch[2]}
              alt={imgMatch[1]}
              onError={() => setImgBroken(true)}
              style={{
                maxWidth: '100%', maxHeight: 200, borderRadius: 8,
                marginBottom: textOnly ? 6 : 0, objectFit: 'cover',
              }}
            />
          )}
          {/* 图片加载失败（如已被清理的临时文件）时降级为文件名，而不是留一个破图 */}
          {imgMatch && imgBroken && (
            <div style={{
              padding: '8px 12px', borderRadius: 8,
              background: 'rgba(255,255,255,0.08)', textAlign: 'center',
              marginBottom: textOnly ? 6 : 0, fontSize: 'var(--text-caption-1)',
            }}>
              🖼 {imgMatch[1] || '图片'}（无法显示）
            </div>
          )}
          {fileMatch && !imgMatch && (
            <div style={{
              padding: '8px 12px', borderRadius: 8,
              background: 'rgba(255,255,255,0.08)', textAlign: 'center',
              marginBottom: textOnly ? 6 : 0, fontSize: 'var(--text-caption-1)',
            }}>
              📎 {fileMatch[1]}
            </div>
          )}
          {agentSteps.length > 0 && <AgentStepBubble steps={agentSteps} />}
          {agentConfirm && (
            <AgentConfirmBubble
              title={agentConfirm.title}
              detail={agentConfirm.detail}
              onApprove={async () => {
                onStatus('正在申请执行…')
                try {
                  const ld = await listPendingApprovals()
                  if (Array.isArray(ld) && ld.length > 0) {
                    await approveApproval(ld[0].pending_id)
                    onStatus('已批准')
                  } else onStatus('已允许（无待审批项）')
                } catch (e) {
                  onStatus('批准失败: ' + (e instanceof Error ? e.message : ''))
                }
              }}
              onReject={async () => {
                try {
                  const ld = await listPendingApprovals()
                  if (Array.isArray(ld) && ld.length > 0) {
                    await rejectApproval(ld[0].pending_id)
                  }
                } catch (e) { swallowError('MessageBubble:reject', e) }
                onStatus('已取消该操作')
              }}
            />
          )}
          {textOnly && <>{textOnly}</>}
        </>
      )}
    </div>
  )
}, areEqual)

export default MessageBubble
