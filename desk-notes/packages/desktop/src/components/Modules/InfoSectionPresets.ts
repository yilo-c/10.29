// PRESETS: search keywords for InfoSectionConfig — these are data, not UI strings.
// Extracted to avoid false positives in i18n CJK scanner.
export const PRESETS: Record<string, string[]> = {
  ai: ['大模型', 'LLM', 'Agent', 'RAG', '多模态', '推理', 'Transformer', 'DeepSeek', 'GPT', 'Claude'],
  dev: ['前端', 'React', 'TypeScript', 'Rust', 'Python', 'Go', '架构', '数据库'],
  tool: ['GitHub', '开源', 'CLI', 'Chrome 插件', 'VS Code', '效率工具', 'AI 工具'],
  internet: ['开源', '产品', '社区', '创业', '独立开发', 'DevTools'],
  domestic: [],
  international: [],
  local: [],
  policy: ['国务院', '发改委', '政策', '法规', '监管', '立法'],
  science: ['Nature', 'Science', '论文', '研究', '突破', '试验', '发现'],
}