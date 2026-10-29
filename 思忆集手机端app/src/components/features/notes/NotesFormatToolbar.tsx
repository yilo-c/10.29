import { useCallback } from 'react'
import { haptic } from '../../../lib/haptic'

/**
 * 笔记格式工具栏 — 对标小米笔记编辑页
 *
 * 小米笔记的底部工具栏是 6 个图标：AI(魔法棒) / 语音 / 图片 / 手写 / 待办清单 / 文字格式。
 * 这里保留同样的信息密度，但把「手写」换成更适合 Markdown 的「结构化」，
 * 并把 AI 做成一级入口（点开弹出改写/续写/摘要/格式化）。
 */

export type ToolbarAction =
  | 'ai'
  | 'voice'
  | 'image'
  | 'checklist'
  | 'format'
  | 'more'

interface Props {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>
  content: string
  onChange: (val: string) => void
  /** 当前是否有选中文本，用于决定 AI 按钮是否可用 */
  selection?: { start: number; end: number }
  onAction?: (action: ToolbarAction) => void
}

interface FmtDef {
  id: string
  label: string
  title: string
  before: string
  after: string
  block: boolean
}

const FORMATS: FmtDef[] = [
  { id: 'b',    label: 'B',   title: '粗体',     before: '**', after: '**', block: false },
  { id: 'i',    label: 'I',   title: '斜体',     before: '*',  after: '*',  block: false },
  { id: 'h1',   label: 'H1',  title: '标题',     before: '## ', after: '',  block: true },
  { id: 'ul',   label: '•',   title: '无序列表', before: '- ', after: '',   block: true },
  { id: 'ol',   label: '1.',  title: '有序列表', before: '1. ', after: '',  block: true },
  { id: 'todo', label: '☑',   title: '待办项',   before: '- [ ] ', after: '', block: true },
  { id: 'quote',label: '❝',   title: '引用',     before: '> ', after: '',   block: true },
  { id: 'code', label: '</>', title: '代码块',   before: '```\n', after: '\n```', block: false },
  { id: 'hr',   label: '—',   title: '分割线',   before: '\n---\n', after: '', block: false },
]

const PRIMARY: { id: ToolbarAction; icon: string; title: string }[] = [
  { id: 'ai',        icon: '✨', title: 'AI 辅助' },
  { id: 'voice',     icon: '🎙', title: '语音输入' },
  { id: 'image',     icon: '🖼', title: '插入图片' },
  { id: 'checklist', icon: '☑',  title: '待办清单' },
  { id: 'format',    icon: 'T',  title: '文字格式' },
  { id: 'more',      icon: '⋯',  title: '更多' },
]

export default function NotesFormatToolbar({
  textareaRef, content, onChange, selection, onAction,
}: Props) {
  const applyFormat = useCallback((fmt: FmtDef) => {
    const ta = textareaRef.current
    if (!ta) return
    haptic.light()

    const start = ta.selectionStart
    const end = ta.selectionEnd
    const val = content

    if (fmt.id === 'hr') {
      // 分割线：独占一行
      const lineStart = val.lastIndexOf('\n', start - 1) + 1
      const newVal = val.slice(0, lineStart) + '---\n' + val.slice(lineStart)
      onChange(newVal)
      const pos = lineStart + 4
      setTimeout(() => { ta.focus(); ta.setSelectionRange(pos, pos) }, 0)
      return
    }

    if (fmt.block) {
      // 块级：作用到光标所在行（多行选中时逐行加前缀）
      const lineStart = val.lastIndexOf('\n', start - 1) + 1
      const lineEndIdx = val.indexOf('\n', end)
      const lineEnd = lineEndIdx === -1 ? val.length : lineEndIdx
      const block = val.slice(lineStart, lineEnd)

      const lines = block.split('\n')
      // 若已带该前缀则移除（切换语义）
      const already = lines.every(l => l.startsWith(fmt.before))
      const next = already
        ? lines.map(l => l.slice(fmt.before.length)).join('\n')
        : lines.map(l => fmt.before + l).join('\n')

      const newVal = val.slice(0, lineStart) + next + val.slice(lineEnd)
      onChange(newVal)
      const pos = lineStart + next.length
      setTimeout(() => { ta.focus(); ta.setSelectionRange(pos, pos) }, 0)
      return
    }

    // 行内：包裹选中文本（无选中则插入占位并选中它）
    const selected = val.substring(start, end)
    const placeholder = selected || '文字'
    const wrapped = fmt.before + placeholder + fmt.after
    const newVal = val.slice(0, start) + wrapped + val.slice(end)
    onChange(newVal)
    setTimeout(() => {
      ta.focus()
      const s = start + fmt.before.length
      ta.setSelectionRange(s, s + placeholder.length)
    }, 0)
  }, [content, onChange, textareaRef])

  const hasSelection = !!selection && selection.end > selection.start

  return (
    <div style={{ flexShrink: 0 }}>
      {/* ── 一级功能条：对标小米笔记的 6 图标 ── */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-around',
        padding: '6px 4px',
        background: 'var(--bg-secondary)',
        borderTop: '0.5px solid var(--border-subtle)',
      }}>
        {PRIMARY.map(p => {
          const isAi = p.id === 'ai'
          return (
            <button
              key={p.id}
              title={p.title}
              aria-label={p.title}
              onPointerDown={e => e.preventDefault()}
              onClick={() => {
                haptic.light()
                if (isAi) {
                  // AI 是核心能力，用强调色凸显；无选中文本时也可用（整篇处理）
                  onAction?.('ai')
                  return
                }
                if (p.id === 'format') {
                  // 展开/收起下方格式行由父组件控制，这里直接滚动展示
                  onAction?.('format')
                  return
                }
                onAction?.(p.id)
              }}
              style={{
                flex: 1,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: 38,
                border: 'none',
                background: isAi ? 'var(--accent-surface)' : 'transparent',
                borderRadius: 'var(--radius-sm)',
                color: isAi ? 'var(--accent)' : 'var(--text-secondary)',
                fontSize: isAi ? 17 : 16,
                fontWeight: isAi ? 600 : 400,
                cursor: 'pointer',
                WebkitTapHighlightColor: 'transparent',
                position: 'relative',
              }}
            >
              {p.icon}
              {isAi && hasSelection && (
                <span style={{
                  position: 'absolute', top: 6, right: 10,
                  width: 5, height: 5, borderRadius: '50%',
                  background: 'var(--accent)',
                }} />
              )}
            </button>
          )
        })}
      </div>

      {/* ── 二级格式行：Markdown 语法快捷插入 ── */}
      <div style={{
        display: 'flex',
        gap: 2,
        padding: '4px 6px',
        background: 'var(--bg-tertiary)',
        overflowX: 'auto',
        scrollbarWidth: 'none',
      }}>
        {FORMATS.map(fmt => (
          <button
            key={fmt.id}
            title={fmt.title}
            aria-label={fmt.title}
            onPointerDown={e => e.preventDefault()}
            onClick={() => applyFormat(fmt)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              minWidth: 38,
              height: 32,
              padding: '0 8px',
              border: 'none',
              borderRadius: 'var(--radius-sm)',
              background: 'transparent',
              color: 'var(--text-secondary)',
              fontSize: fmt.id === 'code' ? 11 : 'var(--text-footnote)',
              fontWeight: fmt.id === 'b' ? 700 : 400,
              fontStyle: fmt.id === 'i' ? 'italic' : 'normal',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              WebkitTapHighlightColor: 'transparent',
            }}
          >{fmt.label}</button>
        ))}
      </div>
    </div>
  )
}
