/* ── Types ── */

export enum IntentType {
  LOCAL_CRUD = 'LOCAL_CRUD',
  NAVIGATION = 'NAVIGATION',
  INFO_FETCH = 'INFO_FETCH',
  COMPLEX_REASONING = 'COMPLEX_REASONING',
}

export enum AiTarget {
  LOCAL_DATABASE = 'LOCAL_DATABASE',
  NETWORK_API = 'NETWORK_API',
  DESKTOP_LOCAL_AI = 'DESKTOP_LOCAL_AI',
  CLOUD_AI = 'CLOUD_AI',
  QUEUED_PENDING = 'QUEUED_PENDING',
  OFFLINE_CACHE = 'OFFLINE_CACHE',
}

export const PrivacyLevel = {
  LOCAL_ONLY: 'local_only',
  SYNCABLE: 'syncable',
  SHAREABLE: 'shareable',
  MODEL_EGRESS_ALLOWED: 'model_egress_allowed',
} as const

export type PrivacyLevelType = (typeof PrivacyLevel)[keyof typeof PrivacyLevel]

export interface UserAiPreference {
  cloudAiEnabled: boolean
  promptBeforeCloud: boolean
  preferDesktopAi: boolean
}

/* ── Privacy Classifier ── */

const SENSITIVE_PATTERNS = [
  '日记', '私人', '秘密', '隐私', '机密',
  '密码', 'token', '密钥', 'api_key', 'private',
]

export function classifyPrivacy(text: string, defaultPrivacy: PrivacyLevelType = PrivacyLevel.SYNCABLE): PrivacyLevelType {
  const lower = text.toLowerCase()
  const match = SENSITIVE_PATTERNS.some(p => lower.includes(p))
  return match ? PrivacyLevel.LOCAL_ONLY : defaultPrivacy
}

export function isPersonalContent(text: string): boolean {
  const lower = text.toLowerCase()
  return ['我的', '我的笔记', 'my note', 'private'].some(p => lower.includes(p))
}

/* ── Intent Classifier ── */

const LOCAL_CRUD_KEYWORDS = ['创建', '新建', '添加', '删除', '修改', '编辑', '更新', '保存',
  'create', 'new', 'add', 'delete', 'remove', 'edit', 'update', 'save']

const NAVIGATION_KEYWORDS: [string, string][] = [
  ['笔记', '笔记'],
  ['待办', '任务'],
  ['设置', '设置'],
  ['日历', '日历'],
  ['收件箱', '收件箱'],
  ['翻译', '翻译'],
  ['词库', '词库'],
  ['进化', '进化'],
  ['闹钟', '闹钟'],
  ['知识库', '知识库'],
  ['时间线', '时间线'],
  ['同步', '同步'],
  ['导出', '导出'],
  ['备份', '备份'],
]

const INFO_FETCH_KEYWORDS = ['查', '搜索', '找', '查询', '显示', '展示', '列出',
  'search', 'find', 'lookup', 'show', 'list', 'what is', 'what are']

export function classifyIntent(text: string): IntentType {
  const lower = text.toLowerCase()

  // Check LOCAL_CRUD first
  if (LOCAL_CRUD_KEYWORDS.some(k => lower.includes(k))) {
    return IntentType.LOCAL_CRUD
  }

  // Check NAVIGATION
  if (NAVIGATION_KEYWORDS.some(([, kw]) => lower.includes(kw.toLowerCase())) ||
    NAVIGATION_KEYWORDS.some(([kw]) => lower.startsWith(kw))) {
    return IntentType.NAVIGATION
  }

  // Check INFO_FETCH
  if (INFO_FETCH_KEYWORDS.some(k => lower.includes(k))) {
    return IntentType.INFO_FETCH
  }

  return IntentType.COMPLEX_REASONING
}

/* ── Navigation mapping ── */

