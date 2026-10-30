import { useCallback, useState } from 'react'
import { haptic } from '../../../lib/haptic'
import {
  IconSparkles, IconImage, IconChecklist, IconBold, IconItalic,
  IconHeading, IconList, IconQuote, IconDivider, IconMore,
  IconChevronDownCircle,
} from '../../ui'

/**
 * 笔记格式工具栏 — 对标小米笔记编辑页
 *
 * 2026-10-30 改版（用户反馈「编辑页还是偏 PC 端、图标老气」）：
 *   旧版问题：
 *     1. 主操作行用 emoji（✨🎙🖼☑）+ 字符（T、⋯）混排，字形风格完全
 *        不统一，是「乱」最直接的来源；
 *     2. 二级格式行**常驻显示**，手机竖屏下白白吃掉一整行高度，
 *        键盘弹起后编辑区被压得很窄 —— 这是典型的 PC 思维；
 *     3. 格式行用 B / I / H1 / • / 1. / ☑ 等字符当按钮，
 *        没有图形语义，视觉噪点多。
 *
 *   新版：
 *     1. 全部换成统一的线性图标（1.75px 圆头），无 emoji、无字符占位；
 *     2. 二级格式行默认收起，点「格式」才展开，把高度还给正文；
 *     3. 格式行内图标统一 20px，横向可滚动，不再挤压变形。
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
  title: string
  before: string
  after: string
  block: boolean
  /** 统一图标组件，替代原来的字符 label */
  Icon: React.FC<{ size?: number }>
}

const FORMATS: FmtDef[] = [
  { id: 'b',     title: '粗体',     before: '**', after: '**', block: false, Icon: IconBold },
  { id: 'i',     title: '斜体',     before: '*',  after: '*',  block: false, Icon: IconItalic },
  { id: 'h1',    title: '标题',     before: '## ', after: '',  block: true,  Icon: IconHeading },
  { id: 'ul',    title: '无序列表', before: '- ',  after: '',  block: true,  Icon: IconList },
  // 注意：这里**不再**放「待办项」。一级工具栏的「待办清单」已覆盖该功能，
  // 两者都插入 "- [ ] "，属于重复入口（用户反馈「两个功能重复」的其中一处）。
  { id: 'quote', title: '引用',     before: '> ',  after: '',  block: true,  Icon: IconQuote },
  { id: 'hr',    title: '分割线',   before: '\n---\n', after: '', block: false, Icon: IconDivider },
]

const PRIMARY: {
  id: ToolbarAction
  title: string
  Icon: React.FC<{ size?: number; accent?: boolean }>
  primary?: boolean
}[] = [
  { id: 'ai',        title: 'AI 辅助',  Icon: IconSparkles, primary: true },
  { id: 'image',     title: '插入图片', Icon: IconImage },
  { id: 'checklist', title: '待办清单', Icon: IconChecklist },
  { id: 'format',    title: '文字格式', Icon: IconChevronDownCircle },
  { id: 'more',      title: '更多',     Icon: IconMore },
]

export default function NotesFormatToolbar({
  textareaRef, content, onChange, selection, onAction,
}: Props) {
  // 二级格式行默认收起：手机竖屏空间宝贵，正文优先
  const [formatOpen, setFormatOpen] = useState(false)

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
    <div style={{ background: 'var(--bg-secondary)', borderTop: '0.5px solid var(--border-subtle)' }}>
      {/* ── 一级：常用操作 ──
          2026-10-30 改为「固定宽度 + 横向滑动」：
            原先用 flex:1 + space-around 均分，图标数量增加时每个按钮被压窄，
            图标挤在一起；用户要求「不够放则改为可平移滑动」。
          现在：每个按钮固定 minWidth，总数放得下时自动居中铺满，
                放不下时容器横向滚动（scroll-snap 让停靠更整齐）。 */}
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-around',
        padding: '7px 6px',
        gap: 2,
        overflowX: 'auto',
        overflowY: 'hidden',
        scrollbarWidth: 'none',
        WebkitOverflowScrolling: 'touch',
        scrollSnapType: 'x proximity',
      }}>
        {PRIMARY.map(p => {
          const isAi = p.primary === true
          const isFormatToggle = p.id === 'format'
          const active = isFormatToggle && formatOpen
          const Icon = p.Icon
          return (
            <button
              key={p.id}
              title={p.title}
              aria-label={p.title}
              aria-pressed={isFormatToggle ? formatOpen : undefined}
              onPointerDown={e => e.preventDefault()}
              onClick={() => {
                haptic.light()
                if (isFormatToggle) {
                  setFormatOpen(v => !v)
                  onAction?.('format')
                  return
                }
                onAction?.(p.id)
              }}
              style={{
                // 固定宽度（不再 flex:1 均分）：图标多了会横向滑动，不会被压扁
                flex: '0 0 auto',
                minWidth: 56,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                height: 40,
                border: 'none',
                background: isAi
                  ? 'var(--accent-surface)'
                  : active ? 'var(--bg-tertiary)' : 'transparent',
                borderRadius: 12,
                color: isAi ? 'var(--accent)' : active ? 'var(--text-primary)' : 'var(--text-secondary)',
                cursor: 'pointer',
                WebkitTapHighlightColor: 'transparent',
                position: 'relative',
                scrollSnapAlign: 'center',
              }}
            >
              <Icon size={21} />
              {isAi && hasSelection && (
                <span style={{
                  position: 'absolute', top: 7, right: '50%', marginRight: -14,
                  width: 5, height: 5, borderRadius: '50%',
                  background: 'var(--accent)',
                }} />
              )}
            </button>
          )
        })}
      </div>

      {/* ── 二级：Markdown 格式（默认收起，展开时占一行且可横滚） ── */}
      {formatOpen && (
        <div style={{
          display: 'flex',
          gap: 4,
          padding: '2px 8px 8px',
          overflowX: 'auto',
          scrollbarWidth: 'none',
        }}>
          {FORMATS.map(fmt => {
            const Icon = fmt.Icon
            return (
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
                  flex: '0 0 auto',
                  width: 42,
                  height: 36,
                  border: 'none',
                  borderRadius: 10,
                  background: 'var(--bg-primary)',
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                  WebkitTapHighlightColor: 'transparent',
                }}
              >
                <Icon size={19} />
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}
