import { useState, useCallback, useRef, useEffect } from 'react'
import { haptic } from '../../../lib/haptic'
import { usingCloudAi, cloudChat, cloudChatMultiTurn } from '../../../lib/cloudAiClient'

/**
 * AI 笔记助手 / AI writing assistant
 *
 * 定位：对标「在编辑页直接调用 AI」的体验（小米笔记的魔法棒、Notion AI）。
 * 与聊天页的区别：这里以「选中文本 + 一个动作」为交互单元，结果可一键替换原文，
 * 不需要用户复制粘贴。
 *
 * 支持的 6 个动作，覆盖写作过程中最高频的求助场景：
 *   改写 / 续写 / 摘要 / 润色 / 翻译 / 提取待办
 *
 * 隐私：沿用 ChatOrchestrator 的判定口径 —— 含敏感词的内容只走本地
 * （这里选择直接拒绝并提示，因为写作助手没有本地模型可用）。
 */
export type AiAction = 'rewrite' | 'continue' | 'summarize' | 'polish' | 'translate' | 'extract_todos'

/** 对话标签用的一轮消息 */
interface Turn {
  role: 'user' | 'assistant'
  content: string
}

interface Props {
  open: boolean
  onClose: () => void
  /** 选中的文本；为空时表示处理整篇 */
  selectionText: string
  fullText: string
  /** 用 AI 结果替换选中文本（或整篇） */
  onApply: (text: string, mode: 'replace_selection' | 'replace_all' | 'append') => void
}

const ACTIONS: { id: AiAction; label: string; desc: string; icon: string }[] = [
  { id: 'rewrite',       label: '改写',     desc: '换个说法，保持原意',   icon: '🔁' },
  { id: 'continue',      label: '续写',     desc: '接着往下写',           icon: '➡️' },
  { id: 'summarize',     label: '摘要',     desc: '提炼要点',             icon: '📌' },
  { id: 'polish',        label: '润色',     desc: '修语病、提可读性',     icon: '✨' },
  { id: 'translate',     label: '翻译',     desc: '中英互译',             icon: '🌐' },
  { id: 'extract_todos', label: '提取待办', desc: '从文中抽出可执行项',   icon: '☑️' },
]

/** 敏感内容词表（与 aiRouter 的 SENSITIVE_PATTERNS 对齐） */
const SENSITIVE = ['密码', '密钥', 'token', 'api_key', '身份证', '银行卡', '私密', '秘密']

function looksSensitive(text: string): boolean {
  const lower = text.toLowerCase()
  return SENSITIVE.some(k => lower.includes(k.toLowerCase()))
}

function buildPrompt(action: AiAction, text: string): { system: string; user: string } {
  const base = '你是一个中文写作助手。只输出改写结果本身，不要任何解释、前言或 Markdown 代码块包裹。'
  switch (action) {
    case 'rewrite':
      return { system: base, user: `请改写下面这段文字，保持原意但换一种更清晰的表达：\n\n${text}` }
    case 'continue':
      return { system: base, user: `请自然地续写下面这段文字，风格保持一致，只输出续写部分：\n\n${text}` }
    case 'summarize':
      return { system: '你是一个摘要助手。用简短要点提炼，每点一行以「- 」开头，不要解释。', user: `概括以下内容：\n\n${text}` }
    case 'polish':
      return { system: base, user: `请润色下面这段文字，修正语病、提升可读性，保持原意：\n\n${text}` }
    case 'translate':
      return { system: '你是翻译。如果原文是中文就译成英文，是英文就译成中文。只输出译文。', user: text }
    case 'extract_todos':
      return {
        system: '你从文本中提取可执行事项。每项一行，格式为「- [ ] 事项」。若无可执行项，输出「- [ ] （无明确待办）」。',
        user: `从下面内容中提取待办事项：\n\n${text}`,
      }
  }
}

