import type { AIProvider, AIAction } from './types'
import type { AIConfig } from '../../types'
import { DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL } from './constants'

const TAG_KEYWORDS: [string, string[]][] = [
  ['工作', ['工作', '项目', '任务', '汇报', '会议', '客户', '面试', '招聘', '绩效']],
  ['学习', ['学习', '读书', '课程', '考试', '复习', '笔记', '知识', '教程']],
  ['生活', ['生活', '日常', '购物', '家务', '饮食', '健康', '运动']],
  ['技术', ['代码', '编程', 'bug', '前端', '后端', 'API', '数据库', 'Git', '部署']],
  ['写作', ['写作', '文章', '博客', '文案', '创作', '编辑', '内容']],
  ['想法', ['想法', '灵感', '创意', '点子', '计划']],
  ['旅行', ['旅行', '出差', '机票', '酒店', '行程', '签证']],
  ['财务', ['财务', '预算', '报销', '薪资', '投资', '账单']],
  ['健康', ['健康', '运动', '健身', '饮食', '睡眠', '医疗', '体检']],
  ['社交', ['社交', '朋友', '聚会', '约会', '联系']],
]

const INTENT_PATTERNS: [RegExp, 'create_note' | 'create_todo' | 'query'][] = [
  [/记住|记录|记下|保存|创建.*(?:笔记|便签|备忘)|新建.*(?:笔记|便签)/, 'create_note'],
  [/提醒|待办|任务|需要做|要做|记得做|安排|计划.*做|创建.*待办/, 'create_todo'],
  [/[？?]|什么|如何|怎样|为什么|查询|搜索|查找/, 'query'],
  [/查找|搜索|找.*(?:笔记|便签|内容)/, 'query'],
  [/添加|增加|新增.*(?:待办|任务|事项)/, 'create_todo'],
  [/写.*(?:笔记|便签|备忘)|记.*东西/, 'create_note'],
]

const ACTION_FALLBACKS: Record<string, (text: string) => string> = {
  summarize: (text) => {
    const clean = text.replace(/<[^>]*>/g, '').trim()
    if (clean.length <= 100) return clean
    return clean.slice(0, 97) + '...'
  },
  organize: (text) => {
    const clean = text.replace(/<[^>]*>/g, '').trim()
    return clean.split('\n').filter(Boolean).join('\n\n')
  },
  polish: (text) => {
    const clean = text.replace(/<[^>]*>/g, '').trim()
    return clean
  },
}

const OFFLINE_MESSAGE = 'AI 当前处于离线模式，实时生成功能不可用。连接网络后可获得 AI 辅助。'

const DEFAULT_CONFIG: AIConfig = {
  enabled: false,
  apiUrl: DEFAULT_LOCAL_API_URL,
  model: DEFAULT_LOCAL_MODEL,
  protocol: 'ollama',
  autoTagging: false,
}

export class RuleAIProvider implements AIProvider {
  private config: AIConfig

  constructor(config?: Partial<AIConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config }
  }

  updateConfig(config: Partial<AIConfig>): void {
    this.config = { ...this.config, ...config }
  }

  getConfig(): AIConfig {
    return { ...this.config }
  }
  async processText(
    _action: AIAction,
    text: string,
    onChunk: (text: string) => void,
  ): Promise<string> {
    const fallback = ACTION_FALLBACKS[_action]
    if (fallback) {
      const result = fallback(text)
      onChunk(result)
      return result
    }
    onChunk(OFFLINE_MESSAGE)
    return OFFLINE_MESSAGE
  }

  async processAction(
    action: AIAction,
    text: string,
    _signal?: AbortSignal,
  ): Promise<string> {
    return this.processText(action, text, () => {})
  }

  async suggestTags(content: string, _existingTags: string[]): Promise<string[]> {
    const plainText = content
      .replace(/<[^>]*>/g, '')
      .replace(/&nbsp;/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase()

    if (!plainText) return []

    const matched: string[] = []
    for (const [tag, keywords] of TAG_KEYWORDS) {
      if (keywords.some(kw => plainText.includes(kw))) {
        matched.push(tag)
      }
    }

    return matched.slice(0, 5)
  }

  async parseIntent(input: string): Promise<{
    type: 'create_note' | 'create_todo' | 'query' | 'unknown'
    title?: string
    content?: string
    tags?: string[]
    dueDate?: number
  }> {
    for (const [pattern, type] of INTENT_PATTERNS) {
      if (pattern.test(input)) {
        const title = input.replace(pattern, '').trim() || undefined
        return { type, title, content: title }
      }
    }
    return { type: 'unknown' }
  }

  async ask(_question: string, context: string[]): Promise<string> {
    if (context.length > 0) {
      return `基于以下笔记内容，我找到 ${context.length} 条相关记录。建议联网后使用 AI 获取更精确的回答。`
    }
    return '未找到相关笔记内容。'
  }
}
