import { useState, useCallback } from 'react'
import { haptic } from '../../../lib/haptic'
import { usingCloudAi, cloudChat } from '../../../lib/cloudAiClient'

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

  const target = selectionText.trim() || fullText.trim()

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
      </div>
    </div>
  )
}
