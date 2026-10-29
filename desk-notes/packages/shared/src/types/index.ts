/* ── 奇门遁甲事件类型 ── */
export type QimenDomain = 'career' | 'wealth' | 'relationship' | 'travel' | 'study' | 'life' | 'other'

export interface QimenEvent {
  domain: QimenDomain
  scenario?: string
  description?: string
}

export * from './info'

export const QIMEN_DOMAINS = {
  career: { label: '事业', icon: 'fa-briefcase', scenarios: ['jobInterview', 'salary', 'signing', 'bid', 'promotion', 'startup', 'team'] },
  wealth: { label: '求财', icon: 'fa-coins', scenarios: ['invest', 'debt', 'cooperate', 'stock', 'project'] },
  relationship: { label: '感情', icon: 'fa-heart', scenarios: ['date', 'matchmake', 'marry', 'reconcile'] },
  travel: { label: '出行', icon: 'fa-plane', scenarios: ['travel', 'move', 'abroad', 'drive'] },
  study: { label: '学业', icon: 'fa-graduation-cap', scenarios: ['exam', 'schoolInterview', 'school', 'defense'] },
  life: { label: '生活', icon: 'fa-shield-virus', scenarios: ['find', 'lawsuit', 'medical', 'renovate', 'meeting'] },
  other: { label: '其他', icon: 'fa-ellipsis', scenarios: [] },
} as const

export type QimenDomainKey = keyof typeof QIMEN_DOMAINS

export interface TodoItem {
  id: string
  text: string
  done: boolean
  createdAt: number
  /**
   * Last modification timestamp. Required for cross-device sync to do
   * proper updatedAt-based merging (P0-9 / H15 fix). Optional for backward
   * compat with persisted state from older versions; treat missing as
   * `createdAt`.
   */
  updatedAt?: number
  category?: string
  priority?: 0 | 1 | 2
  dueDate?: number
  sortOrder?: number
  completedAt?: number
  parentId?: string
  repeatInterval?: number
  reminderTime?: number
  reminderSnoozedUntil?: number
  qimenEvent?: QimenEvent
  isEvent?: boolean
  /** Study subject: politics | english | math | mechanics | agriculture */
  subject?: string
  /** Study activity type */
  studyType?: 'study' | 'practice' | 'review' | 'memorize'
  /** Estimated study time in minutes */
  estimatedMinutes?: number
}

export interface Folder {
  id: string
  name: string
  parentId?: string
  sortOrder: number
  createdAt: number
}

export interface FloatingNote {
  id: string
  type: 'text' | 'todo'
  title: string
  content: string
  todos?: TodoItem[]
  x: number
  y: number
  width: number
  height: number
  zIndex: number
  opacity?: number
  color?: string
  pinned?: boolean
  locked?: boolean
  collapsed?: boolean
  archived?: boolean
  tags?: string[]
  folderId?: string
  createdAt?: number
  updatedAt?: number
  refCount?: number
  pomodoroCompleted?: number
  floated?: boolean
  fontSize?: number
  contentViewMode?: 'edit' | 'preview' | 'split'
  /**
   * 编辑器模式标记（手机端为主）。
   * 'markdown' = 按 Markdown 渲染/编辑；不填按旧纯文本处理。
   * 与 `contentViewMode` 是两个维度：后者控制预览/编辑分栏，本字段控制正文格式。
   * 2026-10 手机端笔记统一为 Markdown 后引入（见 思忆集手机端app/docs/11-编辑器统一为Markdown-v1.md）。
   */
  editMode?: 'markdown' | 'plain'
  /**
   * @deprecated 2026-09-19 U0 治本：读即迁移机件已整体移除，本字段不再被任何代码读取
   * （历史：迁移期格式标记，契约 §1.3）。保留键名仅为已落盘数据兼容，勿在新代码使用。
   */
  contentFormat?: 'md' | 'html-legacy'
  /** AI 生成的一句话摘要 */
  summary?: string
  /**
   * V1-4 乐观锁：后端 personal_note_repository 每次 update/restore 自增的 version。
   * 前端把它当作写入基线（expected version），用于检测并发覆盖。
   */
  version?: number
}

export interface AIConfigProfile {
  id: string
  name: string
  apiUrl: string
  model: string
  protocol: 'ollama' | 'openai'
}

