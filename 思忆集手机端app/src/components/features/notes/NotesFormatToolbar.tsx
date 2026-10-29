import { useCallback } from 'react'

interface NotesFormatToolbarProps {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>
  content: string
  onChange: (val: string) => void
}

interface ToolDef {
  label: string
  title: string
  before: string
  after: string
  block: boolean
}

const TOOLS: ToolDef[] = [
  { label: 'B', title: '粗体', before: '**', after: '**', block: false },
  { label: 'I', title: '斜体', before: '*', after: '*', block: false },
  { label: 'H1', title: '一级标题', before: '# ', after: '', block: true },
  { label: 'H2', title: '二级标题', before: '## ', after: '', block: true },
  { label: '≡', title: '无序列表', before: '- ', after: '', block: true },
  { label: '☐', title: '待办', before: '- [ ] ', after: '', block: true },
  { label: '❝', title: '引用', before: '> ', after: '', block: true },
  { label: '—', title: '分割线', before: '\n---\n', after: '', block: false },
]

export default function NotesFormatToolbar({ textareaRef, content, onChange }: NotesFormatToolbarProps) {
  const handleInsert = useCallback((tool: ToolDef) => {
    const ta = textareaRef.current
    if (!ta) return

    const start = ta.selectionStart
    const end = ta.selectionEnd
    const val = content

    if (tool.block && tool.before !== '\n---\n') {
      // Block-level: insert prefix at current line start
      const lineStart = val.lastIndexOf('\n', start - 1) + 1
      const newVal = val.slice(0, lineStart) + tool.before + val.slice(lineStart)
      onChange(newVal)
      setTimeout(() => {
        ta.focus()
        const pos = lineStart + tool.before.length
        ta.setSelectionRange(pos, pos)
      }, 0)
    } else {
      // Inline: wrap selection with markers
      const selected = val.substring(start, end) || '文字'
      const newVal = val.slice(0, start) + tool.before + selected + tool.after + val.slice(end)
      onChange(newVal)
      setTimeout(() => {
        ta.focus()
        ta.setSelectionRange(start + tool.before.length, start + tool.before.length + selected.length)
      }, 0)
    }
  }, [content, onChange, textareaRef])

  return (
    <div style={{
      display: 'flex',
      gap: 2,
      padding: '4px 2px',
      background: 'var(--bg-tertiary)',
      borderRadius: 'var(--radius-sm)',
      overflowX: 'auto',
      scrollbarWidth: 'none',
      flexShrink: 0,
    }}>
      {TOOLS.map(tool => (
        <button
          key={tool.label}
          title={tool.title}
          onClick={() => handleInsert(tool)}
          onPointerDown={e => e.preventDefault()}
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            minWidth: 36,
            height: 32,
            padding: '0 6px',
            border: 'none',
            borderRadius: 'var(--radius-sm)',
            background: 'transparent',
            color: 'var(--text-secondary)',
            fontSize: 'var(--text-footnote)',
            fontWeight: tool.label === 'B' ? 700 : 400,
            fontStyle: tool.label === 'I' ? 'italic' : 'normal',
            cursor: 'pointer',
            transition: 'background 0.1s ease',
            WebkitTapHighlightColor: 'transparent',
          }}
        >
          {tool.label}
        </button>
      ))}
    </div>
  )
}
