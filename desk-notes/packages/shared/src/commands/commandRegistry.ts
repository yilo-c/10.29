/**
 * 快捷命令系统 — 类似 Raycast/Alfred 的快速命令
 * 
 * 功能：
 * - 命令注册和发现
 * - 快捷键绑定
 * - 模糊搜索
 * - 命令分类
 * - 动态命令（基于上下文）
 */

// 命令类型
export type CommandType = 'action' | 'navigation' | 'search' | 'ai' | 'system'

// 命令上下文
export interface CommandContext {
  selectedNoteId?: string
  selectedTaskId?: string
  currentView?: string
  searchText?: string
  [key: string]: unknown
}

// 命令定义
export interface Command {
  id: string
  name: string
  description?: string
  icon?: string
  type: CommandType
  category: string
  shortcut?: string
  keywords?: string[]
  action: (ctx: CommandContext) => Promise<void> | void
  isVisible?: (ctx: CommandContext) => boolean
  isDisabled?: (ctx: CommandContext) => boolean
}

// 命令结果
export interface CommandResult {
  command: Command
  score: number
  matchedKeywords: string[]
  matchType: 'exact' | 'prefix' | 'contains' | 'fuzzy' | 'pinyin' | 'typo'
}

// 拼音首字母映射表（常用汉字拼音首字母）
const PINYIN_MAP: Record<string, string> = {
  '打': 'd', '开': 'k', '笔': 'b', '记': 'j', '任': 'r', '务': 'w',
  '搜': 's', '索': 's', '查': 'c', '找': 'z', '对': 'd', '话': 'h',
  '聊': 'l', '天': 't', '设': 's', '置': 'z', '配': 'p', '新': 'x',
  '建': 'j', '创': 'c', '切': 'q', '换': 'h', '主': 'z', '题': 't',
  '亮': 'l', '暗': 'a', '色': 's', '重': 'c', '载': 'z', '刷': 's',
  '知': 'z', '识': 's', '库': 'k', '参': 'c', '考': 'k',
}

/**
 * 命令注册表
 */
export class CommandRegistry {
  private commands = new Map<string, Command>()
  private shortcuts = new Map<string, string>() // shortcut -> commandId
  private categories = new Map<string, Set<string>>() // category -> commandIds

  /**
   * 注册命令
   */
  register(command: Command): () => void {
    // 检查重复
    if (this.commands.has(command.id)) {
      console.warn(`[CommandRegistry] Command '${command.id}' already registered, overwriting`)
    }

    this.commands.set(command.id, command)

    // 注册快捷键
    if (command.shortcut) {
      this.shortcuts.set(command.shortcut, command.id)
    }

    // 注册分类
    if (!this.categories.has(command.category)) {
      this.categories.set(command.category, new Set())
    }
    this.categories.get(command.category)!.add(command.id)

    // 返回取消注册函数
    return () => this.unregister(command.id)
  }

  /**
   * 取消注册命令
   */
  unregister(commandId: string): void {
    const command = this.commands.get(commandId)
    if (!command) return

    this.commands.delete(commandId)

    if (command.shortcut) {
      this.shortcuts.delete(command.shortcut)
    }

    const category = this.categories.get(command.category)
    if (category) {
      category.delete(commandId)
      if (category.size === 0) {
        this.categories.delete(command.category)
      }
    }
  }

  /**
   * 获取命令
   */
  get(commandId: string): Command | undefined {
    return this.commands.get(commandId)
  }

  /**
   * 通过快捷键获取命令
   */
  getByShortcut(shortcut: string): Command | undefined {
    const commandId = this.shortcuts.get(shortcut)
    return commandId ? this.commands.get(commandId) : undefined
  }

  /**
   * 获取所有命令
   */
  getAll(): Command[] {
    return Array.from(this.commands.values())
  }

  /**
   * 获取所有分类
   */
  getCategories(): string[] {
    return Array.from(this.categories.keys())
  }

