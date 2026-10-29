// Extracted Chinese AI prompt for QuickSearchModal — keeps prompt text out of
// the i18n scanner since these are AI instructions, not user-visible UI.

/** Build a prompt that asks an LLM to generate structured Markdown note content. */
export function buildQuickCreateNotePrompt(text: string): string {
  return `根据主题"${text}"生成一份结构清晰的笔记内容，使用 Markdown 格式，包含标题、要点列表和简短说明。内容要实用、有条理。`
}
