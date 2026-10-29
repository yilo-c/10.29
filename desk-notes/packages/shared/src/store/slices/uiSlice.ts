import type { StateCreator } from 'zustand'
import type { Store } from '../types'
import { AIConfigProfile, CanvasState, CustomTheme, InfoItem, ProactiveSuggestion, UserProfile, ViewMode } from '../../types'
import type { SupportedLocale } from '../../i18n'

export interface PanelState {
  x: number; y: number; pinned: boolean; locked: boolean; opacity: number; color?: string
  visible: boolean
}

export type SortMode = 'created' | 'priority' | 'dueDate' | 'alpha'

export type AggregationStrategy = 'race' | 'vote' | 'best_of_n' | 'cost_first' | 'merge' | 'off'

export type ActiveModule =
  // 核心高频
  | 'dashboard' | 'chat' | 'notes' | 'todo'
  // 日程与收集
  | 'calendar' | 'inbox'
  // 合并项：学习（词库+闪卡）、知识网络（图谱+反向链接）、工具组（翻译+传送+信息+Agent+系统监控+求职猎手）
  | 'learning' | 'knowledge-network' | 'tools'
  // 保留原始项以兼容（点击合并项时仍可路由到子模块）
  | 'wordbank' | 'flashcard' | 'knowledge-graph' | 'knowledge-base' | 'backlinks'
  | 'translate' | 'transfer' | 'info' | 'agent' | 'system-monitor' | 'job-hunting'
  // 2026-08-17 补齐：任务队列 / 用量看板 / 系统管理（进化·审计·时间线）
  | 'task-queue' | 'usage' | 'admin'
  // 2026-08-17 个人知识体系：信息导入 / 画像 / 回顾 / 信息库
  | 'personal'
  // U3 批2 记忆管理面板 2026-09-16
  | 'memory'
  // V1-6 今日回顾（仅由快捷键进入，不入侧边栏导航）
  | 'today-review'
  | 'app-manager'
  | 'notification'
  | 'automation'
  | 'rss'
  | 'settings'

/** 侧边栏分组定义 */
export interface SidebarGroup {
  id: string
  labelKey: string
  icon: string
  /** 组内成员（按顺序） */
  items: ActiveModule[]
  /** 默认是否折叠 */
  defaultCollapsed: boolean
}

/** 单个侧边栏项的运行时配置 */
export interface SidebarItemConfig {
  key: ActiveModule
  visible: boolean
  pinned: boolean
  order: number
  /** 所属分组 id；null = 独立项（顶层显示） */
  groupId: string | null
}

export interface SidebarConfig {
  items: SidebarItemConfig[]
  groups: SidebarGroup[]
  /** 各分组当前是否折叠（key = groupId） */
  collapsed: Record<string, boolean>
}

const INIT_PANEL_X = () => Math.max((typeof window !== 'undefined' ? window.innerWidth : 1280) - 356, 0)

export interface UiSlice {
  panel: PanelState
  searchQuery: string
  noteSearchQuery: string
  searchModalOpen: boolean
  theme: 'dark' | 'black' | 'light'
  sortMode: SortMode
  viewMode: ViewMode
  canvasMode: boolean
  notesViewMode: 'canvas' | 'list'
  canvas: CanvasState
  aiEnabled: boolean
  aiAutoTagging: boolean
  aiProfiles: AIConfigProfile[]
  activeProfileId: string | null
  preferredModelProvider: string | null
  preferredModelName: string | null
  aggregationStrategy: AggregationStrategy
  focusDuration: number
  pomodoroSessionCount: number
  backgroundMode: 'acrylic' | 'pure-white' | 'pure-black'
  locale: SupportedLocale
  savedThemes: CustomTheme[]
  activeThemeId: string | null
  defaultTemplateId: string | null
  searchHighlight: { keyword: string; noteId: string } | null
  filterStatus: 'all' | 'active' | 'completed' | 'overdue'
  filterPriority: number[]
  filterSubject: string
  appPin: string | null
  appLocked: boolean
  panelMode: 'todo' | 'notes'
  editingNoteId: string | null
  activeModule: ActiveModule
  navCollapsed: boolean
  statusBarCollapsed: boolean
  userProfile: UserProfile
  referenceMode: 'simple' | 'full'
  defaultReminder: number
  qimenNavigationTarget: { todoId?: string; domain?: string; scenario?: string; text?: string } | null
  bootPhase: 'idle' | 'booting' | 'ready' | 'failed'
  bootError: string | null
  lastBackupTime: number