  /**
   * 获取分类下的命令
   */
  getCommandsByCategory(category: string): Command[] {
    const commandIds = this.categories.get(category)
    if (!commandIds) return []
    return Array.from(commandIds)
      .map(id => this.commands.get(id))
      .filter((c): c is Command => c !== undefined)
  }

  /**
   * 搜索命令（模糊匹配）
   */
  search(query: string, ctx: CommandContext = {}): CommandResult[] {
    if (!query.trim()) {
      // 返回所有可见命令
      return this.getAll()
        .filter(cmd => !cmd.isVisible || cmd.isVisible(ctx))
        .map(cmd => ({ command: cmd, score: 0, matchedKeywords: [], matchType: 'exact' as const }))
    }

    const normalizedQuery = query.toLowerCase().trim()
    const results: CommandResult[] = []

    for (const command of this.commands.values()) {
      // 检查可见性
      if (command.isVisible && !command.isVisible(ctx)) {
        continue
      }

      const { score, matchedKeywords, matchType } = this.calculateScore(command, normalizedQuery)
      
      if (score > 0) {
        results.push({ command, score, matchedKeywords, matchType })
      }
    }

    // 按分数排序
    return results.sort((a, b) => b.score - a.score)
  }

  /**
   * 执行命令
   */
  async execute(commandId: string, ctx: CommandContext = {}): Promise<boolean> {
    const command = this.commands.get(commandId)
    if (!command) {
      console.warn(`[CommandRegistry] Command '${commandId}' not found`)
      return false
    }

    // 检查是否禁用
    if (command.isDisabled && command.isDisabled(ctx)) {
      console.log(`[CommandRegistry] Command '${commandId}' is disabled`)
      return false
    }

    try {
      await command.action(ctx)
      return true
    } catch (error) {
      console.error(`[CommandRegistry] Command '${commandId}' failed:`, error)
      return false
    }
  }

  /**
   * 通过快捷键执行命令
   */
  async executeByShortcut(shortcut: string, ctx: CommandContext = {}): Promise<boolean> {
    const command = this.getByShortcut(shortcut)
    if (!command) return false
    return this.execute(command.id, ctx)
  }

  /**
   * 计算命令匹配分数
   */
  private calculateScore(command: Command, query: string): { 
    score: number
    matchedKeywords: string[]
    matchType: 'exact' | 'prefix' | 'contains' | 'fuzzy' | 'pinyin' | 'typo'
  } {
    let score = 0
    const matchedKeywords: string[] = []
    let matchType: 'exact' | 'prefix' | 'contains' | 'fuzzy' | 'pinyin' | 'typo' = 'fuzzy'

    const name = command.name.toLowerCase()
    const description = (command.description || '').toLowerCase()
    const keywords = (command.keywords || []).map(k => k.toLowerCase())

    // 完全匹配名称
    if (name === query) {
      score += 100
      matchedKeywords.push(command.name)
      matchType = 'exact'
    }
    // 名称以查询开头
    else if (name.startsWith(query)) {
      score += 80
      matchedKeywords.push(command.name)
      matchType = 'prefix'
    }
    // 名称包含查询
    else if (name.includes(query)) {
      score += 60
      matchedKeywords.push(command.name)
      matchType = 'contains'
    }

    // 描述包含查询
    if (description.includes(query)) {
      score += 30
      matchedKeywords.push(command.description || '')
      if (matchType === 'fuzzy') matchType = 'contains'
    }

    // 关键词匹配
    for (const keyword of keywords) {
      if (keyword === query) {
        score += 90
        matchedKeywords.push(keyword)
        matchType = 'exact'
      } else if (keyword.startsWith(query)) {
        score += 70
        matchedKeywords.push(keyword)
        if (matchType !== 'exact') matchType = 'prefix'
      } else if (keyword.includes(query)) {
        score += 50
        matchedKeywords.push(keyword)
        if (matchType === 'fuzzy') matchType = 'contains'
      }
    }

    // 拼音首字母匹配（如 "dkbj" 匹配 "打开笔记"）
    if (score === 0) {
      const pinyinScore = this.matchPinyin(name, query)
      if (pinyinScore > 0) {
        score += pinyinScore
        matchedKeywords.push(command.name)
        matchType = 'pinyin'
      }
    }

    // 编辑距离匹配（容错，如 "笔记" 匹配 "笔计"）
    if (score === 0 && query.length >= 2) {
      const typoScore = this.matchWithTypo(name, query)
      if (typoScore > 0) {
        score += typoScore
        matchedKeywords.push(command.name)
        matchType = 'typo'
      }
    }

    // 模糊匹配（字符顺序匹配）
    if (score === 0 && this.fuzzyMatch(name, query)) {
      score += 40
      matchedKeywords.push(command.name)
    }

    return { score, matchedKeywords, matchType }
  }

