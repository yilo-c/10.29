// ── Store ──
export { useStore, setTrashHandler } from './store/useStore'
export {
  useBackendEventsStore,
  installBackendEvents,
  handleTaskResumable,
  handleSyncConflict,
  handleSandboxTimeout,
  handleJobAlert,
  handleProactiveTriggered,
  handleExecStep,
} from './store/backendEventsStore'
export type {
  ResumableTaskInfo,
  SyncConflictInfo,
  SandboxTimeoutInfo,
  JobAlertInfo,
  MorningBriefInfo,
  ExecStepInfo,
} from './store/backendEventsStore'
export {
  useBackgroundJobsStore,
  runTrackedJob,
  nextJobId,
  rehydrateBackgroundJobs,
  selectRunningJobs,
} from './store/backgroundJobsStore'
export type {
  BackgroundJob,
  BackgroundJobType,
  BackgroundJobStatus,
  TrackedJobOptions,
  ActiveTaskInfo,
} from './store/backgroundJobsStore'
export {
  onBackendEvent,
  dispatchBackendEvent,
  countBackendEventSubscribers,
  clearBackendEventHandlers,
} from './store/backendEventBus'
export type { BackendEventHandler } from './store/backendEventBus'
export {
  formatTimeHM,
  formatDateTime,
  formatDateMD,
  formatMessageTimestamp,
  formatRelative,
  formatConversationLabel,
} from './utils/datetime'
export { setNotesLoader } from './store/slices/noteSlice'
export { setPlatformStorage, getPlatformStorage, platformJSONStorage, setPlatformMobile, isPlatformMobile } from './store/storage'
export { errorStore } from './store/errorStore'
export type { ErrorMessage, ErrorLevel, ErrorEvent } from './store/errorStore'
export { getSaveStatus, setSaveStatus, onSaveStatusChange } from './store/saveTracker'
export type { SaveState } from './store/saveTracker'
export { undoManager } from './store/undoManager'
export type { UndoEvent } from './store/undoManager'
export { onContentEdit, flushPendingEdit } from './store/contentEditTracker'
export { getDescendantIds, getDescendantFolderIds, getAncestorFolderIds } from './store/helpers'

// 侧边栏配置类型
export type { ActiveModule, SidebarGroup, SidebarItemConfig, SidebarConfig } from './store/slices/uiSlice'
export { getDefaultSidebarConfig } from './store/slices/uiSlice'

// ── Types ──
export type {
  TodoItem, FloatingNote, Category, CanvasState, AIConfig, AIConfigProfile, ViewMode,
  CustomTheme, Folder, UserProfile, QimenEvent, QimenDomain, QimenDomainKey,
  InfoItem, RawSourceItem, ProactiveSuggestion,
  GraphNode, GraphEdge, KnowledgeGraphData, KnowledgeCard, TopicMOC,
  Conversation, ModelInfo,
  InfoCategory,
} from './types'
export type * from './types/provider'
export { QIMEN_DOMAINS, CATEGORY_META } from './types'

// ── i18n ──
export { useTranslation, getCurrentLocale, getTranslations, locales, tStatic } from './i18n'
export type { SupportedLocale } from './i18n'

// ── RPC ──
export { RpcMethods } from './rpc/methods'
export type { RpcMethod } from './rpc/methods'
export { classifyRpcError, formatRpcError } from './rpc/errors'
export type { RpcResult, RpcError, RpcErrorKind } from './rpc/errors'

// ── Utils ──
export type { AttachmentKind, MarkdownAttachment, AttachmentRef } from './utils/attachments'
export {
  parseSiyiFileUri, guessAttachmentKind,
  extractAttachmentsFromMarkdown, buildAttachmentMarkdown,
  toSiyiFileUri, countAttachmentsByKind, formatAttachmentSummary,
} from './utils/attachments'
export { uid, formatRelativeTime, stripHtml, sanitizeHtml, defaultTodos, defaultCategories, countText, hexToRgb, COLOR_PRESETS } from './utils/helpers'
export { parseWikiLinks, buildFuseIndex, resolveWikiLink, replaceWikiLinks, updateWikiLinksOnRename, buildBacklinkIndex, WIKI_LINK_REGEX, PROTOCOL_LINK_REGEX } from './utils/wikiLinks'
export type { WikiLinkMatch, NoteStub, NoteWithContent } from './utils/wikiLinks'
export { addSnapshot, addAutoSnapshot, getHistory, computeDiff } from './utils/noteHistory'
export type { VersionEntry, DiffLine } from './utils/noteHistory'
export { builtinTemplates, getAllTemplates, getTemplateById, isBuiltin, createCustomTemplate, updateCustomTemplate, deleteCustomTemplate } from './utils/templates'
export type { NoteTemplate } from './utils/templates'
export { applyCustomTheme } from './utils/customTheme'
export { pluginManager } from './utils/pluginSystem'
export type { Plugin } from './utils/pluginSystem'
export { extractContext, highlightInHtml } from './utils/searchContext'
export { enqueueSyncOp, getPendingSyncCount, subscribeSyncQueue, subscribeSyncQueueDrops, type QueuedOp } from './utils/syncQueue'

// ── AI ──
export { getAIProvider, getWsClient, resetWsClient, configureWsAuthToken, streamAI, LocalAIProvider, RuleAIProvider, HybridAIProvider, DEFAULT_LOCAL_MODEL, DEFAULT_LOCAL_API_URL } from './utils/ai'
export type { AIProvider, AIAction } from './utils/ai'
export { AI_ACTION_LABELS, AI_ACTION_ICONS, AI_PROMPTS } from './utils/ai'
export { AI_PATTERN_FAIL_PREFIX } from './utils/ai'
export { WsAIProvider } from './utils/ai/ws-provider'

// ── WebSocket Client ──
export { WsClient } from './ws-client'
export { resolveClientVersion, PACKAGE_CLIENT_VERSION } from './ws-client/client-version'
export type { ConnectionStatus, JsonRpcRequest, JsonRpcResponse, ServerPushEvent, StreamChunkType, TypedStreamChunk } from './ws-client'

// ── Astrology ──
export { getHuangli, getDayCellLunarInfo } from './utils/astrology'
export { calculateBazi, generateDailyAdvice, getWuxingColor, getWuxingLabel } from './utils/astrology'
export type { BaziProfile, GuidanceAdvice } from './utils/astrology'
export { getDailyGuidance, generateSummaryQuote } from './utils/astrology'
export type { DailyGuidance, HuangliData, GanZhiInfo, ShenXiaoInfo, YiJiInfo, ChongShaInfo, FangWeiInfo, XiuInfo, LunarDayInfo } from './utils/astrology'
export { getFengshuiAdvice, getAdditionalTips } from './utils/astrology/fengshui'
export type { FengshuiAdvice } from './utils/astrology/fengshui'
export { fetchWeather, getWeatherEmoji, getCachedWeather } from './utils/astrology/weather'
export type { WeatherData } from './utils/astrology/weather'

// ── Hooks ──
export { useCompass, labelToDegrees, angleDiff } from './hooks/useCompass'
export type { CompassState } from './hooks/useCompass'
export { useModels } from './hooks/useModels'
export type { UseModelsResult } from './hooks/useModels'
export { useProviderManager } from './hooks/useProviderManager'