  // Proactive AI
  proactiveEnabled: boolean
  proactiveBriefDate: string | null
  proactiveBrief: string
  proactiveBriefLoading: boolean
  proactiveSuggestions: ProactiveSuggestion[]

  // Info Brief
  infoTopics: string[]
  infoSectionKeywords: Record<string, string[]>
  infoItems: InfoItem[]
  infoBrief: string
  infoLoading: boolean
  infoLastFetch: number
  infoError: string | null
  autoInfoEnabled: boolean
  autoInfoInterval: number
  autoInfoLastRun: number

  setSearchModalOpen: (v: boolean) => void
  setSearchQuery: (q: string) => void
  setNoteSearchQuery: (q: string) => void
  setPanelPos: (x: number, y: number) => void
  togglePanelPin: () => void
  togglePanelLock: () => void
  setPanelOpacity: (v: number) => void
  setPanelColor: (c: string | undefined) => void
  setPanelVisible: (v: boolean) => void
  setLastBackupTime: (t: number) => void
  toggleTheme: () => void
  setTheme: (t: 'dark' | 'black' | 'light') => void
  setSortMode: (m: SortMode) => void
  setViewMode: (m: ViewMode) => void
  toggleCanvasMode: () => void
  setNotesViewMode: (m: 'canvas' | 'list') => void
  setCanvas: (c: Partial<CanvasState>) => void
  setBackgroundMode: (m: 'acrylic' | 'pure-white' | 'pure-black') => void
  setLocale: (locale: SupportedLocale) => void
  setAIEnabled: (enabled: boolean) => void
  setAIAutoTagging: (autoTagging: boolean) => void
  addAIProfile: (profile: AIConfigProfile) => void
  updateAIProfile: (id: string, data: Partial<AIConfigProfile>) => void
  deleteAIProfile: (id: string) => void
  setActiveProfileId: (id: string | null) => void
  setPreferredModel: (provider: string | null, model: string | null) => void
  setAggregationStrategy: (strategy: AggregationStrategy) => void
  setReferenceMode: (mode: 'simple' | 'full') => void
  setDefaultReminder: (minutes: number) => void
  setFocusDuration: (minutes: number) => void
  incrementPomodoroSession: () => void
  resetPomodoroSession: () => void
  saveTheme: (name: string, colors: CustomTheme['colors']) => void
  deleteTheme: (id: string) => void
  setActiveTheme: (id: string | null) => void
  setSearchHighlight: (val: { keyword: string; noteId: string } | null) => void
  setDefaultTemplateId: (id: string | null) => void
  setEditingNoteId: (id: string | null) => void
  setPanelMode: (mode: 'todo' | 'notes') => void
  setActiveModule: (module: ActiveModule) => void
  toggleNavCollapsed: () => void
  toggleStatusBarCollapsed: () => void
  setFilterStatus: (s: 'all' | 'active' | 'completed' | 'overdue') => void
  setFilterPriority: (p: number[]) => void
  toggleFilterPriority: (p: number) => void
  setQimenNavigationTarget: (target: { todoId?: string; domain?: string; scenario?: string; text?: string } | null) => void
  setFilterSubject: (s: string) => void
  setAppPin: (pin: string | null) => void
  lockApp: () => void
  unlockApp: () => void
  setUserProfile: (profile: Partial<UserProfile>) => void
  setInfoTopics: (topics: string[]) => void
  setInfoSectionKeywords: (kws: Record<string, string[]>) => void
  setInfoItems: (items: InfoItem[]) => void
  setInfoBrief: (brief: string) => void
  setInfoLoading: (v: boolean) => void
  setInfoLastFetch: (t: number) => void
  setInfoError: (e: string | null) => void
  setAutoInfoEnabled: (v: boolean) => void
  setAutoInfoInterval: (v: number) => void
  setAutoInfoLastRun: (t: number) => void
  setProactiveEnabled: (v: boolean) => void
  setProactiveBriefDate: (d: string | null) => void
  setProactiveBrief: (brief: string) => void
  setProactiveBriefLoading: (v: boolean) => void
  setProactiveSuggestions: (suggestions: ProactiveSuggestion[]) => void
  // 侧边栏自定义
  sidebarConfig: SidebarConfig
  setSidebarConfig: (cfg: SidebarConfig) => void
  toggleSidebarGroup: (groupId: string) => void
  resetSidebarConfig: () => void