  /**
   * 拼音首字母匹配
   * 例如: "dkbj" -> "打开笔记"
   */
  private matchPinyin(text: string, query: string): number {
    // 提取文本中每个字符的拼音首字母
    const initials: string[] = []
    for (const char of text) {
      if (PINYIN_MAP[char]) {
        initials.push(PINYIN_MAP[char])
      } else if (/[a-z]/i.test(char)) {
        initials.push(char.toLowerCase())
      }
    }
    
    const initialsStr = initials.join('')
    
    // 完全匹配拼音首字母
    if (initialsStr === query) {
      return 85
    }
    // 拼音首字母以查询开头
    if (initialsStr.startsWith(query)) {
      return 65
    }
    // 拼音首字母包含查询
    if (initialsStr.includes(query)) {
      return 45
    }
    
    return 0
  }

  /**
   * 编辑距离匹配（容错）
   * 允许1-2个字符的编辑距离
   */
  private matchWithTypo(text: string, query: string): number {
    // 只处理较短的查询
    if (query.length > 10 || text.length > 20) return 0
    
    const distance = this.editDistance(text, query)
    
    // 编辑距离为1且查询长度>=2
    if (distance === 1 && query.length >= 2) {
      return 55
    }
    // 编辑距离为2且查询长度>=3
    if (distance === 2 && query.length >= 3) {
      return 35
    }
    
    return 0
  }

  /**
   * 计算编辑距离（Levenshtein distance）
   */
  private editDistance(a: string, b: string): number {
    const m = a.length
    const n = b.length
    
    // 优化：如果长度差超过2，直接返回
    if (Math.abs(m - n) > 2) return Math.max(m, n)
    
    const dp: number[][] = Array(m + 1).fill(null).map(() => Array(n + 1).fill(0))
    
    for (let i = 0; i <= m; i++) dp[i][0] = i
    for (let j = 0; j <= n; j++) dp[0][j] = j
    
    for (let i = 1; i <= m; i++) {
      for (let j = 1; j <= n; j++) {
        if (a[i - 1] === b[j - 1]) {
          dp[i][j] = dp[i - 1][j - 1]
        } else {
          dp[i][j] = 1 + Math.min(
            dp[i - 1][j],     // 删除
            dp[i][j - 1],     // 插入
            dp[i - 1][j - 1]  // 替换
          )
        }
      }
    }
    
    return dp[m][n]
  }

  /**
   * 模糊匹配（字符顺序匹配）
   */
  private fuzzyMatch(text: string, query: string): boolean {
    let queryIndex = 0
    for (let i = 0; i < text.length && queryIndex < query.length; i++) {
      if (text[i] === query[queryIndex]) {
        queryIndex++
      }
    }
    return queryIndex === query.length
  }

  /**
   * 获取快捷键列表
   */
  getShortcuts(): Map<string, Command> {
    const result = new Map<string, Command>()
    for (const [shortcut, commandId] of this.shortcuts) {
      const command = this.commands.get(commandId)
      if (command) {
        result.set(shortcut, command)
      }
    }
    return result
  }

  /**
   * 清空所有命令
   */
  clear(): void {
    this.commands.clear()
    this.shortcuts.clear()
    this.categories.clear()
  }
}

// 全局实例
let _registry: CommandRegistry | null = null

/**
 * 获取命令注册表实例
 */
