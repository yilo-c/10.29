// System prompt template for AIChatPanel — these are AI instructions, not user-visible UI.
// Extracted to avoid false positives in i18n CJK scanner.
export function buildSystemPrompt(opts: {
  userLabel: string
  assistantLabel: string
  emptyContent: string
  userSelected: string
  noteContent: string
  editorSelectedText?: string
  messages: { role: string; content: string }[]
  lastUserInput: string
}): string {
  const parts: string[] = [
    '你是一个智能笔记助手。你可以通过动作标记直接修改笔记内容。',
    '',
    '可用动作：',
    '- replace_selection：替换用户选中的文本。',
    '- insert_at_cursor：在光标位置插入新内容（无选中时）',
    '- append：在笔记末尾追加内容',
    '- prepend：在笔记开头插入内容。',
    '- replace_full：替换全部笔记内容。',
    '',
    '当用户要求修改笔记时，在回复末尾添加动作标记：',
    '【AI_ACTION】{"action":"replace_selection","text":"替换后的文字"}【/AI_ACTION】',
    '如果是普通对话（提问、闲聊等），不要添加动作标记。',
    '',
    '当前笔记内容：',
    opts.noteContent.replace(/<[^>]*>/g, '').trim() || opts.emptyContent,
  ]

  if (opts.editorSelectedText) {
    parts.push(opts.userSelected)
    parts.push(opts.editorSelectedText)
    parts.push('')
  }

  for (const msg of opts.messages) {
    const prefix = msg.role === 'user' ? opts.userLabel : opts.assistantLabel
    parts.push(`${prefix}：${msg.content}`)
  }
  parts.push('')
  parts.push(`用户：${opts.lastUserInput}`)
  parts.push(`助手：\n`)

  return parts.join('\n')
}