/** A model available on the backend, returned by model.list RPC */
export interface ModelInfo {
  provider: string
  model: string
  api_base?: string
}

export interface PomodoroState {
  active: boolean
  remaining: number
  startedAt?: number
  completed: number
}

export type ViewMode = 'list' | 'calendar' | 'kanban'

export interface CanvasState {
  offsetX: number
  offsetY: number
  zoom: number
}

export interface AIConfig {
  enabled: boolean
  apiUrl: string
  model: string
  protocol: 'ollama' | 'openai'
  apiKey?: string
  autoTagging: boolean
  /** AI strategy: online only, offline rule-based, hybrid auto-fallback, or ws (backend WebSocket) */
  strategy?: 'online' | 'offline' | 'hybrid' | 'ws'
}

export interface Category {
  id: string
  label: string
  icon: string
  color: string
}

/** 用户八字/命理信息 */
export interface UserProfile {
  /** 公历出生日期（毫秒时间戳） */
  birthDate: number | null
  /** 出生时辰（0-23，null 表示未知） */
  birthHour: number | null
  /** 性别 */
  gender: 'male' | 'female' | null
  /** 所在城市（用于天气定位） */
  city?: string
}

/** Proactive suggestion from the AI engine */
export interface ProactiveSuggestion {
  id: string
  icon: string
  title: string
  description: string
  /** UI action type */
  actionType?: 'navigate' | 'create_note' | 'open_quick_capture'
  /** Payload for the action (module name, prefilled text, etc.) */
  actionPayload?: string
  /** When this suggestion was generated */
  timestamp: number
  /** 0-100 priority */
  priority: number
}

/** Agent 工作过程事件（工具调用/子任务/计划等）——持久化到 AI 消息用于回溯时间线 */
export interface ChatAgentEventRecord {
  type: string
  data: string
}

/** Chat message within a conversation */
export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  /** AI 生成过程 agent 事件（仅 assistant 消息携带；本地 store.json 持久化，仍在可回溯时间线） */
  agentEvents?: ChatAgentEventRecord[]
  /** AI 生成总耗时（秒），用于时间线摘要 */
  durationSec?: number
}

/** A persisted conversation */
export interface Conversation {
  id: string
  title: string
  createdAt: number
  updatedAt: number
  messages: ChatMessage[]
}

/** A user-saved custom color theme that overrides CSS variables */
export interface CustomTheme {
  id: string
  name: string
  /** ISO timestamp when saved */
  createdAt: number
  colors: {
    panelBg: string
    panelBgSolid: string
    textPrimary: string
    textSecondary: string
    accent: string
    borderColor: string
    bodyBg: string
    success?: string
    warning?: string
    danger?: string
    accentWeak?: string
    hoverBg?: string
    overlayBg?: string
  }
}

export interface GraphNode {
  id: string
  label: string
  type: string
  type_display?: string
  group?: number
  /** 来源主题（后端 properties.topic，如"对话 a6f03bf3"），用于思维导图归类 */
  topic?: string
  /** 节点业务类型：knowledge_card / topic_moc / 其余普通实体 */
  kind?: string
  /**
   * V1-5 图谱下钻：后端实体 properties 原样透传（source_id / url / is_memory / topic / kind …）。
   * 之前在三处 service 转换点被丢弃，导致节点详情面板「点了没东西」。
   */
  properties?: Record<string, unknown>
}

export interface GraphEdge {
  source: string
  target: string
  label?: string
  type?: string
  relation_type?: string
  relation_type_display?: string
}

export interface KnowledgeGraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/** 原子知识卡片（后端 knowledge_synthesis.knowledge.card） */
export interface KnowledgeCard {
  id: string
  title: string
  content: string
  card_topic?: string
  entities?: string[]
  created_at?: string
  /** 详细解释（知识细化整理新增） */
  detail?: string
  /** 相关拓展（知识细化整理新增） */
  expansion?: string
  /** 再分析（知识细化整理新增） */
  analysis?: string
}

/** 主题 MOC（知识地图，后端 knowledge_synthesis.knowledge.moc） */
export interface TopicMOC {
  id: string
  title: string
  content?: string
  card_topic?: string
  card_ids?: string[]
  related_topics?: string[]
  card_count?: number
  created_at?: string
}