export default function NoteAiAssistant({ open, onClose, selectionText, fullText, onApply }: Props) {
  const [busy, setBusy] = useState<AiAction | null>(null)
  const [result, setResult] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [targetLabel, setTargetLabel] = useState('')

  /* ── 对话标签状态 / chat tab state ──
   * tab 默认落在「动作」：用户既有习惯是点开就用六个快捷动作，
   * 对话是新增入口，不该抢默认位。
   */
  const [tab, setTab] = useState<'actions' | 'chat'>('actions')
  const [turns, setTurns] = useState<Turn[]>([])
  const [draft, setDraft] = useState('')
  const [chatBusy, setChatBusy] = useState(false)
  const [chatError, setChatError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const target = selectionText.trim() || fullText.trim()

  /* 新消息到达后滚到底部，否则用户看不见刚出来的回答 */
  useEffect(() => {
    if (tab !== 'chat') return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [turns, chatBusy, tab])

  /* 弹层关闭时清掉对话错误，避免下次打开还挂着上次的红字 */
  useEffect(() => {
    if (!open) {
      setChatError(null)
      setError(null)
    }
  }, [open])

  /**
   * 发起一轮对话。
   *
   * 关键点：把**当前笔记正文**作为 system 里的背景资料一并带上，
   * 且每轮都重发（而不是只在第一轮）。理由：
   *   - 用户可能在对话中途改了正文，重发保证 AI 看到的是最新版本；
   *   - 笔记场景的追问几乎都隐含「针对这篇内容」，AI 缺上下文就会答非所问。
   * 但正文过长会挤爆上下文，限制在 6000 字（约 4k token 量级，留足对话余量）。
   */
  const sendChat = useCallback(async (raw: string) => {
    const text = raw.trim()
    if (!text || chatBusy) return
    if (looksSensitive(text)) {
      setChatError('内容包含敏感信息（密码/密钥等），已阻止上传。')
      return
    }
    if (!usingCloudAi()) {
      setChatError('未配置云 AI。请到 设置 → AI 配置 里填入 API Key。')
      return
    }

    haptic.light()
    setChatError(null)
    const nextTurns: Turn[] = [...turns, { role: 'user', content: text }]
    setTurns(nextTurns)
    setDraft('')
    setChatBusy(true)

    try {
      const noteCtx = target
        ? `\n\n【当前笔记正文，供你参考】\n${target.slice(0, 6000)}${target.length > 6000 ? '\n（正文过长，以上为前 6000 字）' : ''}`
        : ''
      const system =
        '你是嵌在笔记编辑器里的中文写作助手。回答要简洁、直接、可执行，'
        + '不要客套开场白。用户问什么就答什么；涉及改写时直接给出成品文字。'
        + '若用户要求插入正文，只输出可插入的正文内容本身。'
        + noteCtx

      const reply = await cloudChatMultiTurn({
        system,
        messages: nextTurns.slice(-20), // 只带最近 20 轮，防止上下文无限增长
        maxTokens: 1500,
      })

      if (!reply || !reply.trim()) {
        setChatError('AI 未返回内容，请重试')
      } else {
        setTurns(prev => [...prev, { role: 'assistant', content: reply.trim() }])
      }
    } catch (e) {
      setChatError(e instanceof Error ? e.message : 'AI 调用失败')
    } finally {
      setChatBusy(false)
    }
  }, [chatBusy, turns, target])

  const run = useCallback(async (action: AiAction) => {
    if (!target) {
      setError('没有可处理的内容')
      return
    }
    if (looksSensitive(target)) {
      setError('内容包含敏感信息（密码/密钥等），已阻止上传。请手动处理这部分。')
      return
    }
    if (!usingCloudAi()) {
      setError('未配置云 AI。请到 设置 → AI 配置 里填入 API Key。')
      return
    }

    haptic.light()
    setBusy(action)
    setError(null)
    setResult('')
    setTargetLabel(selectionText.trim() ? '选中文本' : '整篇笔记')

    try {
      const { system, user } = buildPrompt(action, target)
      const out = await cloudChat({ system, user, maxTokens: 1200 })
      if (!out || !out.trim()) {
        setError('AI 未返回内容，请重试')
      } else {
        setResult(out.trim())
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'AI 调用失败')
    } finally {
      setBusy(null)
    }
  }, [target, selectionText])

  if (!open) return null

  return (
    <div
      style={{
        position: 'fixed', inset: 0, zIndex: 60,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex', alignItems: 'flex-end',
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: '100%',
          maxHeight: '82vh',
          background: 'var(--bg-primary)',
          borderRadius: 'var(--radius-xl) var(--radius-xl) 0 0',
          paddingBottom: 'var(--safe-bottom)',
          display: 'flex', flexDirection: 'column',
          animation: 'fadeSlideIn 0.2s var(--ease-out)',
        }}
      >
        {/* 头部 */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 'var(--space-3)',
          padding: 'var(--space-4)', borderBottom: '0.5px solid var(--border-subtle)',
        }}>
          <span style={{ fontSize: 20 }}>✨</span>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 'var(--text-headline)', fontWeight: 600 }}>AI 写作助手</div>
            <div style={{ fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)', marginTop: 2 }}>
              {selectionText.trim()
                ? `已选中 ${selectionText.trim().length} 字`
                : `未选中，将处理整篇（${fullText.trim().length} 字）`}
            </div>
          </div>
          <button
            onClick={onClose}
            aria-label="关闭"
            style={{
              width: 32, height: 32, border: 'none', background: 'var(--bg-secondary)',
              borderRadius: '50%', color: 'var(--text-secondary)',
              fontSize: 16, cursor: 'pointer',
            }}
          >✕</button>
        </div>

        {/* 标签切换：「动作」/「对话」
            —— 复用一个弹层而不是再开一个新弹层：编辑页已有一层 AiAssistant，
            叠加第二层弹层在小屏上会互相遮挡，且返回栈容易乱。
            分段控件形态（胶囊底槽 + 选中块），与系统设置页的表单分段控件一致。 */}
        <div style={{ padding: 'var(--space-2) var(--space-4) 0' }}>
          <div style={{
            display: 'flex', gap: 2, padding: 3,
            background: 'var(--bg-secondary)', borderRadius: 10,
          }}>
            {([
              { id: 'actions' as const, label: '快捷动作' },
              { id: 'chat' as const, label: '对话' },
            ]).map(t => {
              const active = tab === t.id
              return (
                <button
                  key={t.id}
                  onClick={() => { haptic.light(); setTab(t.id) }}
                  aria-pressed={active}
                  style={{
                    flex: 1, height: 34, border: 'none', cursor: 'pointer',
                    borderRadius: 8,
                    background: active ? 'var(--bg-primary)' : 'transparent',
                    boxShadow: active ? '0 1px 3px rgba(0,0,0,0.12)' : 'none',
                    color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontSize: 'var(--text-footnote)',
                    fontWeight: active ? 600 : 400,
                    transition: 'background 0.15s, color 0.15s',
                  }}
                >{t.label}</button>
              )
            })}
          </div>
        </div>

        {tab === 'actions' && (
        <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-4)' }}>
          {/* 动作网格 */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 'var(--space-2)',
          }}>
            {ACTIONS.map(a => {
              const loading = busy === a.id
              return (
                <button
                  key={a.id}
                  onClick={() => { void run(a.id) }}
                  disabled={busy !== null}
                  style={{
                    display: 'flex', flexDirection: 'column', alignItems: 'center',
                    gap: 4, padding: 'var(--space-3) var(--space-2)',
                    background: loading ? 'var(--accent-surface)' : 'var(--bg-secondary)',
                    border: loading ? '1px solid var(--accent)' : '1px solid transparent',
                    borderRadius: 'var(--radius-md)',
                    cursor: busy ? 'wait' : 'pointer',
                    opacity: busy && !loading ? 0.5 : 1,
                    minHeight: 74,
                  }}
                >
                  <span style={{ fontSize: 18 }}>{loading ? '⏳' : a.icon}</span>
                  <span style={{
                    fontSize: 'var(--text-footnote)', fontWeight: 500,
                    color: loading ? 'var(--accent)' : 'var(--text-primary)',
                  }}>{a.label}</span>
                  <span style={{
                    fontSize: 'var(--text-caption-2)', color: 'var(--text-tertiary)',
                    textAlign: 'center', lineHeight: 1.3,
                  }}>{a.desc}</span>
                </button>
              )
            })}
          </div>

          {error && (
            <div style={{
              marginTop: 'var(--space-4)', padding: 'var(--space-3)',
              background: 'rgba(255,59,48,0.1)', borderRadius: 'var(--radius-md)',
              color: 'var(--danger)', fontSize: 'var(--text-footnote)', lineHeight: 1.5,
            }}>{error}</div>
          )}

          {result && (
            <div style={{ marginTop: 'var(--space-4)' }}>
              <div style={{
                fontSize: 'var(--text-caption-1)', color: 'var(--text-tertiary)',
                marginBottom: 'var(--space-2)',
              }}>AI 结果（{targetLabel}）</div>
              <div style={{
                padding: 'var(--space-3)',
                background: 'var(--bg-secondary)',
                borderRadius: 'var(--radius-md)',
                fontSize: 'var(--text-footnote)',
                lineHeight: 1.7,
                whiteSpace: 'pre-wrap',
                maxHeight: '28vh',
                overflowY: 'auto',
                color: 'var(--text-primary)',
              }}>{result}</div>

              <div style={{ display: 'flex', gap: 'var(--space-2)', marginTop: 'var(--space-3)' }}>
                <button
                  onClick={() => {
                    haptic.light()
                    const mode = selectionText.trim() ? 'replace_selection' : 'replace_all'
                    onApply(result, mode)
                    onClose()
                  }}
                  style={{
                    flex: 1, height: 44, border: 'none',
                    borderRadius: 'var(--radius-md)',
                    background: 'var(--accent)', color: '#fff',
                    fontSize: 'var(--text-callout)', fontWeight: 600, cursor: 'pointer',
                  }}
                >替换{selectionText.trim() ? '选中内容' : '整篇'}</button>
                <button
                  onClick={() => {
                    haptic.light()
                    onApply(result, 'append')
                    onClose()
                  }}
                  style={{
                    flex: 1, height: 44,
                    border: '0.5px solid var(--border-default)',
                    borderRadius: 'var(--radius-md)',
                    background: 'transparent', color: 'var(--text-primary)',
                    fontSize: 'var(--text-callout)', cursor: 'pointer',
                  }}
                >追加到末尾</button>
              </div>
            </div>
          )}
        </div>
        )}

        {tab === 'chat' && (
        /* ── 对话标签 ──
            用带 flex 布局的 div 作容器而不是 <Fragment>：
            对话页整体需要「消息区 flex:1 + 输入区贴底」，
            这个布局本身就要求一个真实容器，顺带避免 JSX 解析歧义。 */
        <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
          <div
            ref={scrollRef}
            style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-4)', minHeight: 180 }}
          >
            {turns.length === 0 && !chatError && (
              <div style={{
                padding: 'var(--space-4)', textAlign: 'center',
                color: 'var(--text-tertiary)', fontSize: 'var(--text-footnote)',
                lineHeight: 1.8,
              }}>
                <div style={{ fontSize: 28, marginBottom: 8 }}>💬</div>
                和 AI 聊聊这篇笔记。<br />
                可以连续追问，它记得上文，也能看到正文内容。
              </div>
            )}

            {turns.map((t, i) => {
              const isUser = t.role === 'user'
              return (
                <div
                  key={i}
                  style={{
                    display: 'flex',
                    justifyContent: isUser ? 'flex-end' : 'flex-start',
                    marginBottom: 'var(--space-3)',
                  }}
                >
                  <div style={{
                    maxWidth: '86%',
                    padding: '10px 13px',
                    borderRadius: isUser ? '14px 14px 4px 14px' : '14px 14px 14px 4px',
                    background: isUser ? 'var(--accent)' : 'var(--bg-secondary)',
                    color: isUser ? '#fff' : 'var(--text-primary)',
                    fontSize: 'var(--text-footnote)',
                    lineHeight: 1.65,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}>
                    {t.content}
                    {!isUser && (
                      <div style={{
                        display: 'flex', gap: 'var(--space-3)',
                        marginTop: 8, paddingTop: 8,
                        borderTop: '0.5px solid var(--border-subtle)',
                      }}>
                        <button
                          onClick={() => {
                            haptic.light()
                            onApply(t.content, 'append')
                            onClose()
                          }}
                          style={{
                            border: 'none', background: 'none', padding: 0,
                            color: 'var(--accent)', fontSize: 'var(--text-caption-1)',
                            fontWeight: 500, cursor: 'pointer',
                          }}
                        >插入正文末尾</button>
                        <button
                          onClick={() => {
                            haptic.light()
                            /* 与动作页保持同一判定：有选中才替换选中，否则替换整篇。
                               selRange 在弹层打开后用户可能已不再关注，故以
                               selectionText 是否为空（即父组件传来时有选中）为准。
                               注意不能传 'append' —— 那是另一个按钮的语义。 */
                            const mode = selectionText.trim() ? 'replace_selection' : 'replace_all'
                            onApply(t.content, mode)
                            onClose()
                          }}
                          style={{
                            border: 'none', background: 'none', padding: 0,
                            color: 'var(--accent)', fontSize: 'var(--text-caption-1)',
                            fontWeight: 500, cursor: 'pointer',
                          }}
                        >{selectionText.trim() ? '替换选中' : '替换整篇'}</button>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}

            {chatBusy && (
              <div style={{
                color: 'var(--text-tertiary)', fontSize: 'var(--text-footnote)',
                padding: '4px 2px',
              }}>AI 正在思考…</div>
            )}

            {chatError && (
              <div style={{
                marginTop: 'var(--space-2)', padding: 'var(--space-3)',
                background: 'rgba(255,59,48,0.1)', borderRadius: 'var(--radius-md)',
                color: 'var(--danger)', fontSize: 'var(--text-footnote)', lineHeight: 1.5,
              }}>{chatError}</div>
            )}
          </div>

          {/* 输入区：贴底，键盘弹起时由 useKeyboardAvoidance 上推 */}
          <div style={{
            flexShrink: 0,
            display: 'flex', alignItems: 'flex-end', gap: 'var(--space-2)',
            padding: 'var(--space-3) var(--space-4)',
            borderTop: '0.5px solid var(--border-subtle)',
          }}>
            {turns.length > 0 && (
              <button
                onClick={() => { haptic.light(); setTurns([]); setChatError(null) }}
                aria-label="清空对话"
                style={{
                  width: 38, height: 38, flexShrink: 0,
                  border: '0.5px solid var(--border-default)',
                  borderRadius: 19, background: 'transparent',
                  color: 'var(--text-secondary)', fontSize: 14, cursor: 'pointer',
                }}
              >🗑</button>
            )}
            <textarea
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => {
                /* 桌面/外接键盘回车直接发送；Shift+Enter 换行 */
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void sendChat(draft)
                }
              }}
              placeholder="问点什么…"
              rows={1}
              style={{
                flex: 1, minHeight: 38, maxHeight: 110,
                padding: '9px 12px',
                border: '0.5px solid var(--border-default)',
                borderRadius: 19,
                background: 'var(--bg-secondary)',
                color: 'var(--text-primary)',
                fontSize: 'var(--text-footnote)',
                lineHeight: 1.5,
                resize: 'none',
                outline: 'none',
                fontFamily: 'inherit',
              }}
            />
            <button
              onClick={() => { void sendChat(draft) }}
              disabled={chatBusy || !draft.trim()}
              aria-label="发送"
              style={{
                width: 38, height: 38, flexShrink: 0,
                border: 'none', borderRadius: 19,
                background: (chatBusy || !draft.trim()) ? 'var(--bg-secondary)' : 'var(--accent)',
                color: (chatBusy || !draft.trim()) ? 'var(--text-tertiary)' : '#fff',
                fontSize: 15,
                cursor: (chatBusy || !draft.trim()) ? 'default' : 'pointer',
              }}
            >{chatBusy ? '…' : '↑'}</button>
          </div>
        </div>
        )}
      </div>
    </div>
  )
}