  // U4 未配置模块折叠：后端 modules.status RPC 的会话级快照。
  // null = 未知（后端未升级 / 未连接 / 信封失败）→ 所有模块按可用处理，不误伤。
  // 比照 sidebarConfig 不持久化（见 shared/store/useStore.ts TRANSIENT_KEYS）。
  moduleAvailability: Record<string, ModuleStatusInfo> | null
  setModuleAvailability: (m: Record<string, ModuleStatusInfo> | null) => void
}

/** modules.status 单项：模块是否已配置、是否已有数据 */
export interface ModuleStatusInfo {
  configured: boolean
  hasData: boolean
}

export const createUiSlice: StateCreator<Store, [], [], UiSlice> = (set, _get, _api) => ({
    panel: { x: INIT_PANEL_X(), y: 16, pinned: false, locked: false, opacity: 1, visible: true },
    searchQuery: '',
    noteSearchQuery: '',
    searchModalOpen: false,
    theme: 'dark',
    sortMode: 'created' as SortMode,
    viewMode: 'list' as ViewMode,
    canvasMode: false,
    notesViewMode: 'canvas',
    canvas: { offsetX: 0, offsetY: 0, zoom: 1 },
    aiEnabled: true,
    aiAutoTagging: false,
    aiProfiles: [],
    activeProfileId: null,
    preferredModelProvider: null,
    preferredModelName: null,
    aggregationStrategy: 'off',
    focusDuration: 25,
    pomodoroSessionCount: 0,
    backgroundMode: 'acrylic',
    locale: 'zh-CN' as SupportedLocale,
    savedThemes: [],
    activeThemeId: null,
    searchHighlight: null,
    defaultTemplateId: null,
    filterStatus: 'all' as const,
    filterPriority: [],
    filterSubject: '',
    appPin: null,
    appLocked: false,
    panelMode: 'todo',
    editingNoteId: null,
    activeModule: 'dashboard',
    navCollapsed: false,
    statusBarCollapsed: false,
    userProfile: { birthDate: null, birthHour: null, gender: null, city: '' },
    referenceMode: 'simple',
    defaultReminder: 15,
    qimenNavigationTarget: null,
    bootPhase: 'idle',
    bootError: null,
    lastBackupTime: 0,

    // Proactive AI
    proactiveEnabled: true,
    proactiveBriefDate: null,
    proactiveBrief: '',
    proactiveBriefLoading: false,
    proactiveSuggestions: [],

    // Info Brief
    infoTopics: ['AI', '前端开发', '效率工具', '开源项目'],
    infoSectionKeywords: {},
    infoItems: [],
    infoBrief: '',
    infoLoading: false,
    infoLastFetch: 0,
    infoError: null,
    autoInfoEnabled: false,
    autoInfoInterval: 3600,
    autoInfoLastRun: 0,

    // 侧边栏默认配置：核心项独立显示，低频项分组折叠
    sidebarConfig: getDefaultSidebarConfig(),

    // U4 未配置模块折叠：null = 未知，全模块按可用渲染（不误伤）
    moduleAvailability: null,

    setSearchModalOpen: (v) => set({ searchModalOpen: v }),
    setSearchQuery: (q) => set({ searchQuery: q }),
    setNoteSearchQuery: (q) => set({ noteSearchQuery: q }),

    setPanelPos: (x, y) => set((s: UiSlice) => ({ panel: { ...s.panel, x, y } })),
    togglePanelPin: () => set((s: UiSlice) => ({ panel: { ...s.panel, pinned: !s.panel.pinned } })),
    togglePanelLock: () => set((s: UiSlice) => ({ panel: { ...s.panel, locked: !s.panel.locked } })),
    setPanelOpacity: (v) => set((s: UiSlice) => ({ panel: { ...s.panel, opacity: v } })),
    setPanelColor: (c) => set((s: UiSlice) => ({ panel: { ...s.panel, color: c || undefined } })),
    setPanelVisible: (v) => set((s: UiSlice) => ({ panel: { ...s.panel, visible: v } })),

    setLastBackupTime: (t) => set({ lastBackupTime: t }),

    toggleTheme: () => set((s: UiSlice) => {
      const order: Array<'dark' | 'black' | 'light'> = ['dark', 'black', 'light']
      const idx = order.indexOf(s.theme)
      const next = order[(idx + 1) % order.length]
      document.documentElement.setAttribute('data-theme', next)
      return { theme: next }
    }),
    setTheme: (t) => {
      document.documentElement.setAttribute('data-theme', t)
      set({ theme: t })
    },

    setSortMode: (m) => set({ sortMode: m }),
    setViewMode: (m) => set({ viewMode: m }),
    toggleCanvasMode: () => set((s: UiSlice) => ({ canvasMode: !s.canvasMode })),
    setNotesViewMode: (m) => set({ notesViewMode: m }),
    setCanvas: (c) => set((s: UiSlice) => ({ canvas: { ...s.canvas, ...c } })),
    setAIEnabled: (aiEnabled) => set({ aiEnabled }),
    setAIAutoTagging: (aiAutoTagging) => set({ aiAutoTagging }),
    setReferenceMode: (mode) => set({ referenceMode: mode }),
    setDefaultReminder: (minutes) => set({ defaultReminder: minutes }),

    addAIProfile: (profile) => set((s: UiSlice) => ({
      aiProfiles: [...s.aiProfiles, profile],
      activeProfileId: s.activeProfileId ?? profile.id,
    })),
    updateAIProfile: (id, data) => set((s: UiSlice) => ({
      aiProfiles: s.aiProfiles.map(p => p.id === id ? { ...p, ...data } : p),
    })),
    deleteAIProfile: (id) => set((s: UiSlice) => ({
      aiProfiles: s.aiProfiles.filter(p => p.id !== id),
      activeProfileId: s.activeProfileId === id
        ? (s.aiProfiles.find(p => p.id !== id)?.id ?? null)
        : s.activeProfileId,
    })),
    setActiveProfileId: (id) => set({ activeProfileId: id }),
    setPreferredModel: (provider, model) => set({ preferredModelProvider: provider, preferredModelName: model }),
    setAggregationStrategy: (strategy) => set({ aggregationStrategy: strategy }),
    setLocale: (locale) => set({ locale }),

    setBackgroundMode: (m) => set((s: UiSlice) => {
      document.documentElement.setAttribute('data-background', m)
      if (m === 'pure-white') {
        document.documentElement.setAttribute('data-theme', 'light')
      } else if (m === 'pure-black') {
        document.documentElement.setAttribute('data-theme', 'dark')
      } else {
        document.documentElement.setAttribute('data-theme', s.theme)
      }
      const ei = (window as unknown as Record<string, unknown>).electronAPI as { sendBackgroundMode?: (m: string) => void } | undefined
      if (ei?.sendBackgroundMode) ei.sendBackgroundMode(m)
      return { backgroundMode: m }
    }),

    setFocusDuration: (minutes) => set({ focusDuration: minutes }),
    incrementPomodoroSession: () => set((s: UiSlice) => ({ pomodoroSessionCount: s.pomodoroSessionCount + 1 })),
    resetPomodoroSession: () => set({ pomodoroSessionCount: 0 }),

    saveTheme: (name, colors) => set((s: UiSlice) => {
      const existing = s.savedThemes.find(t => t.name === name)
      const theme: CustomTheme = {
        id: existing?.id ?? uid(),
        name,
        createdAt: Date.now(),
        colors,
      }
      return {
        savedThemes: existing
          ? s.savedThemes.map(t => t.id === existing.id ? theme : t)
          : [...s.savedThemes, theme],
        activeThemeId: theme.id,
      }
    }),

    deleteTheme: (id) => set((s: UiSlice) => ({
      savedThemes: s.savedThemes.filter(t => t.id !== id),
      activeThemeId: s.activeThemeId === id ? null : s.activeThemeId,
    })),

    setActiveTheme: (id) => set((s: UiSlice) => ({
      activeThemeId: id,
      panel: id ? s.panel : { ...s.panel, color: undefined },
    })),

    setSearchHighlight: (val) => set({ searchHighlight: val }),
    setPanelMode: (mode) => set({ panelMode: mode }),
    setActiveModule: (module) => set({ activeModule: module }),
    toggleNavCollapsed: () => set((s: UiSlice) => ({ navCollapsed: !s.navCollapsed })),
    toggleStatusBarCollapsed: () => set((s: UiSlice) => ({ statusBarCollapsed: !s.statusBarCollapsed })),
    setEditingNoteId: (id) => set({ editingNoteId: id }),
    setDefaultTemplateId: (id) => set({ defaultTemplateId: id }),
    setFilterStatus: (s) => set({ filterStatus: s }),
    setFilterPriority: (p) => set({ filterPriority: p }),
    setFilterSubject: (s) => set({ filterSubject: s }),
    toggleFilterPriority: (p) => set((s: UiSlice) => ({
      filterPriority: s.filterPriority.includes(p)
        ? s.filterPriority.filter(x => x !== p)
        : [...s.filterPriority, p]
    })),
    setQimenNavigationTarget: (target) => set({ qimenNavigationTarget: target }),
    setAppPin: (pin) => set({ appPin: pin }),
    lockApp: () => set({ appLocked: true }),
    unlockApp: () => set({ appLocked: false }),

    setUserProfile: (profile) => set((s: UiSlice) => ({
      userProfile: { ...s.userProfile, ...profile }
    })),

    setInfoTopics: (topics) => set({ infoTopics: topics }),
    setInfoSectionKeywords: (kws: Record<string, string[]>) => set({ infoSectionKeywords: kws }),
    setInfoItems: (items) => set({ infoItems: items }),
    setInfoBrief: (brief) => set({ infoBrief: brief }),
    setInfoLoading: (v) => set({ infoLoading: v }),
    setInfoLastFetch: (t) => set({ infoLastFetch: t }),
    setInfoError: (e) => set({ infoError: e }),
    setAutoInfoEnabled: (v) => set({ autoInfoEnabled: v }),
    setAutoInfoInterval: (v) => set({ autoInfoInterval: v }),
    setAutoInfoLastRun: (t) => set({ autoInfoLastRun: t }),
    setProactiveEnabled: (v) => set({ proactiveEnabled: v }),
    setProactiveBriefDate: (d) => set({ proactiveBriefDate: d }),
    setProactiveBrief: (brief) => set({ proactiveBrief: brief }),
    setProactiveBriefLoading: (v) => set({ proactiveBriefLoading: v }),
    setProactiveSuggestions: (suggestions) => set({ proactiveSuggestions: suggestions }),

    // 侧边栏自定义
    setSidebarConfig: (cfg) => set({ sidebarConfig: cfg }),
    toggleSidebarGroup: (groupId) => set((s: UiSlice) => ({
      sidebarConfig: {
        ...s.sidebarConfig,
        collapsed: {
          ...s.sidebarConfig.collapsed,
          [groupId]: !s.sidebarConfig.collapsed[groupId],
        },
      },
    })),
    resetSidebarConfig: () => set({ sidebarConfig: getDefaultSidebarConfig() }),

    setModuleAvailability: (m) => set({ moduleAvailability: m }),
  }
)

