import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { Keyboard, KeyboardInfo } from '@capacitor/keyboard'
import { useMobileStore } from '../../../store/mobileStore'
import { IconPlus, IconMenu, IconX } from '../../ui'
import { haptic } from '../../../lib/haptic'
import { tokenManager } from '../../../lib/tokenManager'
import { ChatSocketManager, type ConnectionState } from '../../../lib/chatSocket'
import PendingApprovals from './PendingApprovals'
import AgentStepBubble, { type AgentStep } from './AgentStepBubble'
import AgentConfirmBubble from './AgentConfirmBubble'
import { swallowError } from '../../../lib/swallowError'
import { translateText as bridgeTranslate } from '../../../lib/bridges'
import { listPendingApprovals, approveApproval, rejectApproval } from '../../../lib/rpcClient'
import { saveChatMessages, ingestConversation } from '../../../lib/bridges/aiBridge'
import { ChatOrchestrator } from '../../../lib/chatOrchestrator'
import { ToolRegistry } from '../../../lib/toolRegistry'
import { OfflineAiFallback } from '../../../lib/offlineFallback'

function parseAgentSteps(content: string): AgentStep[] {
  const match = content.match(/```agent-steps\n([\s\S]*?)\n```/)
  if (!match) return []
  try {
    const parsed = JSON.parse(match[1]) as unknown
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((item): item is AgentStep => {
        if (!item || typeof item !== 'object') return false
        const v = item as Record<string, unknown>
        return typeof v.icon === 'string'
          && typeof v.title === 'string'
          && ['running', 'completed', 'failed', 'pending'].includes(String(v.status))
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

function stripAgentBlocks(content: string): string {
  return content
    .replace(/```agent-steps\n[\s\S]*?\n```/g, '')
    .replace(/```agent-confirm\n[\s\S]*?\n```/g, '')
    .trim()
}

function parseAgentConfirm(content: string): { title: string; detail?: string } | null {
  const match = content.match(/```agent-confirm\n([\s\S]*?)\n```/)
  if (!match) return null
  try {
    const parsed = JSON.parse(match[1]) as Record<string, unknown>
    if (typeof parsed.title !== 'string') return null
    return { title: parsed.title, detail: typeof parsed.detail === 'string' ? parsed.detail : undefined }
  } catch {
    return null
  }
}

/** Read a file as base64 data URL */
function readFileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (file.size > 10 * 1024 * 1024) { reject(new Error('文件不能超过 10MB')); return }
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(new Error('读取文件失败'))
    reader.readAsDataURL(file)
  })
}

export default function ChatView() {
  /* 历史会话面板 */
  const [showHistory, setShowHistory] = useState(false)
  const [sessions, setSessions] = useState<Array<{ id: string; title: string; updatedAt: number; messages: unknown[] }>>([])
  const sendMessage = useMobileStore(s => s.sendMessage)
  const setConnection = useMobileStore(s => s.setConnection)
  const setStreaming = useMobileStore(s => s.setStreaming)
  const messages = useMobileStore(s => s.chat?.messages ?? [])
  const streaming = useMobileStore(s => s.chat?.streaming ?? false)
  const sessionId = useMobileStore(s => s.chat?.sessionId ?? null)
  const connState = useMobileStore(s => s.connection)
  const isPaired = useMobileStore(s => s.pairing.isPaired)
  const socketRef = useRef<ChatSocketManager | null>(null)
  if (!socketRef.current) socketRef.current = new ChatSocketManager()
  const socket = socketRef.current
  const [input, setInput] = useState(() => {
    try { return sessionStorage.getItem('siyi-chat-draft') || '' }
    catch { return '' }
  })
  const [pickedFile, setPickedFile] = useState<File | null>(null)
  const [fileDataUrl, setFileDataUrl] = useState<string | null>(null)
  const [showAttachment, setShowAttachment] = useState(false)
  const [status, setStatus] = useState('')

  const [translateResult, setTranslateResult] = useState<string | null>(null)
  // H11: translateTarget 从 useState 取，让翻译按钮可切换中/英方向。
  // 历史 bug: 写死 'zh'，注释自承"setTranslateTarget unused"，按钮形同虚设。
  const [translateTarget, setTranslateTarget] = useState<'zh' | 'en'>('zh')
  const listRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  /* 隐私判定用的单例编排器（inspect 无副作用，不需要真实依赖） */
  const orchestratorRef = useRef(new ChatOrchestrator(new ToolRegistry(), new OfflineAiFallback()))

  /**
   * 实时隐私判定：用户打字时就告诉他这段内容是否会上传。
   * 复用 orchestrator 的判定逻辑（inspect 不执行、无副作用），
   * 避免 UI 与真实路由各写一套规则而漂移。
   */
  const privacyHint = useMemo(
    () => orchestratorRef.current.inspect(input || ''),
    [input],
  )

  // ── Keyboard avoidance (Capacitor + VisualViewport) ──
  useEffect(() => {
    // H12: Capacitor.Keyboard.addListener 返回 Promise<PluginListenerHandle>。
    // 历史 bug：cleanup 阶段 handles 数组里只有"已 resolve 的句柄"；如果用户
    // 先离开页面再被 Promise resolve（StrictMode mount/unmount 来回切），
    // 句柄会 push 到一个**已经被 cleanup 处理过**的数组，永远不会被 remove，
    // 形成幽灵监听 → 切回时 padding 跳变。
    //
    // 修复：用 cancelled 标记 + remove() 在 promise resolution 内自检。
    const handles: { remove: () => void }[] = []
    let cancelled = false
    const trackHandle = (h: { remove: () => void }) => {
      if (cancelled) {
        // 已 unmount → 立即解绑，不要塞进数组
        try { h.remove() } catch (e) { /* ignore */ swallowError('ChatView:125', e) }
      } else {
        handles.push(h)
      }
    }

    Keyboard.addListener('keyboardWillShow', (info: KeyboardInfo) => {
      if (rootRef.current) {
        rootRef.current.style.paddingBottom = Math.round(info.keyboardHeight * 0.85) + 'px'
      }
    }).then(trackHandle).catch(() => { /* plugin unavailable on web */ })
    Keyboard.addListener('keyboardWillHide', () => {
      if (rootRef.current) rootRef.current.style.paddingBottom = '0px'
    }).then(trackHandle).catch(() => { /* plugin unavailable on web */ })

    // VisualViewport fallback for all platforms
    const onResize = () => {
      if (!rootRef.current) return
      const vv = window.visualViewport
      if (!vv) return
      const diff = window.innerHeight - vv.height
      if (diff > 100) {
        rootRef.current.style.paddingBottom = Math.round(diff * 0.85) + 'px'
      } else {
        rootRef.current.style.paddingBottom = '0px'
      }
    }
    window.visualViewport?.addEventListener('resize', onResize)

    return () => {
      cancelled = true
      handles.forEach(h => { try { h.remove() } catch (e) { /* ignore */ swallowError('ChatView:156', e) } })
      window.visualViewport?.removeEventListener('resize', onResize)
    }
  }, [])

  // Socket connection
  useEffect(() => {
    if (isPaired) {
      socket.configure(tokenManager.baseUrl, tokenManager.apiToken)
      socket.connect()
      socket.onConnectionChange = (state: ConnectionState) => setConnection(state)
    } else {
      socket.disconnect()
      setConnection('disconnected')
    }
    return () => { socket.disconnect() }
  }, [isPaired, setConnection])

  // Streaming safety reset — 给 120s 足够覆盖 agent 多步推理
  useEffect(() => {
    if (!streaming) return
    const timer = setTimeout(() => {
      setStreaming(false)
      setStatus('连接超时，请重试')
      setTimeout(() => setStatus(''), 3000)
    }, 120000)
    return () => clearTimeout(timer)
  }, [streaming, setStreaming])

  // Auto-scroll
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [messages])

  const handleSend = useCallback(async () => {
    const text = input.trim()
    if ((!text && !fileDataUrl) || streaming) return
    haptic.light()
    setStatus('')
    let content = text
    if (fileDataUrl) {
      const fileName = pickedFile?.name || 'file'
      content = fileDataUrl.startsWith('data:image/')
        ? (text ? `${text}\n\n![${fileName}](${fileDataUrl})` : `![${fileName}](${fileDataUrl})`)
        : (text ? `${text}\n\n[${fileName}](${fileDataUrl})` : `[${fileName}](${fileDataUrl})`)
    }
    try {
      await sendMessage(content)
      setInput(''); sessionStorage.removeItem('siyi-chat-draft')
      setPickedFile(null); setFileDataUrl(null)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '发送失败'
      setStatus(`发送失败: ${msg}`)
      setTimeout(() => setStatus(''), 4000)
      inputRef.current?.focus()
    }
  }, [input, fileDataUrl, pickedFile, streaming, sendMessage])

  const handleIngestConversation = useCallback(async () => {
    if (!sessionId || messages.length === 0) {
      setStatus('当前没有可整理的对话')
      setTimeout(() => setStatus(''), 3000)
      return
    }
    if (!isPaired || connState !== 'connected') {
      setStatus('请先连接桌面端')
      setTimeout(() => setStatus(''), 3000)
      return
    }
    haptic.light()
    setStatus('正在保存对话…')
    try {
      const saved = await saveChatMessages(sessionId, messages.map(m => ({ role: m.role, content: m.content, timestamp: m.timestamp })))
      if (!saved) {
        setStatus('保存失败')
        setTimeout(() => setStatus(''), 3000)
        return
      }
      setStatus('正在提取知识…')
      const res = await ingestConversation(sessionId)
      if (!res) {
        setStatus('整理失败')
      } else if (res.error) {
        setStatus(`整理失败: ${res.error}`)
      } else {
        setStatus(`已整理: ${res.extracted ?? 0} 条记忆`)
      }
      setTimeout(() => setStatus(''), 4000)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '整理失败'
      setStatus(`整理失败: ${msg}`)
      setTimeout(() => setStatus(''), 4000)
    }
  }, [sessionId, messages, isPaired, connState])

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }, [handleSend])

  const connColor: Record<ConnectionState, string> = {
    disconnected: 'var(--text-disabled)',
    connecting: '#FF9F0A',
    connected: '#34C759',
  }
  const connLabel: Record<ConnectionState, string> = {
    disconnected: '未连接',
    connecting: '连接中',
    connected: '已连接',
  }

  return (
    <div ref={rootRef} style={{
      display: 'flex', flexDirection: 'column', height: '100%',
      transition: 'padding-bottom 0.2s ease',
    }}>
      {/* Header —— 2026-10-30 重构：左上角侧边栏入口 + 新建会话 */}
      <div style={{ padding: '16px 20px 0', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {/* 左上角：历史会话抽屉入口（改为侧边栏，可展开） */}
          <button
            onClick={async () => {
              const list = await useMobileStore.getState().listChatSessions()
              setSessions(list)
              setShowHistory(true)
              haptic.light()
            }}
            aria-label="历史会话"
            style={{
              width: 36, height: 36, border: 'none',
              background: 'transparent', borderRadius: 12,
              color: 'var(--text-secondary)', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              marginLeft: -8,
            }}
          >
            <IconMenu size={22} />
          </button>

          {/* 新建会话：紧随侧边栏入口，同样置于左上 */}
          <button
            onClick={() => {
              useMobileStore.getState().startNewSession()
              haptic.light()
            }}
            aria-label="新建会话"
            style={{
              width: 36, height: 36, border: 'none',
              background: 'transparent', borderRadius: 12,
              color: 'var(--text-secondary)', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <IconPlus size={20} />
          </button>

          {/* 标题居中：绝对定位让左右两侧按钮不影响视觉重心 */}
          <div style={{
            flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center',
            gap: 6, marginLeft: -22,
          }}>
            <span style={{ fontSize: 'var(--text-headline)', fontWeight: 650, letterSpacing: -0.3 }}>
              AI 助手
            </span>
          </div>

          {/* 右侧：整理到知识库（仅在可整理时出现，避免常驻占位） */}
          {messages.length > 0 && sessionId && connState === 'connected' ? (
            <button
              onClick={handleIngestConversation}
              disabled={streaming}
              style={{
                padding: '5px 11px',
                borderRadius: 999,
                border: '0.5px solid var(--accent-border)',
                background: 'var(--accent-surface)',
                color: 'var(--accent)',
                fontSize: 'var(--text-caption-2)',
                fontWeight: 600,
                cursor: streaming ? 'default' : 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              整理
            </button>
          ) : <div style={{ width: 36 }} />}
        </div>
        {status && <div style={{ fontSize: 'var(--text-caption-2)', marginTop: 2, textAlign: 'center', color: status.startsWith('发送失败') || status.startsWith('批准失败') || status.startsWith('整理失败') ? 'var(--danger)' : 'var(--text-tertiary)' }}>{status}</div>}
        {isPaired && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: connColor[connState], transition: 'background 0.3s ease' }} />
            {connLabel[connState]}
            {connState === 'connecting' && ' · 重连中...'}
          </div>
        )}
      </div>

      <PendingApprovals />

      {/* Messages */}
      <div ref={listRef} style={{ flex: 1, overflowY: 'auto', padding: '16px 20px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        {messages.length === 0 ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 'var(--text-footnote)', color: 'var(--text-tertiary)', textAlign: 'center', padding: '0 20px' }}>
            {connState === 'connected'
              ? 'AI 助手已就绪，可以开始对话'
              : isPaired
                ? '正在连接桌面端 AI...'
                : '先在设置中配对桌面端或配置云端 AI 密钥'
            }
          </div>
        ) : (
          messages.map((msg: { id: string; role: string; content: string }) => {
            const imgMatch = msg.content.match(/!\[([^\]]*)\]\((data:image\/[^)]+)\)/)
            const fileMatch = !imgMatch ? msg.content.match(/\[([^\]]*)\]\((data:[^)]+)\)/) : null
            const rawText = imgMatch
              ? msg.content.replace(/!\[([^\]]*)\]\(data:image\/[^)]+\)/, '').trim()
              : fileMatch
                ? msg.content.replace(/\[([^\]]*)\]\(data:[^)]+\)/, '').trim()
                : msg.content
            const agentSteps = msg.role === 'assistant' ? parseAgentSteps(rawText) : []
            const agentConfirm = msg.role === 'assistant' ? parseAgentConfirm(rawText) : null
            const textOnly = msg.role === 'assistant' ? stripAgentBlocks(rawText) : rawText
            return (
              <div key={msg.id} style={{
                alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '80%',
                padding: imgMatch ? '6px 6px 10px 10px' : '10px 14px',
                borderRadius: 'var(--radius-lg)',
                background: msg.role === 'user' ? 'var(--accent)' : 'var(--bg-secondary)',
                color: msg.role === 'user' ? '#fff' : 'var(--text-primary)',
                fontSize: 'var(--text-footnote)',
                lineHeight: 1.5,
                whiteSpace: 'pre-wrap',
              }}>
                {msg.role === 'assistant' && !msg.content && streaming ? (
                  <><span style={{ opacity: 0.5 }}>思考中</span><span style={{ animation: 'ellipsis 1.2s infinite' }}>...</span></>
                ) : (
                  <>
                    {imgMatch && <img src={imgMatch[2]} alt={imgMatch[1]} style={{ maxWidth: '100%', maxHeight: 200, borderRadius: 8, marginBottom: textOnly ? 6 : 0, objectFit: 'cover' }} />}
                    {fileMatch && !imgMatch && (
                      <div style={{ padding: '8px 12px', borderRadius: 8, background: 'rgba(255,255,255,0.08)', textAlign: 'center', marginBottom: textOnly ? 6 : 0, fontSize: 'var(--text-caption-1)' }}>
                        📎 {fileMatch[1]}
                      </div>
                    )}
                    {agentSteps.length > 0 && <AgentStepBubble steps={agentSteps} />}
                    {agentConfirm && (
                      <AgentConfirmBubble
                        title={agentConfirm.title}
                        detail={agentConfirm.detail}
                        onApprove={async () => {
                          setStatus('正在申请执行…')
                          try {
                            const ld = await listPendingApprovals()
                            if (Array.isArray(ld) && ld.length > 0) {
                              await approveApproval(ld[0].pending_id)
                              setStatus('已批准')
                            } else setStatus('已允许（无待审批项）')
                          } catch (e) { setStatus('批准失败: ' + (e instanceof Error ? e.message : '')) }
                        }}
                        onReject={async () => {
                          try {
                            const ld = await listPendingApprovals()
                            if (Array.isArray(ld) && ld.length > 0) {
                              await rejectApproval(ld[0].pending_id)
                            }
                          } catch (e) { swallowError('ChatView:388', e) }
                          setStatus('已取消该操作')
                        }}
                      />
                    )}
                    {textOnly && <>{textOnly}</>}
                  </>
                )}
              </div>
            )
          })
        )}
        {streaming && messages.length > 0 && messages[messages.length - 1].content !== '' && (
          <div style={{ alignSelf: 'flex-start', fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)', paddingLeft: 14 }}>
            <span style={{ animation: 'ellipsis 1.2s infinite' }}>...</span>
          </div>
        )}
      </div>

      {/* 2026-10-30 按用户要求移除「读屏 / 电量 / 存储 / 应用」快捷胶囊。
          原因：这排纯文字按钮常驻在输入框上方，占高度且视觉噪音大；
          AI 侧已有等价能力（a11yTools 的 phone__read_screen 等工具，
          由模型按需调用），无需用户手动触发。 */}

      {/* Input bar */}
      <div style={{ position: 'relative', padding: '8px 16px', paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 8px)', borderTop: '0.5px solid var(--border-subtle)', background: 'var(--bg-primary)', display: 'flex', gap: 8, alignItems: 'center' }}>
        {pickedFile && (
          <div style={{ position: 'absolute', bottom: '100%', left: 16, right: 16, display: 'flex', alignItems: 'center', gap: 8, padding: '6px 10px', borderRadius: 'var(--radius-sm)', background: 'var(--accent-surface)', border: '0.5px solid var(--accent-border)', marginBottom: 4 }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--accent)' }}>{pickedFile.type.startsWith('image/') ? '图片' : pickedFile.type.startsWith('video/') ? '视频' : '文件'}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{pickedFile.name}</div>
              <div style={{ fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)' }}>{pickedFile.size > 1024 * 1024 ? (pickedFile.size / 1024 / 1024).toFixed(1) + ' MB' : (pickedFile.size / 1024).toFixed(0) + ' KB'}</div>
            </div>
            <button onClick={() => { setPickedFile(null); setFileDataUrl(null) }} style={{ background: 'none', border: 'none', color: 'var(--danger)', cursor: 'pointer', fontSize: 12, fontWeight: 600, padding: 4 }}>X</button>
          </div>
        )}

        <button onClick={() => setShowAttachment(true)}
          style={{ width: 36, height: 36, borderRadius: '50%', border: '0.5px solid var(--border-default)', color: 'var(--text-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', flexShrink: 0, fontSize: 12, fontWeight: 600, background: 'none' }}>
          附件
        </button>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
          <input
            ref={inputRef}
            value={input}
            onChange={e => { const v = e.target.value; setInput(v); sessionStorage.setItem('siyi-chat-draft', v) }}
            onKeyDown={handleKeyDown}
            placeholder="输入消息..."
            disabled={streaming}
            style={{ width: '100%', padding: '10px 14px', border: '0.5px solid var(--border-default)', borderRadius: 'var(--radius-xl)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 'var(--text-footnote)', outline: 'none' }}
          />
          {/* 实时隐私提示：让用户看得见「这段内容会不会离开设备」 */}
          {privacyHint.sensitive && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 5,
              padding: '3px 10px',
              fontSize: 'var(--text-caption-2)',
              color: 'var(--tone-smooth)',
              background: 'var(--bg-glass)',
              borderRadius: 'var(--radius-full)',
              alignSelf: 'flex-start',
            }}>
              <span aria-hidden="true">🔒</span>
              <span>含敏感内容 · 不会上传，仅在本机处理</span>
            </div>
          )}
        </div>

        <button
          onClick={async () => {
            const text = input.trim()
            if (!text) return
            try {
              const out = await bridgeTranslate(text, 'auto', translateTarget === 'zh' ? 'zh' : 'en')
              if (out) setTranslateResult(out)
              else setTranslateResult('翻译失败')
            } catch { setTranslateResult('翻译失败') }
          }}
          style={{
            width: 36, height: 36, borderRadius: 8, border: '0.5px solid var(--border-subtle)',
            background: 'var(--bg-secondary)', cursor: 'pointer', display: 'flex',
            alignItems: 'center', justifyContent: 'center', flexShrink: 0, fontSize: 15,
            color: 'var(--text-tertiary)',
          }}
          aria-label="翻译"
        >译</button>
        <button
          onClick={handleSend}
          disabled={(!input.trim() && !fileDataUrl) || streaming}
          onTouchStart={e => { if (!streaming) haptic.light(); (e.currentTarget as HTMLElement).style.transform = 'scale(0.92)' }}
          onTouchEnd={e => { (e.currentTarget as HTMLElement).style.transform = 'scale(1)' }}
          style={{
            width: 40, height: 40, borderRadius: '50%', border: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
            background: (input.trim() || fileDataUrl) && !streaming ? 'var(--accent)' : 'var(--bg-tertiary)',
            color: (input.trim() || fileDataUrl) && !streaming ? '#fff' : 'var(--text-disabled)',
            cursor: (input.trim() || fileDataUrl) && !streaming ? 'pointer' : 'default',
            transition: 'all 0.15s ease',
          }}
          aria-label="发送"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
          </svg>
        </button>
      </div>

      {/* 翻译结果 */}
      {translateResult && (
        <div style={{ padding: '8px 14px', margin: '4px 14px', borderRadius: 8, background: 'var(--bg-tertiary)', fontSize: 'var(--text-footnote)', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          <div style={{ fontWeight: 600, marginBottom: 4, fontSize: 'var(--text-caption-2)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span>{translateTarget === 'zh' ? '英→中：' : '中→英：'}</span>
            {/* H11: 切换翻译方向 — 点一下后下次翻译会用新方向 */}
            <button
              onClick={() => setTranslateTarget(prev => prev === 'zh' ? 'en' : 'zh')}
              style={{ background: 'none', border: '1px solid var(--text-disabled)', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 10, padding: '2px 6px', borderRadius: 4 }}
              aria-label="切换翻译方向"
            >
              ⇄ 切换为 {translateTarget === 'zh' ? '中→英' : '英→中'}
            </button>
            <button onClick={() => setTranslateResult(null)} style={{ marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--text-disabled)', cursor: 'pointer', fontSize: 10, fontWeight: 600 }}>X</button>
          </div>
          <div>{translateResult}</div>
        </div>
      )}
      {/* Attachment picker */}
      {showAttachment && (
        <div style={{ position: 'fixed', inset: 0, zIndex: 2000, display: 'flex', alignItems: 'flex-end', justifyContent: 'center', background: 'rgba(0,0,0,0.4)' }}
          onClick={() => setShowAttachment(false)}>
          <div style={{ width: '100%', maxWidth: 400, padding: 'var(--space-4)', paddingBottom: 'calc(var(--space-4) + env(safe-area-inset-bottom))', background: 'var(--bg-secondary)', borderTopLeftRadius: 'var(--radius-lg)', borderTopRightRadius: 'var(--radius-lg)' }}
            onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 'var(--text-headline)', fontWeight: 600, marginBottom: 16, color: 'var(--text-primary)', textAlign: 'center' }}>选择文件</div>
            <div style={{ display: 'flex', gap: 16, justifyContent: 'center', marginBottom: 20 }}>
              {[
                { icon: '拍照', label: '拍照', accept: 'image/*', capture: 'environment' as const },
                { icon: '相册', label: '相册', accept: 'image/*,video/*' },
                { icon: '文件', label: '文件', accept: '*/*' },
              ].map(opt => (
                <label key={opt.label} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                  <div style={{ width: 56, height: 56, borderRadius: 'var(--radius-md)', background: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>{opt.icon}</div>
                  <span style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-secondary)' }}>{opt.label}</span>
                  <input type="file" accept={opt.accept} capture={opt.capture} style={{ display: 'none' }}
                    onChange={async e => { const f = e.target.files?.[0]; if (f) { try { setFileDataUrl(await readFileBase64(f)); setPickedFile(f) } catch { setStatus('文件读取失败') } setShowAttachment(false) } }} />
                </label>
              ))}
            </div>
            <button onClick={() => setShowAttachment(false)}
              style={{ width: '100%', padding: '12px 0', borderRadius: 'var(--radius-sm)', border: 'none', background: 'var(--bg-tertiary)', color: 'var(--text-secondary)', fontSize: 'var(--text-body)', cursor: 'pointer' }}>
              取消
            </button>
          </div>
        </div>
      )}

      {/* 历史会话侧边栏抽屉（2026-10-30：由底部 Sheet 改为左侧可展开抽屉） */}
      {showHistory && (
        <div
          onClick={() => setShowHistory(false)}
          style={{
            position: 'fixed', inset: 0, zIndex: 80,
            background: 'rgba(0,0,0,0.38)',
            display: 'flex', alignItems: 'stretch', justifyContent: 'flex-start',
            animation: 'siyiFadeIn 0.18s ease-out',
          }}
        >
          <div
            onClick={e => e.stopPropagation()}
            style={{
              width: 'min(82vw, 320px)',
              background: 'var(--bg-primary)',
              borderTopRightRadius: 20,
              borderBottomRightRadius: 20,
              paddingTop: 'var(--safe-top)',
              paddingBottom: 'var(--safe-bottom)',
              display: 'flex', flexDirection: 'column',
              boxShadow: '4px 0 24px rgba(0,0,0,0.14)',
              animation: 'siyiSlideInLeft 0.24s cubic-bezier(0.22, 1, 0.36, 1)',
            }}
          >
            {/* 抽屉顶部：标题 + 新建（新建也放在这里，符合手机端笔记习惯） */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '18px 16px 12px',
            }}>
              <span style={{ fontSize: 'var(--text-title-2)', fontWeight: 700, flex: 1, letterSpacing: -0.3 }}>
                历史会话
              </span>
              <button
                onClick={() => {
                  useMobileStore.getState().startNewSession()
                  setShowHistory(false)
                  haptic.light()
                }}
                aria-label="新建会话"
                style={{
                  width: 34, height: 34, border: 'none',
                  background: 'var(--accent-surface)', borderRadius: 11,
                  color: 'var(--accent)', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}
              >
                <IconPlus size={18} />
              </button>
            </div>
            <div style={{ height: '0.5px', background: 'var(--border-subtle)', margin: '0 16px 8px' }} />

            <div style={{ flex: 1, overflowY: 'auto', padding: '4px 10px 10px' }}>
              {sessions.length === 0 ? (
                <div style={{
                  padding: '40px 20px', textAlign: 'center',
                  color: 'var(--text-tertiary)', fontSize: 'var(--text-footnote)',
                }}>还没有历史会话</div>
              ) : sessions.map(s => (
                <div key={s.id} style={{
                  display: 'flex', alignItems: 'center', gap: 4,
                  padding: '10px 8px 10px 12px',
                  marginBottom: 2,
                  borderRadius: 12,
                  transition: 'background 0.15s ease',
                }}>
                  <button
                    onClick={async () => {
                      await useMobileStore.getState().openChatSession(s.id)
                      setShowHistory(false)
                      haptic.light()
                    }}
                    style={{
                      flex: 1, minWidth: 0, textAlign: 'left',
                      border: 'none', background: 'none', padding: 0, cursor: 'pointer',
                    }}
                  >
                    <div style={{
                      fontSize: 'var(--text-subhead)', color: 'var(--text-primary)',
                      fontWeight: 500,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>{s.title || '未命名会话'}</div>
                    <div style={{
                      fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
                      marginTop: 3,
                    }}>
                      {new Date(s.updatedAt).toLocaleString('zh-CN', {
                        month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit',
                      })} · {s.messages.length} 条
                    </div>
                  </button>
                  <button
                    onClick={async () => {
                      await useMobileStore.getState().removeChatSession(s.id)
                      setSessions(prev => prev.filter(x => x.id !== s.id))
                    }}
                    aria-label="删除会话"
                    style={{
                      width: 28, height: 28, border: 'none', background: 'none',
                      color: 'var(--text-tertiary)', cursor: 'pointer', flexShrink: 0,
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      borderRadius: 8, opacity: 0.6,
                    }}
                  >
                    <IconX size={15} />
                  </button>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