export function getCommandRegistry(): CommandRegistry {
  if (!_registry) {
    _registry = new CommandRegistry()
  }
  return _registry
}

/**
 * 重置命令注册表
 */
export function resetCommandRegistry(): void {
  if (_registry) {
    _registry.clear()
    _registry = null
  }
}

// 内置命令定义
export const BUILTIN_COMMANDS = {
  // 导航命令
  GO_TO_NOTES: {
    id: 'navigation.notes',
    name: '打开笔记',
    description: '跳转到笔记页面',
    icon: '📝',
    type: 'navigation' as CommandType,
    category: '导航',
    shortcut: 'Ctrl+1',
    keywords: ['笔记', '记录', 'notes'],
  },
  GO_TO_TASKS: {
    id: 'navigation.tasks',
    name: '打开任务',
    description: '跳转到任务页面',
    icon: '✅',
    type: 'navigation' as CommandType,
    category: '导航',
    shortcut: 'Ctrl+2',
    keywords: ['任务', '待办', 'tasks', 'todo'],
  },
  GO_TO_CHAT: {
    id: 'navigation.chat',
    name: '打开 AI 对话',
    description: '跳转到 AI 对话页面',
    icon: '💬',
    type: 'navigation' as CommandType,
    category: '导航',
    shortcut: 'Ctrl+3',
    keywords: ['对话', '聊天', 'AI', 'chat'],
  },
  GO_TO_REFERENCE: {
    id: 'navigation.reference',
    name: '打开知识库',
    description: '跳转到知识库页面',
    icon: '📚',
    type: 'navigation' as CommandType,
    category: '导航',
    shortcut: 'Ctrl+4',
    keywords: ['知识库', '参考', 'reference'],
  },
  GO_TO_SETTINGS: {
    id: 'navigation.settings',
    name: '打开设置',
    description: '跳转到设置页面',
    icon: '⚙️',
    type: 'navigation' as CommandType,
    category: '导航',
    shortcut: 'Ctrl+,',
    keywords: ['设置', '配置', 'settings'],
  },

  // 操作命令
  NEW_NOTE: {
    id: 'action.newNote',
    name: '新建笔记',
    description: '创建一条新笔记',
    icon: '➕',
    type: 'action' as CommandType,
    category: '操作',
    shortcut: 'Ctrl+N',
    keywords: ['新建', '创建', '笔记', 'new', 'create'],
  },
  NEW_TASK: {
    id: 'action.newTask',
    name: '新建任务',
    description: '创建一条新任务',
    icon: '➕',
    type: 'action' as CommandType,
    category: '操作',
    shortcut: 'Ctrl+T',
    keywords: ['新建', '创建', '任务', '待办', 'new', 'create'],
  },
  SEARCH: {
    id: 'action.search',
    name: '搜索',
    description: '全局搜索',
    icon: '🔍',
    type: 'search' as CommandType,
    category: '操作',
    shortcut: 'Ctrl+K',
    keywords: ['搜索', '查找', 'search', 'find'],
  },
  AI_CHAT: {
    id: 'action.aiChat',
    name: 'AI 对话',
    description: '打开 AI 对话',
    icon: '🤖',
    type: 'ai' as CommandType,
    category: 'AI',
    shortcut: 'Ctrl+J',
    keywords: ['AI', '对话', '聊天', 'chat'],
  },

  // 系统命令
  TOGGLE_THEME: {
    id: 'system.toggleTheme',
    name: '切换主题',
    description: '切换亮色/暗色主题',
    icon: '🌓',
    type: 'system' as CommandType,
    category: '系统',
    keywords: ['主题', '亮色', '暗色', 'theme', 'dark', 'light'],
  },
  RELOAD: {
    id: 'system.reload',
    name: '重新加载',
    description: '重新加载应用',
    icon: '🔄',
    type: 'system' as CommandType,
    category: '系统',
    shortcut: 'Ctrl+Shift+R',
    keywords: ['重载', '刷新', 'reload', 'refresh'],
  },
}