// 侧边栏默认配置工厂：每次返回新对象，避免可变默认值污染
// 8 场景分类：首页 / 对话 / 捕获 / 资讯 / 求职 / 成长 / 工具 / 管理
export function getDefaultSidebarConfig(): SidebarConfig {
  const groups: SidebarGroup[] = [
    {
      id: 'home',
      labelKey: 'nav.groupHome',
      icon: 'dashboard',
      items: ['dashboard'],
      defaultCollapsed: false,
    },
    {
      id: 'chat',
      labelKey: 'nav.groupChat',
      icon: 'chat',
      items: ['chat'],
      defaultCollapsed: false,
    },
    {
      id: 'capture',
      labelKey: 'nav.groupCapture',
      icon: 'feather',
      items: ['notes', 'todo', 'inbox', 'calendar'],
      defaultCollapsed: false,
    },
    {
      id: 'news',
      labelKey: 'nav.groupNews',
      icon: 'newspaper',
      items: ['info', 'rss'],
      defaultCollapsed: false,
    },
    {
      id: 'job',
      labelKey: 'nav.groupJob',
      icon: 'briefcase',
      items: ['job-hunting'],
      defaultCollapsed: false,
    },
    {
      id: 'growth',
      labelKey: 'nav.groupGrowth',
      icon: 'brain',
      items: ['learning', 'knowledge-network', 'memory'],
      defaultCollapsed: true,
    },
    {
      id: 'tools',
      labelKey: 'nav.groupTools',
      icon: 'sliders',
      items: ['tools'],
      defaultCollapsed: true,
    },
    {
      id: 'admin',
      labelKey: 'nav.groupAdmin',
      icon: 'settings',
      items: ['agent', 'notification', 'system-monitor', 'app-manager', 'settings'],
      defaultCollapsed: true,
    },
  ]

  const items: SidebarItemConfig[] = [
    // 首页组
    { key: 'dashboard', visible: true, pinned: true, order: 0, groupId: 'home' },
    // 对话组
    { key: 'chat', visible: true, pinned: true, order: 1, groupId: 'chat' },
    // 捕获组
    { key: 'notes', visible: true, pinned: true, order: 2, groupId: 'capture' },
    { key: 'todo', visible: true, pinned: true, order: 3, groupId: 'capture' },
    { key: 'inbox', visible: true, pinned: false, order: 4, groupId: 'capture' },
    { key: 'calendar', visible: true, pinned: false, order: 5, groupId: 'capture' },
    // 资讯组
    { key: 'info', visible: true, pinned: true, order: 6, groupId: 'news' },
    { key: 'rss', visible: true, pinned: false, order: 7, groupId: 'news' },
    // 求职组
    { key: 'job-hunting', visible: true, pinned: true, order: 7, groupId: 'job' },
    // 成长组（默认折叠）
    { key: 'learning', visible: true, pinned: false, order: 8, groupId: 'growth' },
    { key: 'knowledge-network', visible: true, pinned: false, order: 9, groupId: 'growth' },
    // U3 批2 记忆管理面板 2026-09-16
    { key: 'memory', visible: true, pinned: false, order: 10, groupId: 'growth' },
    // 工具组（默认折叠）
    { key: 'tools', visible: true, pinned: false, order: 10, groupId: 'tools' },
    // 管理组（默认折叠）
    { key: 'agent', visible: true, pinned: false, order: 11, groupId: 'admin' },
    { key: 'system-monitor', visible: true, pinned: false, order: 12, groupId: 'admin' },
    { key: 'app-manager', visible: true, pinned: false, order: 13, groupId: 'admin' },
    { key: 'settings', visible: true, pinned: true, order: 14, groupId: 'admin' },
  ]

  const collapsed: Record<string, boolean> = {}
  for (const g of groups) {
    collapsed[g.id] = g.defaultCollapsed
  }

  return { items, groups, collapsed }
}

// Re-export uid for use in saveTheme
import { uid } from '../../utils/helpers'
