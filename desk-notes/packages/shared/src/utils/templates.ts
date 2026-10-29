export interface NoteTemplate {
  id: string
  label: string
  icon: string
  title: string
  content: string
  source: 'builtin' | 'custom'
}

const STORAGE_KEY = 'desk-notes-custom-templates'

export const builtinTemplates: NoteTemplate[] = [
  {
    id: 'meeting',
    label: '会议记录',
    icon: 'fa-people-group',
    title: '📅 会议记录',
    source: 'builtin',
    content: `<h1>会议主题</h1><p><b>时间：</b><br><b>参与人：</b></p><h2>议程</h2><ol><li></li><li></li></ol><h2>决议</h2><ul><li></li></ul>`,
  },
  {
    id: 'weekly',
    label: '周报',
    icon: 'fa-calendar-week',
    title: '📊 本周工作',
    source: 'builtin',
    content: `<h1>本周工作</h1><h2>完成</h2><ul><li></li></ul><h2>进行中</h2><ul><li></li></ul><h2>问题</h2><ul><li></li></ul>`,
  },
  {
    id: 'idea',
    label: '灵感捕捉',
    icon: 'fa-lightbulb',
    title: '💡 灵感',
    source: 'builtin',
    content: `<h1>灵感</h1><h2>想法</h2><p><br></p><h2>为什么重要</h2><p><br></p><h2>下一步</h2><ul><li></li></ul>`,
  },
  {
    id: 'reading',
    label: '读书笔记',
    icon: 'fa-book-open',
    title: '📖 读书笔记',
    source: 'builtin',
    content: `<h1>书名</h1><p><b>作者：</b></p><h2>核心观点</h2><ul><li></li></ul><h2>摘录</h2><p><br></p><h2>感想</h2><p><br></p>`,
  },
  {
    id: 'todo-list',
    label: '待办清单',
    icon: 'fa-list-check',
    title: '📋 待办清单',
    source: 'builtin',
    content: `<h1>待办清单</h1><ul><li><input type="checkbox"> </li><li><input type="checkbox"> </li><li><input type="checkbox"> </li></ul>`,
  },
]

function loadCustomTemplates(): NoteTemplate[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) return JSON.parse(raw)
  } catch { /* ignore */ }
  return []
}

function saveCustomTemplates(templates: NoteTemplate[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(templates))
  } catch { /* ignore */ }
}

export function getAllTemplates(): NoteTemplate[] {
  const custom = loadCustomTemplates()
  return [...builtinTemplates, ...custom]
}

export function getTemplateById(id: string): NoteTemplate | null {
  return getAllTemplates().find(t => t.id === id) || null
}

export function isBuiltin(id: string): boolean {
  return builtinTemplates.some(t => t.id === id)
}

export function createCustomTemplate(
  label: string,
  title: string,
  content: string,
  icon = 'fa-file-pen',
): NoteTemplate {
  const custom = loadCustomTemplates()
  const template: NoteTemplate = {
    id: `custom-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    label,
    icon,
    title,
    content,
    source: 'custom',
  }
  custom.push(template)
  saveCustomTemplates(custom)
  return template
}

export function updateCustomTemplate(
  id: string,
  data: Partial<Pick<NoteTemplate, 'label' | 'icon' | 'title' | 'content'>>,
): boolean {
  if (isBuiltin(id)) return false
  const custom = loadCustomTemplates()
  const idx = custom.findIndex(t => t.id === id)
  if (idx === -1) return false
  custom[idx] = { ...custom[idx], ...data }
  saveCustomTemplates(custom)
  return true
}

export function deleteCustomTemplate(id: string): boolean {
  if (isBuiltin(id)) return false
  const custom = loadCustomTemplates()
  const filtered = custom.filter(t => t.id !== id)
  if (filtered.length === custom.length) return false
  saveCustomTemplates(filtered)
  return true
}