export function localNavScreen(text: string): string | null {
  const lower = text.toLowerCase()
  for (const [screen, kw] of NAVIGATION_KEYWORDS) {
    if (lower.includes(kw.toLowerCase()) || lower.startsWith(screen)) {
      return screen
    }
  }
  return null
}

/* ── Target Resolution ── */

export function resolveTarget(
  intent: IntentType,
  privacyLevel: PrivacyLevelType,
  isDesktopConnected: boolean,
  userPref: UserAiPreference,
): AiTarget {
  switch (intent) {
    case IntentType.LOCAL_CRUD:
      return AiTarget.LOCAL_DATABASE

    case IntentType.NAVIGATION:
      return resolveNavTarget(privacyLevel, isDesktopConnected, userPref)

    case IntentType.INFO_FETCH:
      return AiTarget.NETWORK_API

    case IntentType.COMPLEX_REASONING:
      return resolveComplexTarget(privacyLevel, isDesktopConnected, userPref)
  }
}

function resolveNavTarget(
  _privacyLevel: PrivacyLevelType,
  isDesktopConnected: boolean,
  userPref: UserAiPreference,
): AiTarget {
  if (isDesktopConnected && userPref.preferDesktopAi) {
    return AiTarget.DESKTOP_LOCAL_AI
  }
  if (userPref.cloudAiEnabled && _privacyLevel !== PrivacyLevel.LOCAL_ONLY) {
    return AiTarget.CLOUD_AI
  }
  return AiTarget.QUEUED_PENDING
}

function resolveComplexTarget(
  privacyLevel: PrivacyLevelType,
  isDesktopConnected: boolean,
  userPref: UserAiPreference,
): AiTarget {
  /*
   * 隐私语义修正（2026-10）：
   * 原实现把 SYNCABLE 和 LOCAL_ONLY 一起判为「私密」，导致桌面未连接时
   * **所有普通问题都排队**，云 AI 永远不可达 —— 这是 assistantRuntime 当初
   * 被弃用、真实链路另写一套的直接原因。
   *
   * 正确语义：
   *  - LOCAL_ONLY   → 绝不外发，只走桌面（本地模型）
   *  - SYNCABLE     → 可在自己设备间同步，允许发给自己的桌面端；桌面不可达时
   *                   按用户设置决定是否上云（默认不打扰：排队）
   *  - SHAREABLE    → 可外发，允许上云（受 promptBeforeCloud 控制）
   *  - MODEL_EGRESS_ALLOWED → 明确允许发给模型，直接上云
   */
  if (privacyLevel === PrivacyLevel.LOCAL_ONLY) {
    return isDesktopConnected ? AiTarget.DESKTOP_LOCAL_AI : AiTarget.QUEUED_PENDING
  }

  if (privacyLevel === PrivacyLevel.SYNCABLE) {
    // 自己的数据：优先自己的桌面端；桌面不在且用户开了云，才上云
    if (isDesktopConnected && userPref.preferDesktopAi) {
      return AiTarget.DESKTOP_LOCAL_AI
    }
    if (isDesktopConnected) {
      return AiTarget.DESKTOP_LOCAL_AI
    }
    // 桌面不可达：用户显式允许云 → 上云；否则排队等桌面
    if (userPref.cloudAiEnabled && !userPref.promptBeforeCloud) {
      return AiTarget.CLOUD_AI
    }
    return AiTarget.QUEUED_PENDING
  }

  if (privacyLevel === PrivacyLevel.MODEL_EGRESS_ALLOWED && userPref.cloudAiEnabled) {
    return AiTarget.CLOUD_AI
  }

  if (privacyLevel === PrivacyLevel.SHAREABLE) {
    if (userPref.promptBeforeCloud) {
      return AiTarget.QUEUED_PENDING
    }
    if (userPref.cloudAiEnabled) {
      return AiTarget.CLOUD_AI
    }
  }

  return AiTarget.QUEUED_PENDING
}
